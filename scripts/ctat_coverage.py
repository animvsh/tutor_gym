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

PER_PROBLEM_TIMEOUT = 30.0  # seconds
MAX_STEPS = 500             # safety cap in case is_done never fires
PARALLELISM = max(2, (os.cpu_count() or 2) - 1)


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

    buf = io.StringIO()
    tutor = None
    state = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            # Ensure the rendered JSON cache exists before the wrapper looks for it.
            prerender_html_to_json(Path(html_path))

            from tutorgym.env_classes.CTAT.CTAT_tutor import CTAT_Tutor

            result["phase"] = "load"
            tutor = CTAT_Tutor()
            tutor.set_problem(html_path=html_path, model_path=model_path)

            result["phase"] = "step"
            state = tutor.get_state()
            for i in range(MAX_STEPS):
                if state.get_annotation("is_done", False):
                    result["status"] = "PASS"
                    result["steps"] = i
                    return result
                demos = tutor.get_all_demos()
                if not demos:
                    result["status"] = "INCOMPLETE"
                    result["error_msg"] = "__INCOMPLETE__ no demos available"
                    result["steps"] = i
                    result["diagnostic"] = _stuck_diagnostic(tutor, state, model_path)
                    return result
                state = tutor.apply(demos[0])
            result["status"] = "INCOMPLETE"
            result["error_msg"] = f"__INCOMPLETE__ exceeded MAX_STEPS ({MAX_STEPS})"
            result["steps"] = MAX_STEPS
            result["diagnostic"] = _stuck_diagnostic(tutor, state, model_path)
            return result
    except Exception as exc:
        result["status"] = "LOAD_FAIL" if result["phase"] == "load" else "STEP_FAIL"
        result["error_class"] = type(exc).__name__
        tb = traceback.format_exc(limit=5)
        result["error_msg"] = f"{type(exc).__name__}: {exc}\n{tb}"
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

def main() -> int:
    print("Enumerating problems...")
    problems = enumerate_problems()
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

    write_outputs(results)
    print_summary(results)
    return 0


def write_outputs(results: list[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DOCS_DIR / "ctat_coverage.json"
    with open(out_json, "w") as fh:
        json.dump(results, fh, indent=2, default=str)

    # Markdown report
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
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

    lines: list[str] = []
    lines.append("# CTAT Coverage Report\n")
    lines.append("Baseline measurement of how many CTAT Mathtutor problems load and complete "
                 "through the TutorGym `CTAT_Tutor` wrapper when stepped by the oracle agent.\n")
    lines.append("## Summary\n")
    lines.append(f"- Total problems: **{total}**")
    lines.append(f"- PASS (load + complete): **{passed}** ({pct(passed, total)})")
    lines.append(f"- LOAD_FAIL: **{load_fail}** ({pct(load_fail, total)})")
    lines.append(f"- STEP_FAIL: **{step_fail}** ({pct(step_fail, total)})")
    lines.append(f"- INCOMPLETE: **{incomplete}** ({pct(incomplete, total)})\n")

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

    lines.append("## Methodology\n")
    lines.append(
        "- Every CTAT problem is enumerated from each unit's `package.xml` under "
        "`tutorgym/envs/CTAT/Mathtutor/`.\n"
        "- The wrapper's browser-based HTML→JSON preprocessor is bypassed with an "
        "lxml/BeautifulSoup pre-render that writes the same cache file "
        "(`.<name>.tgym_<hash14>.json`) the wrapper looks for, plus a 1×1 PNG "
        "placeholder for the image cache check. No browser needed.\n"
        "- Each problem is loaded through `CTAT_Tutor.set_problem(...)` and stepped by "
        f"repeatedly applying `tutor.get_all_demos()[0]` until `is_done` fires, "
        f"capped at {MAX_STEPS} steps and {PER_PROBLEM_TIMEOUT:g}s wall time, in an "
        f"isolated subprocess (so a hang doesn't kill the sweep).\n"
        "- Failures are classified from the raw error and, for INCOMPLETE runs, from "
        "a structured diagnostic that records the stuck node id, whether it's in a "
        "group, the matcher classes of its outgoing edges, actor, and whether any "
        "candidate input still contains a `%(...)%` MassProduction template.\n"
    )

    lines.append("## Per-unit table\n")
    lines.append("| Unit | Total | PASS | LOAD_FAIL | STEP_FAIL | INCOMPLETE |")
    lines.append("|------|------:|-----:|----------:|----------:|-----------:|")
    for unit in sorted(unit_stats):
        s = unit_stats[unit]
        lines.append(f"| {unit} | {s['total']} | {s['PASS']} | {s['LOAD_FAIL']} | "
                     f"{s['STEP_FAIL']} | {s['INCOMPLETE']} |")
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
    load_fail = sum(1 for r in results if r["status"] == "LOAD_FAIL")
    step_fail = sum(1 for r in results if r["status"] == "STEP_FAIL")
    incomplete = sum(1 for r in results if r["status"] == "INCOMPLETE")
    print()
    print("=" * 70)
    print(f"TOTAL: {total}  PASS: {passed}  LOAD_FAIL: {load_fail}  "
          f"STEP_FAIL: {step_fail}  INCOMPLETE: {incomplete}")
    cat_counter: Counter[str] = Counter(r["category"] for r in results if r["status"] != "PASS")
    for cat, cnt in cat_counter.most_common(10):
        print(f"  {cnt:4d}  {cat}")
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main())
