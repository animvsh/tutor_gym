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


def test_brd_start_state_node_name_respected():
    # 8_22_HTML/01circleArea.brd declares startStateNodeName="labelFilled"
    # (a non-"1" node). It never progressed past step 0 while the wrapper
    # hardcoded the FSM start uid to "1". Fixing that makes the oracle
    # complete the problem.
    steps = _run_oracle(
        MATHTUTOR / "8_22_HTML/HTML/8.22.html",
        MATHTUTOR / "8_22_HTML/FinalBRDs/01circleArea.brd",
    )
    assert steps > 0


def test_brd_start_state_falls_back_to_one_when_missing(tmp_path):
    # A BRD without startStateNodeName should still begin at uid "1"
    # (CTAT's historical default). Every full BRD in the corpus declares
    # the attribute, so construct a minimal BRD on the fly that doesn't.
    brd = tmp_path / "no_start.brd"
    brd.write_text(
        '<?xml version="1.0" standalone="yes"?>\n'
        '<stateGraph version="4.0">\n'
        '  <node><text>node1</text><uniqueID>1</uniqueID></node>\n'
        '</stateGraph>\n'
    )
    from tutorgym.env_classes.CTAT.brd_tools import parse_brd
    _, _, _, start_id = parse_brd(str(brd))
    assert start_id == "1"


def test_repeated_sai_chain_completes():
    # 8_17_HTML/01Automobile.brd has two consecutive edges with the same SAI
    # (UpdateTextField(Ans6, 120)) across sequential nodes. The _action_satisfied
    # filter used to drop the second edge because its SAI was already in
    # history, stranding the oracle. Direct out-edges should always be
    # emitted; only group members are deduped by history.
    steps = _run_oracle(
        MATHTUTOR / "8_17_HTML/HTML/8.17.html",
        MATHTUTOR / "8_17_HTML/FinalBRDs/01Automobile.brd",
    )
    assert steps > 0


def test_brd_start_state_resolves_named_node():
    from tutorgym.env_classes.CTAT.brd_tools import parse_brd
    _, _, _, start_id = parse_brd(
        str(MATHTUTOR / "8_22_HTML/FinalBRDs/01circleArea.brd")
    )
    # "labelFilled" node has uniqueID 24 in that BRD.
    assert start_id == "24"
