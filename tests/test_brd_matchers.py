"""Unit tests for the CTAT BRD matcher implementations in brd_tools."""
import pytest

from tutorgym.env_classes.CTAT.brd_tools import (
    AnyMatcher,
    ExactMatcher,
    ExpressionMatcher,
    RegexMatcher,
    _split_top_level,
    extract_expression_target,
    parse_matcher,
    parse_old_matcher,
)
import lxml.etree as ET


# ---------------------------------------------------------------------------
# Legacy <matcher> parsing: null input must not crash.

def _old_matcher_xml(selection, action, inp, actor="Student"):
    inp_tag = f"<matcherParameter name=\"input\">{inp}</matcherParameter>" if inp is not None \
        else "<matcherParameter name=\"input\"></matcherParameter>"
    return ET.fromstring(f"""
        <matcher>
            <matcherType>ExactMatcher</matcherType>
            <matcherParameter name="selection">{selection}</matcherParameter>
            <matcherParameter name="action">{action}</matcherParameter>
            {inp_tag}
            <matcherParameter name="actor">{actor}</matcherParameter>
        </matcher>
    """)


def test_old_matcher_with_null_input_is_exact_empty():
    # CTAT semantics: ExactMatcher with empty <input> means "expected input is
    # the empty string" (not "accept any"). Preserves grading strictness on
    # clear-field Correct Action edges like 6_25_HTML GCF-16_24.brd uid 17.
    actor, action = parse_old_matcher(_old_matcher_xml("R1C1", "UpdateTextField", None))
    assert actor == "student"
    assert action.input == ""
    checker = action.get_annotation("checker")
    assert isinstance(checker.input_matcher, ExactMatcher)
    assert checker.input_matcher.value == ""
    assert checker.input_matcher.check({}, "") is True
    assert checker.input_matcher.check({}, "7") is False


def test_old_matcher_with_concrete_input_keeps_exact_semantics():
    actor, action = parse_old_matcher(_old_matcher_xml("R1C1", "UpdateTextField", "6"))
    assert action.input == "6"
    checker = action.get_annotation("checker")
    assert isinstance(checker.input_matcher, ExactMatcher)
    assert checker.input_matcher.check({}, "6") is True
    assert checker.input_matcher.check({}, "7") is False


# ---------------------------------------------------------------------------
# New-style <matchers> parsing: null param becomes AnyMatcher instead of
# crashing norand_xeger with RegexMatcher(None).

def _matcher_node(matcher_type, param_name, param_value):
    txt = "" if param_value is None else param_value
    return ET.fromstring(f"""
        <matcher>
            <matcherType>{matcher_type}</matcherType>
            <matcherParameter name="{param_name}">{txt}</matcherParameter>
        </matcher>
    """)


def test_parse_matcher_null_exact_becomes_empty_string_exact():
    # Strict CTAT semantic preserved: empty <matcherParameter name="single">
    # for an ExactMatcher means "expected value is ''".
    node = _matcher_node("ExactMatcher", "single", None)
    m = parse_matcher(node)
    assert isinstance(m, ExactMatcher)
    assert m.value == ""
    assert m.check({}, "") is True
    assert m.check({}, "x") is False


def test_parse_matcher_null_regex_falls_back_to_any():
    # RegexMatcher with None regex would crash norand_xeger — keep the
    # defensive AnyMatcher fallback for non-Exact types.
    node = _matcher_node("RegexMatcher", "single", None)
    m = parse_matcher(node)
    assert isinstance(m, AnyMatcher)


def test_parse_matcher_regex_parses_sampled_value():
    node = _matcher_node("RegexMatcher", "single", "R[4-5]C[3-4]")
    m = parse_matcher(node)
    assert isinstance(m, RegexMatcher)
    assert m.value is not None
    assert m.check({}, m.value) is True


def test_regex_matcher_check_is_none_safe():
    m = RegexMatcher("foo|bar")
    assert m.check({}, None) is False
    assert m.check({}, "foo") is True
    assert m.check({}, "baz") is False


# ---------------------------------------------------------------------------
# ExpressionMatcher formula extraction.

def test_split_top_level_respects_parens_and_quotes():
    assert _split_top_level("a, b, c") == ["a", "b", "c"]
    assert _split_top_level("f(a, b), c") == ["f(a, b)", "c"]
    assert _split_top_level('a, "b, c", d') == ["a", '"b, c"', "d"]


@pytest.mark.parametrize(
    "formula,expected",
    [
        ("10", "10"),
        ('expressionMatches(input, "10")', "10"),
        ("expressionMatches(input,160)", "160"),
        ("expressionMatches(10*4*3*1,input)", "10*4*3*1"),
        ('polyTermsEqual(input, "5+3")', "5+3"),
        ('and(polyTermsEqual(input, "15+35"))', "15+35"),
        ('and( expressionMatches(input, 9 / 10), rationalEquals(getDivisor(input),100))', "9 / 10"),
        ('or(polyTermsEqual(input,concat(link.input,"+")), polyTermsEqual(input,"foo"))', "foo"),
        ('matchWithoutPrecision(calc(input),"1")', "1"),
        ("equals(6/4, eval(input))", "6/4"),
    ],
)
def test_extract_expression_target_literal_cases(formula, expected):
    assert extract_expression_target(formula) == expected


def test_extract_expression_target_unresolved_returns_none():
    # RHS references a state variable we can't evaluate here.
    assert extract_expression_target(
        'expressionMatches(concat(thirdleft,"+35"),input)'
    ) is None


def test_expression_matcher_sympy_equivalence():
    m = ExpressionMatcher("expressionMatches(input, 10*4*3*1)", "boolean")
    assert m.target == "10*4*3*1"
    # Exact string:
    assert m.check({}, "10*4*3*1") is True
    # Algebraically equivalent rearrangement:
    assert m.check({}, "4*10*3*1") is True
    # Totally different:
    assert m.check({}, "123") is False
    # None is a rejection, not a crash.
    assert m.check({}, None) is False


def test_expression_matcher_unresolved_formula_is_strict():
    # Strict grading: when the formula references an identifier we can't
    # resolve against the state, reject the input rather than accept anything.
    m = ExpressionMatcher('expressionMatches(concat(x,"+1"),input)', "boolean")
    assert m.target is None
    # self.value is still a non-None string so edge template filters pass.
    assert m.value is not None
    # Empty state -> identifier 'x' unresolvable -> strict rejection.
    assert m.check({}, "anything") is False
    assert m.check({}, None) is False


def test_expression_matcher_resolves_state_variable_concat():
    # concat(thirdleft,"+35") with thirdleft='9' resolves to '9+35'.
    m = ExpressionMatcher(
        'expressionMatches(concat(thirdleft,"+35"),input)', "boolean")
    state = {"thirdleft": {"value": "9"}}
    assert m.check(state, "9+35") is True
    assert m.check(state, "10+35") is False
    assert m.check(state, "44") is True  # algebraic equivalent


def test_expression_matcher_or_accepts_any_branch():
    m = ExpressionMatcher(
        'or(expressionMatches(input, 3), expressionMatches(input, 5))',
        "boolean")
    assert m.check({}, "3") is True
    assert m.check({}, "5") is True
    assert m.check({}, "4") is False


def test_expression_matcher_and_requires_all_branches():
    # 6 satisfies both predicates (==6 and even). 4 satisfies only "even".
    m = ExpressionMatcher(
        'and(expressionMatches(input, 6), expressionMatches(input, 6))',
        "boolean")
    assert m.check({}, "6") is True
    assert m.check({}, "4") is False


def test_link_ref_resolves_from_action_hist():
    # CTAT formulas like concat(link25.input, "/4 + 150") resolve
    # link25.input to the student input on the earlier edge whose
    # <uniqueID>25</uniqueID>. _resolve_operand reads state.action_hist
    # and matches by the 'unique_id' annotation carried on each demo.
    from tutorgym.env_classes.CTAT.brd_tools import _resolve_operand
    from tutorgym.shared import ProblemState, Action
    hist = [Action(("variable", "UpdateTextField", "x"), unique_id="25")]
    s = ProblemState({})
    s.action_hist = hist
    assert _resolve_operand("link25.input", s, inp=None) == "x"
    # Full formula path via ExpressionMatcher.check
    m = ExpressionMatcher(
        'polyTermsEqual(input, concat(link25.input, "/4 + 150"))', "boolean")
    assert m.check(s, "x/4 + 150") is True
    assert m.check(s, "y/4 + 150") is False


def test_regex_match_in_expression_matcher():
    # 7_15_HTML/1.brd fourthMid2: or(and(regExMatch(".*[+-*/].*",input),
    #   expressionMatches(input,"1.25")), dollarEquals(input,"1.25"))
    # The oracle used to type the raw regex pattern; now resolve_action
    # prefers the BRD's recorded Input ("1.25"), which the matcher
    # accepts via the dollarEquals branch.
    f = 'or(and(regExMatch(".*[\\+\\-\\*\\/].*",input), expressionMatches(input,"1.25")), dollarEquals(input,"1.25"))'
    m = ExpressionMatcher(f, "boolean")
    assert m.check({}, "1.25") is True
    assert m.check({}, "1.0+0.25") is True   # satisfies the regex+equals and-branch
    assert m.check({}, "wrong") is False


def test_dollar_equals_tolerates_prefix():
    m = ExpressionMatcher('dollarEquals(input,"1.25")', "boolean")
    assert m.check({}, "1.25") is True
    assert m.check({}, "$1.25") is True
    assert m.check({}, "1.26") is False


def test_expression_matcher_and_tolerates_unsupported_branch():
    # 8_09_HTML/8.9.2.brd q7c1 edge: formula wraps equals(...) with an
    # or() of isSimplified/isSimplifiedMixed sanity predicates we don't
    # model. The equals branch already answered YES — don't let the
    # unsupported sibling branch drag the whole and() to False.
    f = 'and(equals("-5/3",input),or(isSimplifiedMixed(input),isSimplified(input)))'
    m = ExpressionMatcher(f, "boolean")
    assert m.check({}, "-5/3") is True
    # Wrong fraction must still be rejected.
    assert m.check({}, "5/3") is False
    assert m.check({}, "tgym_wrong") is False


def test_expression_matcher_calc_input_wrapper():
    # matchWithoutPrecision(calc(input), "1") - calc(input) treated as input.
    m = ExpressionMatcher('matchWithoutPrecision(calc(input),"1")', "boolean")
    assert m.check({}, "1") is True
    assert m.check({}, "1.0") is True   # sympy equal
    assert m.check({}, "2") is False
