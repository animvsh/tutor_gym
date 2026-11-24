import re as _re
import lxml.etree as ET
from tutorgym.utils import norand_xeger
from tutorgym.shared import Action
from tutorgym.env_classes.fsm_tutor import ActionGroup


# ---------------------
# : ExpressionMatcher formula helpers
#
# CTAT BRDs express tutor checks as small formulas inside <matcherParameter
# name="InputExpression">. The most common shapes (by raw count across
# Mathtutor) are literal values, expressionMatches(input, X) /
# expressionMatches(X, input), polyTermsEqual(input, "X"), and_wrappers
# like and(...) / or(...) around either of the above. Together those cover
# ~70% of the ExpressionMatcher formulas in the corpus. The code below
# extracts a concrete *target value* so the oracle can supply a demo input,
# and compares student input to that target using a sympy-based algebraic
# equality with string fallback.

_FUNC_RE = _re.compile(r'^\s*([A-Za-z_]\w*)\s*\((.*)\)\s*$', _re.DOTALL)


def _split_top_level(s):
    """Split a comma-separated arg list respecting parentheses and quotes."""
    out, buf, depth = [], [], 0
    quote = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = ''.join(buf).strip()
    if tail or out:
        out.append(tail)
    return out


def _strip_quotes(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _is_input_token(s):
    return s.strip() == 'input'


def extract_expression_target(expr):
    """Return a concrete target value string for an ExpressionMatcher formula,
    or None if no literal target can be extracted (e.g. the RHS references
    state variables the wrapper doesn't evaluate).
    """
    if expr is None:
        return None
    expr = expr.strip()
    if not expr:
        return None

    mo = _FUNC_RE.match(expr)
    if not mo:
        # Not a function call — treat the whole expression as a literal.
        return _strip_quotes(expr)

    name = mo.group(1)
    args = _split_top_level(mo.group(2))

    # Logical wrappers: recurse into the first child that yields a target.
    if name in ('and', 'or'):
        for a in args:
            t = extract_expression_target(a)
            if t is not None:
                return t
        return None

    # expressionMatches(input, X) or expressionMatches(X, input):
    # pick the non-'input' side as the target.
    if name in ('expressionMatches', 'polyTermsEqual', 'algPartiallyEquivalent',
                'patternMatches', 'fractionEquals', 'matchWithoutPrecision',
                'equals', 'regExMatch', 'algEquiv'):
        for a in args:
            if _is_input_token(a):
                continue
            # Nested calc(input)/eval(input)/rm1coeff(input) → skip, those
            # wrap the student input, not the target.
            inner = _FUNC_RE.match(a)
            if inner and _is_input_token(inner.group(2)):
                continue
            target = _strip_quotes(a)
            # If the target itself still references BRD-time state variables
            # (e.g. concat(thirdleft, "+35")) we can't resolve it to a literal.
            if any(k in target for k in ('concat(', 'calc(', 'eval(', 'input')):
                return None
            return target
        return None

    # Literal or unknown shape — fall back to the raw expression.
    return _strip_quotes(expr)


def _sympy_equal(a, b):
    """True if two strings represent algebraically equivalent expressions.
    Returns False on any parse failure. Used by ExpressionMatcher.check().
    """
    try:
        from sympy import sympify, simplify
        ea = sympify(a, evaluate=True)
        eb = sympify(b, evaluate=True)
        return bool(simplify(ea - eb) == 0)
    except Exception:
        return False


_IDENT_RE = _re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
# Function wrappers whose single argument should be treated as the student
# input itself (CTAT applies them before the equality check; TutorGym grades
# against the raw typed text).
_INPUT_WRAPPERS = {'calc', 'eval', 'rm1coeff', 'simplify', 'fmtDollar',
                   'fmtDecimal'}
# Comparison predicates that reduce to "is one side equal to the other"
# modulo symbolic simplification. We treat all of these the same way.
_EQ_PREDICATES = {'expressionMatches', 'equals', 'polyTermsEqual',
                  'algPartiallyEquivalent', 'algEquiv', 'fractionEquals',
                  'matchWithoutPrecision', 'patternMatches', 'regExMatch'}


def _resolve_operand(expr, state, inp):
    """Resolve a formula operand to a concrete string value.

    - ``'input'`` -> the student's typed input.
    - Quoted literal -> literal content.
    - Bare identifier -> ``state[ident]['value']`` or ``['input']``.
    - ``concat(a, b, ...)`` -> concatenation of resolved args.
    - ``calc(input)`` / ``eval(input)`` / ``rm1coeff(input)`` etc. -> input.
    - Raises ``_Unsupported`` if the operand cannot be resolved.
    """
    expr = expr.strip()
    if _is_input_token(expr):
        return str(inp)
    # Quoted literal.
    if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in ('"', "'"):
        return expr[1:-1]
    # Function wrapper.
    mo = _FUNC_RE.match(expr)
    if mo:
        name = mo.group(1)
        args = _split_top_level(mo.group(2))
        if name == 'concat':
            return ''.join(_resolve_operand(a, state, inp) for a in args)
        if name in _INPUT_WRAPPERS and len(args) == 1:
            return _resolve_operand(args[0], state, inp)
        raise _Unsupported(f"cannot resolve operand {expr!r}")
    # Bare identifier referencing a state selection id.
    if _IDENT_RE.match(expr):
        try:
            obj = state[expr] if hasattr(state, '__getitem__') else None
        except Exception:
            obj = None
        if isinstance(obj, dict):
            for k in ('value', 'input'):
                v = obj.get(k)
                if v is not None:
                    return str(v)
        raise _Unsupported(f"unresolved identifier {expr!r}")
    # Dotted ref (e.g. link25.input) — not supported.
    if '.' in expr:
        raise _Unsupported(f"dotted ref {expr!r}")
    # Numeric literal / simple expression — hand back as-is for sympy.
    return expr


class _Unsupported(Exception):
    pass


def _value_equals(a, b):
    a, b = str(a).strip(), str(b).strip()
    if a == b:
        return True
    return _sympy_equal(a, b)


def _resolve_target_for_state(formula, state):
    """Extract a concrete demo input for a formula against a specific state.

    Walks the same predicate shapes as ``_eval_predicate`` but, instead of
    checking equality, returns the resolved non-'input' side as a string.
    Returns None when nothing can be resolved.
    """
    if not formula:
        return None
    expr = formula.strip()
    mo = _FUNC_RE.match(expr)
    if not mo:
        return _strip_quotes(expr)
    name = mo.group(1)
    args = _split_top_level(mo.group(2))
    if name in ('and', 'or'):
        for a in args:
            t = _resolve_target_for_state(a, state)
            if t is not None:
                return t
        return None
    if name in _EQ_PREDICATES:
        for a in args:
            if _is_input_token(a):
                continue
            inner = _FUNC_RE.match(a)
            if inner and inner.group(1) in _INPUT_WRAPPERS and _is_input_token(inner.group(2)):
                continue
            try:
                return _resolve_operand(a, state, inp=None)
            except _Unsupported:
                continue
        return None
    return None


def _eval_predicate(expr, state, inp):
    """Evaluate a CTAT ExpressionMatcher formula against (state, inp).

    Returns True/False for supported shapes; raises ``_Unsupported`` for
    formulas we don't model (strict behaviour — caller returns False).
    """
    expr = (expr or '').strip()
    if not expr:
        raise _Unsupported("empty formula")
    mo = _FUNC_RE.match(expr)
    if not mo:
        # Treat the whole expression as a literal the student must type.
        return _value_equals(str(inp), _strip_quotes(expr))
    name = mo.group(1)
    args = _split_top_level(mo.group(2))
    if name == 'and':
        return all(_eval_predicate(a, state, inp) for a in args)
    if name == 'or':
        for a in args:
            try:
                if _eval_predicate(a, state, inp):
                    return True
            except _Unsupported:
                continue
        return False
    if name in _EQ_PREDICATES and len(args) >= 2:
        lhs = _resolve_operand(args[0], state, inp)
        rhs = _resolve_operand(args[1], state, inp)
        return _value_equals(lhs, rhs)
    raise _Unsupported(f"unsupported predicate {name!r}")


# ---------------------
class BaseMatcher:
    def __str__(self):
        return f"{type(self).__name__}({', '.join([f'{k}={v}' for k,v in self.__dict__.items()])})"

class ExpressionMatcher(BaseMatcher):
    def __init__(self, InputExpression, relation, **kwargs):
        self.formula = InputExpression
        self.relation = relation
        self.kwargs = kwargs
        # target is the concrete literal the oracle can type when the formula
        # can be reduced; otherwise None (formulas that reference state vars
        # like concat(thirdleft,"+35") can't be reduced here).
        self.target = extract_expression_target(InputExpression)
        # self.value is what parse_matchers reads as the Input SAI value and
        # what the oracle types when nothing else resolves it. Prefer the
        # extracted literal; fall back to the raw formula so the action still
        # has a non-None input (keeps it off the action_not_template filter
        # and distinct from unrelated edges).
        self.value = self.target if self.target is not None else InputExpression

    def check(self, state, inp):
        if inp is None:
            return False
        # Strict evaluation against the full formula (handles and/or,
        # state-variable references like concat(thirdleft,"+35"), and nested
        # input-wrappers like calc(input)). If the formula uses a predicate
        # we don't model, return False so wrong inputs are not silently
        # accepted. The oracle still gets a demo input from ``self.target``
        # for the resolvable branch, so BRDs whose first branch reduces to
        # a literal keep passing; unresolvable BRDs count honestly as not
        # supported.
        try:
            return bool(_eval_predicate(self.formula, state, inp))
        except _Unsupported:
            pass
        # Final fallback: if a literal target was extracted, accept equality
        # against it. Covers predicates whose arg resolution raised _Unsupported
        # but whose target string the oracle already computed (keeps lenient
        # pre-existing passes for pure-literal formulas intact).
        if self.target is not None:
            return _value_equals(str(inp), str(self.target))
        return False

    def resolve_target_against_state(self, state):
        """Return a concrete expected input value resolved against ``state``,
        or None if the formula still references unknown variables.

        Used by the tutor at demo time to produce a typeable value for
        formulas like ``expressionMatches(concat(thirdleft, "+35"), input)``,
        whose target cannot be derived statically from the BRD alone.
        """
        return _resolve_target_for_state(self.formula, state)


class ExactMatcher(BaseMatcher): 
    def __init__(self, single):
        self.value = single

    def check(self, state, inp):
        return inp == self.value

class RegexMatcher(BaseMatcher):
    def __init__(self, single, **kwargs):
        self.regex = single
        self.value = norand_xeger(self.regex)

    def check(self, state, inp):
        if inp is None:
            return False
        return _re.fullmatch(self.regex, str(inp)) is not None

class AnyMatcher(BaseMatcher): 
    def __init__(self, **kwargs):
        self.value = None

    def check(self, state, inp):
        return True


matcher_classes = {
    "ExpressionMatcher" : ExpressionMatcher,
    "ExactMatcher" : ExactMatcher,
    "RegexMatcher" : RegexMatcher,
    "AnyMatcher" : AnyMatcher,
}

class Checker():
    def __init__(self, selection, action_type, input_matcher):
        self.selection = selection
        self.action_type = action_type
        self.input_matcher = input_matcher

    def __call__(self, state, action):
        sel, act, inp = action.as_tuple()
        if(sel == self.selection and
           act == self.action_type and
           self.input_matcher.check(state, inp)):
            return True
        return False

    def __str__(self):
        return f"Checker({self.input_matcher})"

    __repr__ = __str__


# --------------------
# : parse_sai / matcher

def parse_sai(node):
    selection = node.find("Selection").find("value").text
    action_type = node.find("Action").find("value").text
    inp = node.find("Input").find("value").text
    # inputs = {}
    # for node in node.find("Input"):
    #     inputs[node.tag] = node.text

    return Action((selection, action_type, inp))

def parse_matcher(node):
    matcher_type = node.find("matcherType").text
    cls = matcher_classes[matcher_type]
    params = {x.attrib['name'] : x.text for x in node.iter("matcherParameter")}
    # Normalise empty matcherParameter text (``None`` here). CTAT itself treats
    # ExactMatcher(single="") as "expected input is the empty string" — e.g. a
    # clear-field or no-op Correct Action — so preserve that semantic rather
    # than silently converting to AnyMatcher (which would accept ANY input and
    # corrupt grading). For other matcher classes with required params still
    # missing, fall back to AnyMatcher so parsing doesn't crash (RegexMatcher
    # with None regex explodes inside norand_xeger).
    if matcher_type == "ExactMatcher" and params.get("single") is None:
        return ExactMatcher("")
    if any(v is None for v in params.values()):
        return AnyMatcher()
    matcher = cls(**params)
    return matcher


def parse_old_matcher(node):
    matcher_type = node.find("matcherType").text
    cls = matcher_classes[matcher_type]

    params = {x.attrib['name'] : x.text for x in node.iter("matcherParameter")}

    selection = params["selection"]
    action_type = params["action"]
    inp = params["input"]
    actor = params["actor"].lower()

    # Legacy <matcher> entries sometimes omit the <input> text. CTAT treats
    # ExactMatcher with empty input as "expected input is the empty string"
    # (not "match any") — e.g. a clear-field Correct Action. Preserve that so
    # strict grading doesn't accept arbitrary wrong inputs. For other matcher
    # classes where a missing param would crash construction (e.g. RegexMatcher
    # with None regex), fall back to AnyMatcher.
    if inp is None:
        if cls is ExactMatcher:
            inp_m = ExactMatcher("")
        else:
            inp_m = AnyMatcher()
        inp = ""
    else:
        inp_m = cls(params["input"])

    annotations = {"checker" :
        Checker(selection, action_type, inp_m)
    }
    return actor, Action((selection, action_type, inp), **annotations)



def parse_matchers(node):
    sel_m = parse_matcher(node.find("Selection").find("matcher"))
    act_m = parse_matcher(node.find("Action").find("matcher"))
    inp_m = parse_matcher(node.find("Input").find("matcher"))

    # assert isinstance(sel_m, ExactMatcher), "Not Implemented non exact 'selection' matcher"
    # assert isinstance(act_m, ExactMatcher), "Not Implemented non exact action_type matcher"

    selection = sel_m.value
    action_type = act_m.value
    inp = inp_m.value

    actor = node.find("Actor").text.lower()
    # selection = node.find("Selection").find("matcher").text
    # action_type = node.find("Action").find("value").text
    # inputs = {}
    # for node in node.find("Input"):
    #     inputs[node.tag] = node.text

    annotations = {}
    if(not isinstance(inp_m, ExactMatcher)):
        annotations['checker'] = Checker(selection, action_type, inp_m)

    return actor, Action((selection, action_type, inp), **annotations)


# --------------------
# : parse_start_node_messages()

def parse_jess_wm_description(node):
    # Probably don't need to implement this, but it is a thing
    #  in the brds of Mathtutor
    pass


def parse_start_node_messages(messages, verbosity=1):
    problem_name = None
    start_actions = []

    for message in messages:
        if(message.tag != "message"):
            if(verbosity > 0):
                print(f"Warning StartNodeMessages contains non-message: {message}")
            continue

        verb = message.find("verb").text
        if(verb == "SendNoteProperty"):
            properties = message.find("properties")
            message_type = properties.find("MessageType").text

            # StartProblem
            if(message_type == "StartProblem"):
                problem_name = properties.find("ProblemName").text

            # Jess Stuff
            elif(message_type == "InterfaceDescription"):
                parse_jess_wm_description(properties)

        elif(verb == "NotePropertySet"):
            properties = message.find("properties")
            message_type = properties.find("MessageType").text

            # InterfaceAction
            if(message_type == "InterfaceAction"):
                action = parse_sai(properties)
                start_actions.append(action)

        else:
            if(verbosity > 0):
                print(f"Warning unhandled verb: {verb}")
            
        # print("M", message, message.find("verb"))
    return problem_name, start_actions


# --------------------
# : parse_edges()
import re 
polyTermsEqual_re = re.compile(r"polyTermsEqual\((.*),\"([^\)\"]*)\"\)")
expr_matches_re = re.compile(r"expressionMatches\((.*),([^\)\"]*)\)")
# number_re = re.compile(r"expressionMatches\((.*),(.*)\)")

def resolve_input_from_matcher(matcher, matcher_action):
    sel, at, inp = matcher_action.as_tuple()
    # ExpressionMatcher.value is already the extracted literal (or None) now.
    if isinstance(matcher, ExpressionMatcher):
        return matcher.value
    if matcher.relation in ("=", "<=", ">="):
        return matcher.value
    return None


def resolve_action(message_action, matcher_action):
    ''' Uses information from the message_action and matcher_action to resolve the
        'input' field of the SAIs.
    '''
    if(matcher_action.get_annotation("omitted", False) == True or
       matcher_action.get_annotation("action_type") == "Buggy Action"):
        return matcher_action

    checker = matcher_action.get_annotation('checker')
    if(not checker or isinstance(checker.input_matcher, ExactMatcher)):
        return matcher_action

    sel, at, inp = matcher_action.as_tuple()
    matcher = checker.input_matcher

    if(isinstance(matcher, ExpressionMatcher)):
        resolved = matcher.target if matcher.target is not None else matcher.value
        if(resolved is not None):
            return Action((sel, at, resolved), **matcher_action.annotations)
        return matcher_action

    if(isinstance(matcher, RegexMatcher)):
        # Give the oracle a concrete string the regex accepts. norand_xeger
        # already produced one at matcher construction time; re-use it so the
        # tutor.apply() path doesn't crash on re.fullmatch(regex, None).
        if(matcher.value is not None):
            return Action((sel, at, matcher.value), **matcher_action.annotations)

    return matcher_action

def parse_edge(edge, verbosity=1):
    # for edge in edges:

    # -- SAI Part --
    action_label = edge.find("actionLabel")
    message = action_label.find("message")

    verb = message.find("verb").text
    if(verb not in ("NotePropertySet", "SendNoteProperty")):
        if(verbosity > 0):
            print(f"Warning edge message unknown verb: {verb}")
    properties = message.find("properties")

    message_type = properties.find("MessageType").text
    if(message_type not in ["InterfaceAction", "CorrectAction"]):
        raise ValueError(f"unexpected edge MessageType: {properties.find('MessageType').text}")

    # TODO... not sure what 
    message_action = parse_sai(properties)

    
    matchers = action_label.find("matchers")
    if(matchers is not None):
        actor, matcher_action = parse_matchers(matchers)
    else:
        matcher = action_label.find("matcher")
        actor, matcher_action = parse_old_matcher(matcher)


    # print("matchers", matchers)

    # Use the matcher action instaed of the message action
    
    # print("action", action)

        

    # -- Annotations --
    annotations = {"actor" : actor,
        "optional" : action_label.attrib['minTraversals'] == "0",
        "omitted" : action_label.attrib['maxTraversals'] == "0",
        **matcher_action.annotations}

    bm = action_label.find("buggyMessage")
    if(bm is not None):
        annotations["buggy_message"] = bm.text

    sm = action_label.find("successMessage")
    if(sm is not None):
        annotations["success_message"] = sm.text

    act_t = action_label.find("actionType")
    if(act_t is not None):
        annotations["action_type"] = act_t.text

    uid = action_label.find("uniqueID")
    if(uid is not None):
        annotations["unique_id"] = uid.text

    src_id = edge.find("sourceID").text
    annotations["src_id"] = src_id
    dest_id = edge.find("destID").text
    annotations["dest_id"] = dest_id

    hint_messages = []
    for hint in action_label.iter("hintMessage"):
        if(hint.text):
            hint_messages.append(hint.text)

    annotations["hint_messages"] = hint_messages

    matcher_action.add_annotations(annotations)


    action = resolve_action(message_action, matcher_action)
    

    # print(f"{repr(action)}")
    
    return (src_id, dest_id, action)


# --------------------
# : parse_group()

def parse_groups(edge_groups, edges):
    out_groups = {}
    for i, group_node in enumerate(edge_groups.iter("group")):
        name = group_node.attrib.get("name", f'group{i}')
        assert name not in out_groups, f"Duplicate EdgeGroup name {name}"

        actions = []
        for link_node in group_node.iter("link"):
            s, n, action = edges[link_node.attrib['id']]
            action.add_annotations({"group" : name})
            actions.append(action)

        unordered = group_node.attrib.get("ordered", 'false') != 'false'
        reenterable = group_node.attrib.get("reenterable", True)

        out_groups[name] = ActionGroup(name, actions, unordered, reenterable)

    return out_groups


def parse_brd(filepath, verbosity=1):
    start_actions = None
    edges = {}
    _eg_nodes = []
    edge_groups = {}

    tree = ET.parse(filepath)
    root = tree.getroot()

    try:
        for child in root:
            if(child.tag == "startNodeMessages"):
                problem_name, start_actions = parse_start_node_messages(child)

            elif(child.tag == "edge"):
                edge = (src,dest,action) = parse_edge(child, verbosity)
                edges[action.get_annotation('unique_id')] = edge
            elif(child.tag == "EdgesGroups"):
                _eg_nodes.append(child)

        # Need be parsed after edges
        for eg_node in _eg_nodes:
            edge_groups.update(parse_groups(eg_node, edges))

    except Exception as e:
        raise type(e)(f"An error occured while parsing {filepath} " + str(e)) from e


    # print(problem_name)
    # print("--START ACTIONS--")
    # for a in start_actions:
    #     print(a)
    # print("--EDGES--")
    # for e in edges:
    #     print(e)

    return start_actions, edges, edge_groups

        # print("  ", child, child.tag)

# parse_brd("AS_3_7_plus_4_7.brd")
# parse_brd("Mathtutor/6_01_HTML/FinalBRDs/Problem1.brd")


