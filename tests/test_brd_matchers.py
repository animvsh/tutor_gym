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


def test_old_matcher_with_null_input_becomes_anymatcher():
    actor, action = parse_old_matcher(_old_matcher_xml("R1C1", "UpdateTextField", None))
    assert actor == "student"
    assert action.input == ""
    checker = action.get_annotation("checker")
    assert isinstance(checker.input_matcher, AnyMatcher)
    # AnyMatcher accepts any typed value.
    assert checker.input_matcher.check({}, "7") is True
    assert checker.input_matcher.check({}, "") is True


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


@pytest.mark.parametrize("mt", ["ExactMatcher", "RegexMatcher"])
def test_parse_matcher_with_null_param_returns_anymatcher(mt):
    node = _matcher_node(mt, "single", None)
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


def test_expression_matcher_unresolved_formula_is_lenient():
    m = ExpressionMatcher('expressionMatches(concat(x,"+1"),input)', "boolean")
    assert m.target is None
    # Still has a self.value so parse_matchers can read something non-None.
    assert m.value is not None
    # Lenient: any typed input accepted when the RHS can't be reduced.
    assert m.check({}, "anything") is True
    assert m.check({}, None) is False
