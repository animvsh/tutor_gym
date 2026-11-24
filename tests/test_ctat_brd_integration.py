"""End-to-end oracle-stepping tests for a real BRD per new feature.

Each test loads a Mathtutor problem that previously failed in the baseline
coverage sweep and asserts that the tutor's own demos now grade CORRECT at
every step and `is_done` fires within MAX_STEPS.
"""
from pathlib import Path

import pytest

from tests.conftest import MATHTUTOR, prerender

MAX_STEPS = 200


def _run_oracle(html_path, model_path):
    prerender(html_path)
    from tutorgym.env_classes.CTAT.CTAT_tutor import CTAT_Tutor

    tutor = CTAT_Tutor()
    tutor.set_problem(html_path=str(html_path), model_path=str(model_path))
    state = tutor.get_state()
    for step in range(MAX_STEPS):
        if state.get_annotation("is_done", False):
            return step
        demos = tutor.get_all_demos()
        assert demos, f"ran out of demos at step {step}"
        demo = demos[0]
        assert tutor.check(demo) == 1, (
            f"oracle demo {demo!s} graded incorrect at step {step}"
        )
        state = tutor.apply(demo)
    pytest.fail(f"did not reach is_done within {MAX_STEPS} steps")


def test_null_matcher_brd_completes():
    # 6_25_HTML uses legacy <matcher> entries with empty <input> text.
    steps = _run_oracle(
        MATHTUTOR / "6_25_HTML/HTML/6.25.html",
        MATHTUTOR / "6_25_HTML/FinalBRDs/GCF-6_28.brd",
    )
    assert steps > 0


def test_regex_matcher_brd_completes():
    # 7_06_HTML/01grapher.brd exercises RegexMatcher input resolution.
    steps = _run_oracle(
        MATHTUTOR / "7_06_HTML/HTML/7.06.html",
        MATHTUTOR / "7_06_HTML/FinalBRDs/01grapher.brd",
    )
    assert steps > 0


def test_expression_matcher_brd_completes():
    # Problem14 uses polyTermsEqual/expressionMatches formulas whose targets
    # extract to literals; it was INCOMPLETE in the baseline but completes
    # with the new ExpressionMatcher implementation.
    steps = _run_oracle(
        MATHTUTOR / "6_01_HTML/HTML/6.01-4.html",
        MATHTUTOR / "6_01_HTML/FinalBRDs/Problem14.brd",
    )
    assert steps > 0
