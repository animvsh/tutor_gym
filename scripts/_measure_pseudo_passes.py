"""Count baseline pseudo-passes.

A pseudo-pass is a PASS at baseline commit 7b20b8fd where the oracle's demo
input literally equals the raw ExpressionMatcher formula string (or a
substring that looks like a formula). Those passes were artifacts of the
old loose ``check()`` that returned ``inp == self.value`` when
``self.value`` was the raw formula.

Runs against the baseline worktree (expected at /tmp/tutorgym-baseline).
Usage:  python scripts/_measure_pseudo_passes.py
"""
from __future__ import annotations

import io
import contextlib
import json
import os
import re
import sys
import traceback
from pathlib import Path


BASELINE = Path("/tmp/tutorgym-baseline")


def _looks_like_formula(s: str) -> bool:
    if not isinstance(s, str):
        return False
    for name in ("polyTermsEqual(", "expressionMatches(", "and(", "or(",
                 "concat(", "regExMatch(", "dollarEquals(", "equals("):
        if name in s:
            return True
    # bare regex pattern
    if re.search(r"\[\\\\?[+\-*/]+\]", s):
        return True
    return False


def _run_one(html_path: str, model_path: str) -> dict:
    """Minimal oracle loop: records every demo input it typed."""
    from pathlib import Path as _P
    from scripts.ctat_coverage import prerender_html_to_json
    from tutorgym.env_classes.CTAT.CTAT_tutor import CTAT_Tutor

    prerender_html_to_json(_P(html_path))
    buf = io.StringIO()
    typed = []
    status = "unknown"
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            t = CTAT_Tutor()
            t.set_problem(html_path=html_path, model_path=model_path)
            state = t.get_state()
            for _ in range(500):
                if state.get_annotation("is_done", False):
                    status = "PASS"
                    break
                demos = t.get_all_demos()
                if not demos:
                    status = "INCOMPLETE"
                    break
                d = demos[0]
                typed.append(str(d.input))
                try:
                    grade = t.check(d)
                except Exception:
                    grade = -1
                if grade != 1:
                    status = "STEP_FAIL"
                    break
                state = t.apply(d)
            else:
                status = "INCOMPLETE"
        except Exception:
            status = "LOAD_FAIL"
    return {"status": status, "typed": typed}


def _worker_entry(q, html_path, model_path):
    try:
        q.put(_run_one(html_path, model_path))
    except BaseException as exc:
        q.put({"status": "LOAD_FAIL", "typed": [], "err": str(exc)})


def main() -> int:
    if not BASELINE.exists():
        print(f"ERROR: baseline worktree not at {BASELINE}")
        return 1
    # Run inside baseline worktree
    os.chdir(BASELINE)
    sys.path.insert(0, str(BASELINE))
    from scripts.ctat_coverage import enumerate_problems

    import multiprocessing as mp

    problems = enumerate_problems()
    print(f"enumerating {len(problems)} baseline problems")

    pseudo = 0
    pseudo_examples = []
    total_pass = 0

    ctx = mp.get_context("spawn")
    done = 0
    for p in problems:
        q = ctx.Queue()
        proc = ctx.Process(target=_worker_entry, args=(q, p["html_path"], p["model_path"]))
        proc.start()
        proc.join(60)
        if proc.is_alive():
            proc.terminate(); proc.join(5)
            r = {"status": "TIMEOUT", "typed": []}
        else:
            try:
                r = q.get_nowait()
            except Exception:
                r = {"status": "LOAD_FAIL", "typed": []}
        if r["status"] == "PASS":
            total_pass += 1
            for t in r["typed"]:
                if _looks_like_formula(t):
                    pseudo += 1
                    if len(pseudo_examples) < 10:
                        pseudo_examples.append({
                            "unit": p["unit"],
                            "problem": p["problem_name"],
                            "input": t[:160],
                        })
                    break
        done += 1
        if done % 25 == 0 or done == len(problems):
            print(f"  {done}/{len(problems)}  pass={total_pass} pseudo={pseudo}")

    out = {
        "total_pass": total_pass,
        "pseudo_pass": pseudo,
        "examples": pseudo_examples,
    }
    print(json.dumps(out, indent=2))
    out_path = Path("/Users/animesh/tutorgym/docs/ctat_baseline_pseudo_pass.json")
    out_path.write_text(json.dumps(out, indent=2))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
