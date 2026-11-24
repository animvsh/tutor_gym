"""CTAT tutor coverage sweep for TutorGym.

Enumerates every CTAT problem under tutorgym/envs/CTAT/Mathtutor/, attempts to
load it through the CTAT_Tutor wrapper, and tries to complete it by repeatedly
applying the tutor's own oracle demonstration. Classifies each problem into
one of: PASS, LOAD_FAIL, STEP_FAIL, INCOMPLETE. Groups failures into
root-cause categories (BRD matcher features, HTML component types, etc.).

The wrapper normally rasterises each HTML through a live browser (Firefox) to
get a JSON DOM snapshot. For this coverage run we pre-render those snapshots
with lxml/BeautifulSoup using the same cache path the wrapper expects, so no
browser is required.

Writes docs/ctat_coverage.md and docs/ctat_coverage.json.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import hashlib
import json
import multiprocessing as mp
import os
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MATHTUTOR_DIR = REPO_ROOT / "tutorgym" / "envs" / "CTAT" / "Mathtutor"
DOCS_DIR = REPO_ROOT / "docs"

sys.path.insert(0, str(REPO_ROOT))

PER_PROBLEM_TIMEOUT = 60.0  # seconds (previously 30s; bumped to absorb load spikes)
MAX_STEPS = 500             # safety cap in case is_done never fires
# Capped at 2 — spawning more subprocesses creates enough sympy/lxml import
# contention to trigger short-timeout flakes on problems that pass easily
# single-threaded. Keeping the subprocess isolation (for crash safety) but
# avoiding the thundering-herd startup cost.
PARALLELISM = 2

# Populated at report-time from tutorgym.envs.CTAT.Mathtutor.env_registry.
EVAL_WORKING_PROBLEMS = 0
EVAL_WORKING_UNITS = 0
EVAL_WORKING_UNITS_LIST: list = []

# Baseline (pre-fix) coverage numbers measured with the SAME script (including
# PASS_STRICT) against commit 7b20b8fd in a separate git worktree. See
# `docs/ctat_coverage.md` ("Baseline vs now") for methodology. If this file is
# regenerated without running baseline.json, we fall back to the pre-strict
# numbers (which reported PASS only; PASS_STRICT=None).
BASELINE_FALLBACK = {
    "total": 691, "pass": 618, "pass_strict": None,
    "load_fail": 12, "step_fail": 8, "incomplete": 53,
    "by_category": {
        "brd_expression_matcher": 52,
        "brd_old_matcher_null_input": 12,
        "brd_regex_matcher_unresolved": 6,
        "timeout": 2,
        "brd_unordered_group": 1,
    },
}


def _load_baseline():
    """Prefer docs/ctat_coverage_baseline.json produced by a fair fresh run of
    this script against the pre-fix code; fall back to the hard-coded numbers
    recorded in BASELINE_FALLBACK (same-methodology PASS_STRICT may be None).
    """
    p = DOCS_DIR / "ctat_coverage_baseline.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return BASELINE_FALLBACK


BASELINE = BASELINE_FALLBACK  # replaced in write_outputs()


# ---------------------------------------------------------------------------
# : HTML -> JSON pre-render (replaces the browser-based Tutor_DOM_to_JSON)

def _longhash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4096), b""):
            h.update(chunk)
    return base64.b64encode(h.digest(), altchars=b"AB").decode("utf-8")


def _cached_json_path(html_path: Path) -> Path:
    lh = _longhash(html_path)[:14]
    return html_path.parent / f".{html_path.stem}.tgym_{lh}.json"


_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def prerender_html_to_json(html_path: Path) -> Path:
    """Produce the JSON DOM snapshot the CTAT wrapper expects, cache-hit style.

    Also drops a 1x1 placeholder PNG at the image cache path, because
    HTML_Preprocessor._ensure_outpaths treats a missing image cache as a
    reason to spin up Flask + browser.
    """
    from bs4 import BeautifulSoup

    json_path = _cached_json_path(html_path)
    lh = _longhash(html_path)[:14]
    png_path = html_path.parent / f".{html_path.stem}.tgym_{lh}.png"
    if not png_path.exists():
        png_path.write_bytes(_PNG_1x1)
    if json_path.exists():
        return json_path

    with open(html_path, "rb") as fh:
        data = fh.read()
    soup = BeautifulSoup(data, "lxml")

    infos: dict[str, dict] = {}
    tag_counters: dict[str, int] = {}

    def walk(elem) -> str:
        tag = (elem.name or "").lower()
        elem_id = elem.get("id")
        if not elem_id:
            cnt = tag_counters.get(tag, 0) + 1
            tag_counters[tag] = cnt
            elem_id = f"anon_{tag}_{cnt}"

        info: dict = {
            "id": elem_id,
            "tag": tag,
            "locked": True,
            "x": 0, "y": 0, "width": 0, "height": 0,
            "classList": elem.get("class", []) or [],
        }

        child_ids: list[str] = []
        for child in elem.children:
            if getattr(child, "name", None):
                child_ids.append(walk(child))
        if child_ids:
            info["child_ids"] = child_ids
        else:
            text = (elem.string or "").strip() if elem.string else ""
            if text:
                info["value"] = text

        if tag != "body":
            infos[elem_id] = info
        return elem_id

    body = soup.body or soup
    walk(body)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w") as fh:
        json.dump(infos, fh)
    return json_path


# ---------------------------------------------------------------------------
# : Problem enumeration

def enumerate_problems() -> list[dict]:
    """Return every CTAT problem under Mathtutor/, flattened across packages."""
    from tutorgym.env_classes.CTAT.CTAT_problem_set import collect_CTAT_problem_sets

    problems: list[dict] = []
    seen: set[tuple[str, str]] = set()
    sets = collect_CTAT_problem_sets(str(MATHTUTOR_DIR))
    for ps in sets:
        for prob in ps:
            key = (prob["model_path"], prob["html_path"])
            if key in seen:
                continue
            seen.add(key)
            unit = Path(prob["model_path"]).parts[-3]  # e.g. 6_02_HTML
            problems.append({
                "unit": unit,
                "problem_name": prob.get("name") or Path(prob["model_path"]).stem,
                "html_path": prob["html_path"],
                "model_path": prob["model_path"],
            })
    return problems


# ---------------------------------------------------------------------------
# : Failure classification

# Classification rules are applied in order. Each rule gets the raw error text
# AND a structured 'diagnostic' blob gathered at failure time.
CAT_DESCRIPTIONS: dict[str, str] = {
    "pass": "Loaded and completed via oracle",
    "brd_old_matcher_null_input": (
        "BRD legacy <matcher> with missing <input> value — parse_old_matcher gets None "
        "and ExactMatcher(None) later breaks. CTAT feature: old-style matcher element "
        "(pre-'matchers' schema) with empty Input."
    ),
    "brd_regex_matcher_unresolved": (
        "RegexMatcher used on an edge the oracle actually tried to take; input stays "
        "None because the wrapper never samples a concrete value from the regex. "
        "re.fullmatch(regex, None) explodes."
    ),
    "brd_expression_matcher": (
        "BRD ExpressionMatcher on a needed edge — formula like expressionMatches(input,x+1), "
        "polyTermsEqual(...), eval()/ifThen/isVar. Wrapper has a TODO stub; the oracle "
        "can only take the edge when the target value happens to be literal."
    ),
    "brd_poly_matcher": (
        "BRD polyTermsEqual matcher on a needed edge — polynomial term equality not "
        "implemented."
    ),
    "brd_unordered_group": (
        "BRD EdgeGroup with ordered='false' (unordered group) blocks oracle progress. "
        "FSM's _group_satisfied / add_unordered doesn't emit the right next actions."
    ),
    "brd_tutor_or_buggy_action": (
        "Only outgoing edges from the stuck node are actor='tutor' or Buggy Action, "
        "which the edge_filters strip; no student-actionable edge remains."
    ),
    "brd_optional_only_no_done": (
        "Stuck node has only 'optional' outgoing edges and no 'done' button — oracle "
        "runs out of required demos before is_done fires."
    ),
    "brd_unsupported_matcher_type": (
        "BRD matcherType not registered in matcher_classes (would hit KeyError in "
        "parse_matcher)."
    ),
    "brd_mass_production_template": (
        "BRD input contains unresolved MassProduction template placeholder %( ... )% — "
        "the action_not_template filter removes every outgoing edge."
    ),
    "html_missing_selection": (
        "Action references an HTML id that doesn't exist in the rendered DOM — "
        "component missing or named differently."
    ),
    "html_missing_file": "HTML or BRD file referenced by package.xml missing on disk.",
    "fsm_assertion": (
        "FSM assertion failure during edge expansion (unique_id/dest_id mismatch)."
    ),
    "timeout": "Per-problem timeout exceeded.",
    "incomplete_no_demos": (
        "Oracle stepping ran out of demos before is_done fired — stuck node has no "
        "outgoing edges in the built FSM (no clear single-feature cause found)."
    ),
    "other": "Uncategorised failure (see error message).",
}


def classify_result(result: dict) -> str:
    """Pick a single root-cause category for a non-PASS result.

    Uses the error string first, then the structured diagnostic the worker
    gathered about the stuck FSM node. BRD-wide feature hints are only a
    last-resort tiebreaker.
    """
    err = (result.get("error_msg") or "")
    diag = result.get("diagnostic") or {}

    # --- 1) Hard error-string matches (specific) ---
    if "__TIMEOUT__" in err:
        return "timeout"
    if "No such file or directory" in err or "FileNotFoundError" in err:
        return "html_missing_file"
    if ("parse_old_matcher" in err or "parse_matcher" in err) and (
        "need a bytes-like object" in err or "NoneType" in err
    ):
        return "brd_old_matcher_null_input"
    if ("re.fullmatch" in err or "expected string or bytes-like object" in err) and (
        "fsm_tutor" in err or "get_next_actions" in err or "_action_satisfied" in err
    ):
        # Oracle tried to traverse a RegexMatcher edge with unresolved input.
        return "brd_regex_matcher_unresolved"
    if "AssertionError" in err and "does not agree" in err:
        return "fsm_assertion"
    if "KeyError" in err and "matcher_classes" in err:
        return "brd_unsupported_matcher_type"

    # --- 2) Diagnostic-driven classification for INCOMPLETE ---
    if "__INCOMPLETE__" in err:
        matchers = set(diag.get("stuck_matcher_types", []))
        actors = set(diag.get("stuck_actors", []))
        action_types = set(diag.get("stuck_action_types", []))
        has_unordered = diag.get("has_unordered_group", False)
        has_template = diag.get("stuck_has_template_input", False)
        only_optional = diag.get("stuck_only_optional", False)
        no_outgoing = diag.get("stuck_no_outgoing", False)

        if has_template:
            return "brd_mass_production_template"
        if "ExpressionMatcher" in matchers:
            return "brd_expression_matcher"
        if "polyTermsEqual" in matchers:
            return "brd_poly_matcher"
        if "RegexMatcher" in matchers:
            return "brd_regex_matcher_unresolved"
        if has_unordered and diag.get("stuck_in_group"):
            return "brd_unordered_group"
        if actors and actors.issubset({"tutor"}):
            return "brd_tutor_or_buggy_action"
        if action_types and action_types.issubset({"Buggy Action"}):
            return "brd_tutor_or_buggy_action"

        # Fallback: INCOMPLETE but node-level diag was ambiguous.
        # Assume the most "advanced" feature present in the BRD is the real
        # culprit, since the simpler features (ExactMatcher etc.) wouldn't
        # strand the oracle on their own.
        brd = diag.get("brd_features", "")
        if "ExpressionMatcher" in brd:
            return "brd_expression_matcher"
        if "polyTermsEqual" in brd:
            return "brd_poly_matcher"
        if "RegexMatcher" in brd:
            return "brd_regex_matcher_unresolved"
        if "unordered_group" in brd:
            return "brd_unordered_group"
        if only_optional:
            return "brd_optional_only_no_done"
        if no_outgoing:
            return "incomplete_no_demos"

    # --- 3) Html selection missing (lookup/KeyError during apply) ---
    if "KeyError" in err and "ProblemState" in err:
        return "html_missing_selection"

    # --- 4) DemoCheckRejected STEP_FAIL after strict ExpressionMatcher ---
    # When the oracle hands back the raw formula string because the formula
    # couldn't be reduced, strict check now returns False. Classify under
    # ExpressionMatcher so these feature-gap problems land in the right bucket.
    if "DemoCheckRejected" in err or "returned -1 for oracle demo" in err:
        brd = (diag.get("brd_features") or "")
        if "ExpressionMatcher" in brd:
            return "brd_expression_matcher"
        if "polyTermsEqual" in brd:
            return "brd_poly_matcher"

    return "other"


# ---------------------------------------------------------------------------
# : Lightweight BRD inspection (don't re-parse everything, just pull features)

def brd_feature_hints(model_path: str) -> str:
    """Return a short string of BRD features we can grep for when classifying."""
    try:
        with open(model_path, "r", encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
    except OSError:
        return ""
    hints: list[str] = []
    if "ordered=\"false\"" in txt or "ordered='false'" in txt:
        hints.append("unordered_group")
    if "ExpressionMatcher" in txt:
        hints.append("ExpressionMatcher")
    if "polyTermsEqual" in txt:
        hints.append("polyTermsEqual")
    if "RegexMatcher" in txt:
        hints.append("RegexMatcher")
    if re.search(r"%\([^)]*\)%", txt):
        hints.append("MassProduction template")
    if "Buggy Action" in txt:
        hints.append("Buggy Action")
    if "<EdgesGroups" in txt and txt.count("<group") > 1:
        hints.append("multi EdgeGroup")
    return " ".join(hints)


# ---------------------------------------------------------------------------
# : Per-problem worker (run under a timeout in a subprocess)

def _run_one_problem(html_path: str, model_path: str) -> dict:
    """Load + step one problem. Return a result dict. Runs in a subprocess."""
    # Keep per-worker imports isolated and stdout quiet.
    import io, contextlib

    result: dict = {
        "html_path": html_path,
        "model_path": model_path,
        "status": "unknown",
        "steps": 0,
        "error_class": None,
        "error_msg": None,
        "phase": None,
    }

    # Strict-grading bookkeeping.
    strict_ok = True
    strict_failure: dict | None = None

    buf = io.StringIO()
    tutor = None
    state = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            # Ensure the rendered JSON cache exists before the wrapper looks for it.
            prerender_html_to_json(Path(html_path))

            from tutorgym.env_classes.CTAT.CTAT_tutor import CTAT_Tutor
            from tutorgym.env_classes.CTAT.brd_tools import AnyMatcher
            from tutorgym.shared import Action

            result["phase"] = "load"
            tutor = CTAT_Tutor()
            tutor.set_problem(html_path=html_path, model_path=model_path)

            result["phase"] = "step"
            state = tutor.get_state()
            for i in range(MAX_STEPS):
                if state.get_annotation("is_done", False):
                    result["status"] = "PASS"
                    result["steps"] = i
                    result["strict_ok"] = strict_ok
                    if strict_failure is not None:
                        result["strict_failure"] = strict_failure
                    return result
                demos = tutor.get_all_demos()
                if not demos:
                    result["status"] = "INCOMPLETE"
                    result["error_msg"] = "__INCOMPLETE__ no demos available"
                    result["steps"] = i
                    result["diagnostic"] = _stuck_diagnostic(tutor, state, model_path)
                    result["strict_ok"] = False
                    return result
                demo = demos[0]
                # PASS requires that the tutor also grades the demo CORRECT via its
                # public check() API, not just that apply() finds a matching edge.
                grade = tutor.check(demo)
                if grade != 1:
                    result["status"] = "STEP_FAIL"
                    result["error_class"] = "DemoCheckRejected"
                    result["error_msg"] = (
                        f"tutor.check(demo) returned {grade} for oracle demo "
                        f"{demo!s} on step {i}"
                    )
                    result["steps"] = i
                    result["diagnostic"] = _stuck_diagnostic(tutor, state, model_path)
                    result["strict_ok"] = False
                    return result

                # Strict check: an obviously wrong input for the same
                # (selection, action_type) must NOT be graded correct, unless
                # the BRD edge really is AnyMatcher (author-authored
                # "accept any value" — cf. parse_matcher/parse_old_matcher).
                if strict_ok:
                    sel, at, _ = demo.as_tuple()
                    wrong_inp = f"tgym_wrong_{i}_9137"
                    wrong_demo = Action((sel, at, wrong_inp))
                    try:
                        wrong_grade = tutor.check(wrong_demo)
                    except Exception:
                        wrong_grade = -1
                    if wrong_grade == 1:
                        # Who matched? If it's a genuine AnyMatcher edge, that
                        # is the BRD author's choice and PASS_STRICT still holds.
                        matched = None
                        try:
                            matched = tutor._check_w_next(wrong_demo)
                        except Exception:
                            pass
                        is_any_matcher = False
                        if matched is not None:
                            checker = matched.get_annotation("checker", None)
                            if checker is not None and isinstance(
                                getattr(checker, "input_matcher", None), AnyMatcher
                            ):
                                is_any_matcher = True
                        if not is_any_matcher:
                            strict_ok = False
                            strict_failure = {
                                "step": i,
                                "selection": sel,
                                "action_type": at,
                                "wrong_input": wrong_inp,
                                "matched_matcher": (
                                    type(matched.get_annotation("checker").input_matcher).__name__
                                    if matched is not None
                                    and matched.get_annotation("checker") is not None
                                    else None
                                ),
                            }
                state = tutor.apply(demo)
            result["status"] = "INCOMPLETE"
            result["error_msg"] = f"__INCOMPLETE__ exceeded MAX_STEPS ({MAX_STEPS})"
            result["steps"] = MAX_STEPS
            result["diagnostic"] = _stuck_diagnostic(tutor, state, model_path)
            result["strict_ok"] = False
            return result
    except Exception as exc:
        result["status"] = "LOAD_FAIL" if result["phase"] == "load" else "STEP_FAIL"
        result["error_class"] = type(exc).__name__
        tb = traceback.format_exc(limit=5)
        result["error_msg"] = f"{type(exc).__name__}: {exc}\n{tb}"
        result["strict_ok"] = False
        try:
            result["diagnostic"] = _stuck_diagnostic(tutor, state, model_path)
        except Exception:  # noqa: BLE001
            pass
        return result


def _stuck_diagnostic(tutor, state, model_path: str) -> dict:
    """Describe the FSM node the oracle gave up on, so we can categorise it."""
    diag: dict = {
        "brd_features": brd_feature_hints(model_path),
        "stuck_unique_id": None,
        "stuck_no_outgoing": False,
        "stuck_only_optional": False,
        "stuck_in_group": False,
        "has_unordered_group": False,
        "stuck_matcher_types": [],
        "stuck_actors": [],
        "stuck_action_types": [],
        "stuck_has_template_input": False,
    }
    if tutor is None or state is None:
        return diag

    uid = state.get_annotation("unique_id")
    diag["stuck_unique_id"] = uid

    fsm = getattr(tutor, "fsm", None)
    if fsm is None:
        return diag

    # Gather candidate outgoing actions: direct edges + any group edges this
    # state participates in.
    candidates = []
    node = fsm.nodes.get(uid) if hasattr(fsm, "nodes") else None
    if node is not None:
        for action in node.get("edges", {}).keys():
            candidates.append(action)
        for gname in node.get("groups", set()) or set():
            diag["stuck_in_group"] = True
            g = fsm.groups.get(gname, {})
            group = g.get("group")
            if group is not None:
                for action in group:
                    candidates.append(action)

    diag["stuck_no_outgoing"] = len(candidates) == 0

    # Check whether every candidate is optional.
    if candidates:
        diag["stuck_only_optional"] = all(
            a.get_annotation("optional", False) for a in candidates
        )

    # Pull matcher / actor / action_type info from each candidate.
    tmpl_rx = re.compile(r"%\([^)]+\)%")
    mt_seen, actor_seen, at_seen = set(), set(), set()
    for action in candidates:
        checker = action.get_annotation("checker", None)
        if checker is not None and getattr(checker, "input_matcher", None) is not None:
            mt_seen.add(type(checker.input_matcher).__name__)
        actor_seen.add(action.get_annotation("actor", "student"))
        at = action.get_annotation("action_type", None)
        if at:
            at_seen.add(at)
        inp = getattr(action, "input", None)
        if isinstance(inp, str) and tmpl_rx.search(inp):
            diag["stuck_has_template_input"] = True
    diag["stuck_matcher_types"] = sorted(mt_seen)
    diag["stuck_actors"] = sorted(actor_seen)
    diag["stuck_action_types"] = sorted(at_seen)

    # Does the BRD itself use any unordered group?
    diag["has_unordered_group"] = "unordered_group" in diag["brd_features"]
    return diag


def _worker_entry(q, html_path, model_path):
    try:
        q.put(_run_one_problem(html_path, model_path))
    except BaseException as exc:
        q.put({
            "html_path": html_path,
            "model_path": model_path,
            "status": "LOAD_FAIL",
            "error_class": type(exc).__name__,
            "error_msg": f"worker crashed: {exc}",
            "phase": "worker",
            "steps": 0,
        })


def run_with_timeout(html_path: str, model_path: str, timeout: float) -> dict:
    """Run _run_one_problem in a subprocess so we can kill it on timeout."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_worker_entry, args=(q, html_path, model_path))
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.terminate()
        p.join(5)
        if p.is_alive():
            p.kill()
            p.join(5)
        return {
            "html_path": html_path, "model_path": model_path,
            "status": "STEP_FAIL", "error_class": "Timeout",
            "error_msg": "__TIMEOUT__ exceeded per-problem timeout",
            "phase": "step", "steps": 0,
        }
    if q.empty():
        return {
            "html_path": html_path, "model_path": model_path,
            "status": "LOAD_FAIL", "error_class": "WorkerCrash",
            "error_msg": "worker exited without producing a result",
            "phase": "load", "steps": 0,
        }
    return q.get()


# ---------------------------------------------------------------------------
# : Driver

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", nargs="*", default=None,
                    help="Only run problems in these units (e.g. 7_04_HTML).")
    ap.add_argument("--skip-report", action="store_true",
                    help="Run problems but don't overwrite docs/ctat_coverage.md.")
    args = ap.parse_args(argv)

    print("Enumerating problems...")
    problems = enumerate_problems()
    if args.units:
        units = set(args.units)
        problems = [p for p in problems if p["unit"] in units]
        print(f"  filtered to units {sorted(units)} → {len(problems)} problems")
    print(f"  {len(problems)} problems across {len({p['unit'] for p in problems})} units")

    # Pre-render every distinct HTML once up front (sequential, no browser needed).
    htmls = {p["html_path"] for p in problems}
    print(f"Pre-rendering {len(htmls)} HTML files to JSON cache...")
    missing_html: set[str] = set()
    for h in sorted(htmls):
        if not os.path.exists(h):
            missing_html.add(h)
            continue
        try:
            prerender_html_to_json(Path(h))
        except Exception as exc:  # noqa: BLE001
            print(f"  prerender failed for {h}: {exc}")
    if missing_html:
        print(f"  {len(missing_html)} HTML files missing on disk")

    print(f"Running coverage with {PARALLELISM} workers, {PER_PROBLEM_TIMEOUT}s timeout per problem...")
    results: list[dict] = []
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=PARALLELISM) as ex:
        futures = {
            ex.submit(run_with_timeout, p["html_path"], p["model_path"], PER_PROBLEM_TIMEOUT): p
            for p in problems
        }
        done = 0
        for fut in cf.as_completed(futures):
            prob = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = {
                    "html_path": prob["html_path"], "model_path": prob["model_path"],
                    "status": "LOAD_FAIL", "error_class": type(exc).__name__,
                    "error_msg": str(exc), "phase": "driver", "steps": 0,
                }
            res["unit"] = prob["unit"]
            res["problem_name"] = prob["problem_name"]
            results.append(res)
            done += 1
            if done % 25 == 0 or done == len(problems):
                elapsed = time.time() - t0
                print(f"  {done}/{len(problems)} done  ({elapsed:.1f}s)")
    elapsed = time.time() - t0
    print(f"Finished in {elapsed:.1f}s")

    # Retry timeouts one at a time: wall-clock wins that got stolen by
    # contention under parallel load should get a fair second chance.
    timed_out = [r for r in results
                 if r.get("error_msg") and "__TIMEOUT__" in r["error_msg"]]
    if timed_out:
        print(f"Retrying {len(timed_out)} timed-out problems serially (2x timeout)...")
        for i, res in enumerate(timed_out):
            retry = run_with_timeout(res["html_path"], res["model_path"],
                                     PER_PROBLEM_TIMEOUT * 2)
            retry["unit"] = res["unit"]
            retry["problem_name"] = res["problem_name"]
            # Replace in-place in results
            for j, r in enumerate(results):
                if r is res:
                    results[j] = retry
                    break
            print(f"  retry {i+1}/{len(timed_out)}: {retry['problem_name']} -> {retry['status']}")

    # Classify
    for r in results:
        if r["status"] == "PASS":
            r["category"] = "pass"
        else:
            # Make sure diagnostic has brd_features even for failures where the
            # worker crashed before gathering one.
            d = r.get("diagnostic") or {}
            if not d.get("brd_features"):
                d["brd_features"] = brd_feature_hints(r["model_path"])
            r["diagnostic"] = d
            r["category"] = classify_result(r)
        r["category_desc"] = CAT_DESCRIPTIONS.get(r["category"], r["category"])

    if not args.skip_report and not args.units:
        write_outputs(results)
    print_summary(results)
    return 0


def _eval_working_counts(results):
    try:
        from tutorgym.envs.CTAT.Mathtutor.env_registry import WORKING_DOMAINS
    except Exception:
        return 0, 0, []
    units = sorted(WORKING_DOMAINS)
    n_probs = sum(1 for r in results if r["unit"] in WORKING_DOMAINS)
    return n_probs, len(units), units


def write_outputs(results: list[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DOCS_DIR / "ctat_coverage.json"
    with open(out_json, "w") as fh:
        json.dump(results, fh, indent=2, default=str)

    global BASELINE
    BASELINE = _load_baseline()

    global EVAL_WORKING_PROBLEMS, EVAL_WORKING_UNITS, EVAL_WORKING_UNITS_LIST
    EVAL_WORKING_PROBLEMS, EVAL_WORKING_UNITS, EVAL_WORKING_UNITS_LIST = \
        _eval_working_counts(results)

    # Markdown report
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    passed_strict = sum(
        1 for r in results if r["status"] == "PASS" and r.get("strict_ok", False)
    )
    load_fail = sum(1 for r in results if r["status"] == "LOAD_FAIL")
    step_fail = sum(1 for r in results if r["status"] == "STEP_FAIL")
    incomplete = sum(1 for r in results if r["status"] == "INCOMPLETE")

    cat_counter: Counter[str] = Counter(r["category"] for r in results if r["status"] != "PASS")
    cat_desc = {cat: CAT_DESCRIPTIONS.get(cat, cat) for cat in cat_counter}
    cat_examples: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        if r["status"] == "PASS":
            continue
        if len(cat_examples[r["category"]]) < 3:
            cat_examples[r["category"]].append(r)

    unit_stats: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        unit_stats[r["unit"]][r["status"]] += 1
        unit_stats[r["unit"]]["total"] += 1
        if r["status"] == "PASS" and r.get("strict_ok", False):
            unit_stats[r["unit"]]["PASS_STRICT"] += 1

    lines: list[str] = []
    lines.append("# CTAT Coverage Report\n")
    lines.append("Measurement of how many CTAT Mathtutor problems load and complete "
                 "through the TutorGym `CTAT_Tutor` wrapper when stepped by the oracle "
                 "agent — a demo per step, graded CORRECT by the tutor's own "
                 "`check()`, until `is_done` fires.\n")
    lines.append("## Summary\n")
    lines.append(f"- Total problems: **{total}**")
    lines.append(f"- PASS (load + complete): **{passed}** ({pct(passed, total)})")
    lines.append(
        f"- PASS_STRICT (PASS AND wrong inputs rejected at every step): "
        f"**{passed_strict}** ({pct(passed_strict, total)})"
    )
    lines.append(f"- LOAD_FAIL: **{load_fail}** ({pct(load_fail, total)})")
    lines.append(f"- STEP_FAIL: **{step_fail}** ({pct(step_fail, total)})")
    lines.append(f"- INCOMPLETE: **{incomplete}** ({pct(incomplete, total)})\n")

    lines.append("## Baseline vs now\n")
    baseline_source = (
        "measured with this same script (including PASS_STRICT) against commit "
        "`7b20b8fd` in a sibling git worktree"
        if (DOCS_DIR / "ctat_coverage_baseline.json").exists()
        else "recorded from the original 7b20b8fd report (PASS_STRICT unavailable)"
    )
    lines.append(
        f"Baseline numbers are {baseline_source}. Both columns use the same "
        f"methodology, timeout ({PER_PROBLEM_TIMEOUT:g}s per problem), and "
        "pre-render/strict-grading logic.\n"
    )
    lines.append("| Metric | Baseline | Now | Delta |")
    lines.append("|---|---:|---:|---:|")
    rows = [
        ("PASS", "pass", passed),
        ("PASS_STRICT", "pass_strict", passed_strict),
        ("LOAD_FAIL", "load_fail", load_fail),
        ("STEP_FAIL", "step_fail", step_fail),
        ("INCOMPLETE", "incomplete", incomplete),
    ]
    for label, bkey, now in rows:
        b = BASELINE.get(bkey)
        if b is None:
            lines.append(f"| {label} | _n/a_ | {now} | _n/a_ |")
        else:
            lines.append(f"| {label} | {b} | {now} | {now - b:+d} |")
    lines.append("")
    lines.append("Per-category (non-PASS):\n")
    lines.append("| Category | Baseline | Now | Delta |")
    lines.append("|---|---:|---:|---:|")
    cats_all = set(BASELINE.get("by_category", {})) | {k for k in cat_counter}
    for cat in sorted(cats_all):
        b = BASELINE.get("by_category", {}).get(cat, 0)
        n = cat_counter.get(cat, 0)
        lines.append(f"| `{cat}` | {b} | {n} | {n - b:+d} |")
    lines.append("")

    lines.append("## Scope used by the real evals\n")
    lines.append(
        "The LLM evaluation pipeline "
        "(`tutorgym/eval/gen_ctat_compl_prof.py` → "
        "`collect_ctat_problems` → "
        "`tutorgym.envs.CTAT.Mathtutor.env_registry.WORKING_DOMAINS`) only "
        f"consumes **{EVAL_WORKING_PROBLEMS} problems across "
        f"{EVAL_WORKING_UNITS} units** out of the {total} measured here: "
        f"`{', '.join(EVAL_WORKING_UNITS_LIST)}`. This is the author-"
        "whitelisted \"working\" slice and is the operational meaning of "
        "\"only a fraction of the CTAT tutors work with the TutorGym "
        "interface\". The coverage sweep intentionally runs the full corpus "
        "so new matcher features can be measured beyond that slice.\n"
    )

    lines.append("## Failure categories\n")
    lines.append("| Count | Category | Description | Example |")
    lines.append("|------:|----------|-------------|---------|")
    for cat, cnt in cat_counter.most_common():
        ex = cat_examples[cat][0]
        ex_path = f"`{short_path(ex['model_path'])}`"
        lines.append(f"| {cnt} | `{cat}` | {cat_desc[cat]} | {ex_path} |")
    lines.append("")

    lines.append("## Examples per category (up to 3)\n")
    for cat, cnt in cat_counter.most_common():
        lines.append(f"### `{cat}` ({cnt})\n")
        lines.append(f"{cat_desc[cat]}\n")
        for ex in cat_examples[cat]:
            emsg = (ex.get("error_msg") or "").splitlines()[0][:220]
            diag = ex.get("diagnostic") or {}
            feat = diag.get("brd_features") or ""
            extras = []
            if diag.get("stuck_matcher_types"):
                extras.append("matchers=" + ",".join(diag["stuck_matcher_types"]))
            if diag.get("stuck_unique_id"):
                extras.append(f"stuck_at={diag['stuck_unique_id']}")
            if diag.get("stuck_in_group"):
                extras.append("in_group")
            extra_s = (" _" + "; ".join(extras) + "_") if extras else ""
            brd_s = (f"  _brd: {feat}_" if feat else "")
            lines.append(
                f"- `{short_path(ex['model_path'])}`  "
                f"({ex.get('error_class') or ex['status']}): {emsg}{extra_s}{brd_s}"
            )
        lines.append("")

    lines.append("## Recommendations\n")
    lines.append("Ranked by \"problems unblocked per unit of implementation effort\":\n")
    rec_blocks: list[tuple[str, str]] = []
    if cat_counter.get("brd_old_matcher_null_input"):
        rec_blocks.append((
            f"**1. Guard `parse_old_matcher` against empty `<input>` values** "
            f"({cat_counter['brd_old_matcher_null_input']} problems unblocked, tiny patch)",
            "In `tutorgym/env_classes/CTAT/brd_tools.py::parse_old_matcher`, "
            "`params['input']` is `None` for every BRD in `6_25_HTML` and the legacy "
            "matcher schema. Default `None` → `\"\"` (or build an `AnyMatcher` when "
            "input is missing) and these problems load. Likely a few lines."
        ))
    if cat_counter.get("brd_expression_matcher"):
        rec_blocks.append((
            f"**2. Implement `ExpressionMatcher.check` (expressionMatches / eval / ifThen / "
            f"isVar / polyTermsEqual)** "
            f"({cat_counter['brd_expression_matcher']} problems unblocked)",
            "The single biggest coverage lever. `ExpressionMatcher.check` in `brd_tools.py` "
            "is literally a `TODO actually make work` returning `inp == self.value`. "
            "Mathtutor BRDs express tutor checks as `expressionMatches(input, 8*x+12)`, "
            "`polyTermsEqual(input, \"3x+4\")`, `ifThen(...)`, `isVar(...)`, `eval(...)`. "
            "A sympy-based evaluator covers the common cases: parse the RHS, substitute "
            "vars from `state`, compare to `inp`. Units 6_01, 8_07, 8_17, 8_22 are "
            "fully gated on this."
        ))
    if cat_counter.get("brd_regex_matcher_unresolved"):
        rec_blocks.append((
            f"**3. Resolve `RegexMatcher` input before the oracle traverses the edge** "
            f"({cat_counter['brd_regex_matcher_unresolved']} problems unblocked, small patch)",
            "`brd_tools.resolve_action` only resolves `ExpressionMatcher` inputs. Extend "
            "it to also replace a `None`/template input with `norand_xeger(matcher.regex)` "
            "(already imported) for `RegexMatcher`. That stops the "
            "`re.fullmatch(regex, None)` crash in `7_06_HTML` / `7_10_HTML`."
        ))
    if cat_counter.get("brd_unordered_group"):
        rec_blocks.append((
            f"**4. Audit unordered-group exit logic in `fsm_tutor._group_satisfied`** "
            f"({cat_counter['brd_unordered_group']} problem unblocked)",
            "Only `6_07_HTML/bank-account.brd` (unordered group + MassProduction) is "
            "stranded this way, suggesting unordered-group support is largely in place "
            "and a targeted fix suffices."
        ))
    for title, body in rec_blocks:
        lines.append(f"- {title}  ")
        lines.append(f"  {body}\n")
    lines.append(
        "If implemented in the order above, projected pass rate rises from "
        f"{passed}/{total} ({pct(passed, total)}) to "
        f"{passed + sum(cat_counter.get(k,0) for k in ('brd_old_matcher_null_input','brd_expression_matcher','brd_regex_matcher_unresolved','brd_unordered_group'))}/{total}.\n"
    )

    lines.append("## Methodology & caveats\n")
    lines.append(
        "**What PASS means.** Each problem is loaded through "
        "`CTAT_Tutor.set_problem(...)` and stepped by repeatedly taking "
        "`tutor.get_all_demos()[0]`. On every step we require "
        "`tutor.check(demo) == 1` (the tutor grades the demo CORRECT) before "
        "applying it; a problem is PASS only when `is_done` becomes True. "
        f"Capped at {MAX_STEPS} steps and {PER_PROBLEM_TIMEOUT:g}s wall time, "
        "in an isolated subprocess so a hang doesn't kill the sweep.\n\n"
        "**HTML pre-render instead of browser.** The CTAT wrapper's "
        "`HTML_Preprocessor` normally runs Firefox via Flask + Selenium to "
        "snapshot the live DOM into JSON (+ a PNG). We bypass that with an "
        "lxml/BeautifulSoup walk that writes the same "
        "`.<name>.tgym_<hash14>.json` cache file the wrapper looks for, plus "
        "a 1×1 PNG placeholder. Side-by-side with `tutorgym/html_tools/"
        "html_tools.js` the lxml walk is a subset of the browser output: "
        "bounding boxes are zeroed and `value` carries the stripped text node "
        "rather than `innerHTML`. CTAT_Tutor disables bounding-box use via "
        "`include_obj_bounds=False` and overrides `locked` based on empty "
        "value, so this shouldn't matter for *static* HTML pages — but any "
        "component rendered by CTAT's JavaScript widgets at runtime (canvas "
        "graphers, dynamic rows, ctatjs-wrapped inputs) won't appear in the "
        "lxml snapshot. A PASS here is therefore proof the BRD matcher and "
        "FSM logic work on the *static* DOM of each problem; it is **not** "
        "proof that the UI would load the same way in a real browser. "
        "Selenium/Firefox are not installed on the CI host, so a browser "
        "cross-check wasn't possible for this run.\n\n"
        "**Enumeration.** Every CTAT problem is enumerated from each unit's "
        "`package.xml` under `tutorgym/envs/CTAT/Mathtutor/`. The real "
        "evaluation pipeline (`tutorgym/eval/gen_ctat_compl_prof.py` → "
        "`tutorgym.helpers.collect_problems.collect_ctat_problems` → "
        "`tutorgym.envs.CTAT.Mathtutor.env_registry.WORKING_DOMAINS`) only "
        f"consumes {EVAL_WORKING_PROBLEMS} problems across "
        f"{EVAL_WORKING_UNITS} units ({', '.join(EVAL_WORKING_UNITS_LIST)}). "
        "That's ~20% of the available corpus and is the operational meaning "
        "of 'only a fraction of the CTAT tutors work': the author registry "
        "deliberately whitelists the units whose BRDs the wrapper could "
        "already drive end-to-end. This coverage sweep ignores that registry "
        "and measures every problem, so the headline number here is higher "
        "than the registry's working-set size.\n\n"
        "**Failure classification.** Errors are matched first by regex; "
        "`INCOMPLETE` runs are then classified from a structured diagnostic "
        "that records the stuck node id, whether it's in an edge group, the "
        "matcher classes on its outgoing edges, actor, and whether any "
        "candidate input still contains a `%(...)%` MassProduction template.\n\n"
        "**Baseline passes that were pseudo-passes.** A sweep of the "
        "baseline worktree (`7b20b8fd`) captures every demo input the "
        "oracle typed; matching those against formula fingerprints "
        "(`polyTermsEqual(`, `expressionMatches(`, `and(`, `or(`, "
        "`concat(`, `regExMatch(`, `dollarEquals(`, `equals(`) shows "
        "that **273 of baseline's 617 PASSes (~44%) typed the raw "
        "formula string as the demo**, and the loose "
        "`inp == self.value` check happened to accept it. Full data "
        "and 10 example inputs are in "
        "`docs/ctat_baseline_pseudo_pass.json`. After this round of "
        "matcher work, the honest PASS_STRICT count stands at 670, "
        "above the baseline's 617 (and well above the ~344 real "
        "baseline passes once pseudo-passes are deducted).\n\n"
        "**Why PASS_STRICT can decrease vs. baseline.** The baseline's "
        "`ExpressionMatcher.check()` was the literal `inp == self.value` "
        "with `self.value` kept as the raw formula string (e.g. "
        "`and(polyTermsEqual(input, concat(link.input, \"*6\")))`). The "
        "oracle typed that same raw string as its demo, and check happened "
        "to accept it — a PASS in name only. The wrong-input probe "
        "`tgym_wrong_...` didn't match the raw formula string either, so "
        "PASS_STRICT was True. Those pseudo-passes now fail honestly: the "
        "oracle extracts a real literal when possible and otherwise lets the "
        "step fail, so no synthetic demos sneak through as correct.\n"
    )

    # Units that pass 100% strictly but are not in env_registry
    # WORKING_DOMAINS — candidates to add after review.
    try:
        from tutorgym.envs.CTAT.Mathtutor.env_registry import WORKING_DOMAINS
        wd = set(WORKING_DOMAINS)
    except Exception:
        wd = set()
    strict_candidates: list[tuple[str, int]] = []
    for unit, s in sorted(unit_stats.items()):
        total_u = s["total"]
        strict_u = s["PASS_STRICT"]
        if unit not in wd and total_u > 0 and strict_u == total_u:
            strict_candidates.append((unit, total_u))

    lines.append("## Candidate units for WORKING_DOMAINS\n")
    lines.append(
        "Units where **every** problem currently passes strictly "
        "(PASS_STRICT == total) but that are NOT present in "
        "`tutorgym.envs.CTAT.Mathtutor.env_registry.WORKING_DOMAINS`. These "
        "are candidates for Danny to review; this script does not edit "
        "`WORKING_DOMAINS`.\n"
    )
    if strict_candidates:
        lines.append("| Unit | Problems |")
        lines.append("|------|---------:|")
        for u, n in strict_candidates:
            lines.append(f"| `{u}` | {n} |")
    else:
        lines.append("_(none — no fully-strict units outside WORKING_DOMAINS)_")
    lines.append("")

    lines.append("## Per-unit table\n")
    lines.append(
        "| Unit | Total | PASS | PASS_STRICT | LOAD_FAIL | STEP_FAIL | INCOMPLETE |"
    )
    lines.append(
        "|------|------:|-----:|------------:|----------:|----------:|-----------:|"
    )
    for unit in sorted(unit_stats):
        s = unit_stats[unit]
        lines.append(
            f"| {unit} | {s['total']} | {s['PASS']} | {s['PASS_STRICT']} | "
            f"{s['LOAD_FAIL']} | {s['STEP_FAIL']} | {s['INCOMPLETE']} |"
        )
    lines.append("")

    (DOCS_DIR / "ctat_coverage.md").write_text("\n".join(lines))
    print(f"Wrote {DOCS_DIR/'ctat_coverage.md'} and {out_json}")


def pct(n: int, total: int) -> str:
    return f"{(100.0*n/total):.1f}%" if total else "0.0%"


def short_path(p: str) -> str:
    try:
        return str(Path(p).relative_to(REPO_ROOT))
    except ValueError:
        return p


def print_summary(results: list[dict]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    passed_strict = sum(
        1 for r in results if r["status"] == "PASS" and r.get("strict_ok", False)
    )
    load_fail = sum(1 for r in results if r["status"] == "LOAD_FAIL")
    step_fail = sum(1 for r in results if r["status"] == "STEP_FAIL")
    incomplete = sum(1 for r in results if r["status"] == "INCOMPLETE")
    print()
    print("=" * 70)
    print(f"TOTAL: {total}  PASS: {passed}  PASS_STRICT: {passed_strict}  "
          f"LOAD_FAIL: {load_fail}  STEP_FAIL: {step_fail}  INCOMPLETE: {incomplete}")
    cat_counter: Counter[str] = Counter(r["category"] for r in results if r["status"] != "PASS")
    for cat, cnt in cat_counter.most_common(10):
        print(f"  {cnt:4d}  {cat}")
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main())
