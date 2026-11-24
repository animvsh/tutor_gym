# CTAT Coverage Report

Baseline measurement of how many CTAT Mathtutor problems load and complete through the TutorGym `CTAT_Tutor` wrapper when stepped by the oracle agent.

## Summary

- Total problems: **691**
- PASS (load + complete): **618** (89.4%)
- LOAD_FAIL: **12** (1.7%)
- STEP_FAIL: **8** (1.2%)
- INCOMPLETE: **53** (7.7%)

## Failure categories

| Count | Category | Description | Example |
|------:|----------|-------------|---------|
| 52 | `brd_expression_matcher` | BRD ExpressionMatcher on a needed edge — formula like expressionMatches(input,x+1), polyTermsEqual(...), eval()/ifThen/isVar. Wrapper has a TODO stub; the oracle can only take the edge when the target value happens to be literal. | `tutorgym/envs/CTAT/Mathtutor/6_01_HTML/FinalBRDs/Problem1.brd` |
| 12 | `brd_old_matcher_null_input` | BRD legacy <matcher> with missing <input> value — parse_old_matcher gets None and ExactMatcher(None) later breaks. CTAT feature: old-style matcher element (pre-'matchers' schema) with empty Input. | `tutorgym/envs/CTAT/Mathtutor/6_25_HTML/FinalBRDs/GCF-6_28.brd` |
| 6 | `brd_regex_matcher_unresolved` | RegexMatcher used on an edge the oracle actually tried to take; input stays None because the wrapper never samples a concrete value from the regex. re.fullmatch(regex, None) explodes. | `tutorgym/envs/CTAT/Mathtutor/7_06_HTML/FinalBRDs/01grapher.brd` |
| 2 | `timeout` | Per-problem timeout exceeded. | `tutorgym/envs/CTAT/Mathtutor/6_11_HTML/FinalBRDs/p11.brd` |
| 1 | `brd_unordered_group` | BRD EdgeGroup with ordered='false' (unordered group) blocks oracle progress. FSM's _group_satisfied / add_unordered doesn't emit the right next actions. | `tutorgym/envs/CTAT/Mathtutor/6_07_HTML/FinalBRDs/bank-account.brd` |

## Examples per category (up to 3)

### `brd_expression_matcher` (52)

BRD ExpressionMatcher on a needed edge — formula like expressionMatches(input,x+1), polyTermsEqual(...), eval()/ifThen/isVar. Wrapper has a TODO stub; the oracle can only take the edge when the target value happens to be literal.

- `tutorgym/envs/CTAT/Mathtutor/6_01_HTML/FinalBRDs/Problem1.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _stuck_at=20_  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher multi EdgeGroup_
- `tutorgym/envs/CTAT/Mathtutor/6_01_HTML/FinalBRDs/Problem5.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _stuck_at=20_  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher multi EdgeGroup_
- `tutorgym/envs/CTAT/Mathtutor/6_01_HTML/FinalBRDs/Problem7.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _stuck_at=22_  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher multi EdgeGroup_

### `brd_old_matcher_null_input` (12)

BRD legacy <matcher> with missing <input> value — parse_old_matcher gets None and ExactMatcher(None) later breaks. CTAT feature: old-style matcher element (pre-'matchers' schema) with empty Input.

- `tutorgym/envs/CTAT/Mathtutor/6_25_HTML/FinalBRDs/GCF-6_28.brd`  (TypeError): TypeError: An error occured while parsing /Users/animesh/tutorgym/tutorgym/envs/CTAT/Mathtutor/6_25_HTML/FinalBRDs/GCF-6_28.brd decoding to str: need a bytes-like object, NoneType found  _brd: unordered_group RegexMatcher Buggy Action_
- `tutorgym/envs/CTAT/Mathtutor/6_25_HTML/FinalBRDs/GCF-16_24.brd`  (TypeError): TypeError: An error occured while parsing /Users/animesh/tutorgym/tutorgym/envs/CTAT/Mathtutor/6_25_HTML/FinalBRDs/GCF-16_24.brd decoding to str: need a bytes-like object, NoneType found  _brd: unordered_group RegexMatcher Buggy Action_
- `tutorgym/envs/CTAT/Mathtutor/6_25_HTML/FinalBRDs/GCF-15_45.brd`  (TypeError): TypeError: An error occured while parsing /Users/animesh/tutorgym/tutorgym/envs/CTAT/Mathtutor/6_25_HTML/FinalBRDs/GCF-15_45.brd decoding to str: need a bytes-like object, NoneType found  _brd: unordered_group RegexMatcher Buggy Action_

### `brd_regex_matcher_unresolved` (6)

RegexMatcher used on an edge the oracle actually tried to take; input stays None because the wrapper never samples a concrete value from the regex. re.fullmatch(regex, None) explodes.

- `tutorgym/envs/CTAT/Mathtutor/7_06_HTML/FinalBRDs/01grapher.brd`  (TypeError): TypeError: expected string or bytes-like object, got 'NoneType' _matchers=AnyMatcher,ExpressionMatcher,RegexMatcher; stuck_at=248; in_group_  _brd: unordered_group ExpressionMatcher RegexMatcher Buggy Action multi EdgeGroup_
- `tutorgym/envs/CTAT/Mathtutor/7_06_HTML/FinalBRDs/04grapher.brd`  (TypeError): TypeError: expected string or bytes-like object, got 'NoneType' _matchers=ExpressionMatcher; stuck_at=104; in_group_  _brd: unordered_group ExpressionMatcher RegexMatcher Buggy Action multi EdgeGroup_
- `tutorgym/envs/CTAT/Mathtutor/7_06_HTML/FinalBRDs/07grapher.brd`  (TypeError): TypeError: expected string or bytes-like object, got 'NoneType' _matchers=ExpressionMatcher; stuck_at=104; in_group_  _brd: unordered_group ExpressionMatcher RegexMatcher Buggy Action multi EdgeGroup_

### `timeout` (2)

Per-problem timeout exceeded.

- `tutorgym/envs/CTAT/Mathtutor/6_11_HTML/FinalBRDs/p11.brd`  (Timeout): __TIMEOUT__ exceeded per-problem timeout  _brd: unordered_group RegexMatcher MassProduction template Buggy Action_
- `tutorgym/envs/CTAT/Mathtutor/7_04_HTML/FinalBRDs/Problem3.brd`  (Timeout): __TIMEOUT__ exceeded per-problem timeout  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher_

### `brd_unordered_group` (1)

BRD EdgeGroup with ordered='false' (unordered group) blocks oracle progress. FSM's _group_satisfied / add_unordered doesn't emit the right next actions.

- `tutorgym/envs/CTAT/Mathtutor/6_07_HTML/FinalBRDs/bank-account.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _matchers=ExactMatcher; stuck_at=39; in_group_  _brd: unordered_group MassProduction template multi EdgeGroup_

## Recommendations

Ranked by "problems unblocked per unit of implementation effort":

- **1. Guard `parse_old_matcher` against empty `<input>` values** (12 problems unblocked, tiny patch)  
  In `tutorgym/env_classes/CTAT/brd_tools.py::parse_old_matcher`, `params['input']` is `None` for every BRD in `6_25_HTML` and the legacy matcher schema. Default `None` → `""` (or build an `AnyMatcher` when input is missing) and these problems load. Likely a few lines.

- **2. Implement `ExpressionMatcher.check` (expressionMatches / eval / ifThen / isVar / polyTermsEqual)** (52 problems unblocked)  
  The single biggest coverage lever. `ExpressionMatcher.check` in `brd_tools.py` is literally a `TODO actually make work` returning `inp == self.value`. Mathtutor BRDs express tutor checks as `expressionMatches(input, 8*x+12)`, `polyTermsEqual(input, "3x+4")`, `ifThen(...)`, `isVar(...)`, `eval(...)`. A sympy-based evaluator covers the common cases: parse the RHS, substitute vars from `state`, compare to `inp`. Units 6_01, 8_07, 8_17, 8_22 are fully gated on this.

- **3. Resolve `RegexMatcher` input before the oracle traverses the edge** (6 problems unblocked, small patch)  
  `brd_tools.resolve_action` only resolves `ExpressionMatcher` inputs. Extend it to also replace a `None`/template input with `norand_xeger(matcher.regex)` (already imported) for `RegexMatcher`. That stops the `re.fullmatch(regex, None)` crash in `7_06_HTML` / `7_10_HTML`.

- **4. Audit unordered-group exit logic in `fsm_tutor._group_satisfied`** (1 problem unblocked)  
  Only `6_07_HTML/bank-account.brd` (unordered group + MassProduction) is stranded this way, suggesting unordered-group support is largely in place and a targeted fix suffices.

If implemented in the order above, projected pass rate rises from 618/691 (89.4%) to 689/691.

## Methodology

- Every CTAT problem is enumerated from each unit's `package.xml` under `tutorgym/envs/CTAT/Mathtutor/`.
- The wrapper's browser-based HTML→JSON preprocessor is bypassed with an lxml/BeautifulSoup pre-render that writes the same cache file (`.<name>.tgym_<hash14>.json`) the wrapper looks for, plus a 1×1 PNG placeholder for the image cache check. No browser needed.
- Each problem is loaded through `CTAT_Tutor.set_problem(...)` and stepped by repeatedly applying `tutor.get_all_demos()[0]` until `is_done` fires, capped at 500 steps and 30s wall time, in an isolated subprocess (so a hang doesn't kill the sweep).
- Failures are classified from the raw error and, for INCOMPLETE runs, from a structured diagnostic that records the stuck node id, whether it's in a group, the matcher classes of its outgoing edges, actor, and whether any candidate input still contains a `%(...)%` MassProduction template.

## Per-unit table

| Unit | Total | PASS | LOAD_FAIL | STEP_FAIL | INCOMPLETE |
|------|------:|-----:|----------:|----------:|-----------:|
| 6_01_HTML | 25 | 15 | 0 | 0 | 10 |
| 6_02_HTML | 11 | 11 | 0 | 0 | 0 |
| 6_05_HTML | 8 | 8 | 0 | 0 | 0 |
| 6_06_HTML | 9 | 9 | 0 | 0 | 0 |
| 6_07_HTML | 7 | 6 | 0 | 0 | 1 |
| 6_08_HTML | 8 | 8 | 0 | 0 | 0 |
| 6_09_HTML | 5 | 5 | 0 | 0 | 0 |
| 6_10_HTML | 8 | 8 | 0 | 0 | 0 |
| 6_11_HTML | 27 | 26 | 0 | 1 | 0 |
| 6_14_HTML | 5 | 5 | 0 | 0 | 0 |
| 6_15_HTML | 9 | 9 | 0 | 0 | 0 |
| 6_16_HTML | 35 | 35 | 0 | 0 | 0 |
| 6_17_HTML | 24 | 24 | 0 | 0 | 0 |
| 6_18_HTML | 20 | 20 | 0 | 0 | 0 |
| 6_19_HTML | 23 | 23 | 0 | 0 | 0 |
| 6_20_HTML | 19 | 19 | 0 | 0 | 0 |
| 6_21_HTML | 10 | 10 | 0 | 0 | 0 |
| 6_24_HTML | 17 | 17 | 0 | 0 | 0 |
| 6_25_HTML | 12 | 0 | 12 | 0 | 0 |
| 6_26_HTML | 13 | 13 | 0 | 0 | 0 |
| 6_27_HTML | 15 | 15 | 0 | 0 | 0 |
| 6_28_HTML | 17 | 17 | 0 | 0 | 0 |
| 6_30_HTML | 11 | 11 | 0 | 0 | 0 |
| 6_34_HTML | 32 | 32 | 0 | 0 | 0 |
| 7_01_HTML | 9 | 9 | 0 | 0 | 0 |
| 7_02_HTML | 10 | 10 | 0 | 0 | 0 |
| 7_04_HTML | 10 | 9 | 0 | 1 | 0 |
| 7_05_HTML | 8 | 8 | 0 | 0 | 0 |
| 7_06_HTML | 10 | 6 | 0 | 4 | 0 |
| 7_10_HTML | 9 | 7 | 0 | 2 | 0 |
| 7_12_HTML | 17 | 17 | 0 | 0 | 0 |
| 7_15_HTML | 11 | 11 | 0 | 0 | 0 |
| 7_16_HTML | 8 | 8 | 0 | 0 | 0 |
| 7_50_HTML | 8 | 8 | 0 | 0 | 0 |
| 7_51_HTML | 8 | 8 | 0 | 0 | 0 |
| 7_52_HTML | 8 | 8 | 0 | 0 | 0 |
| 7_53_HTML | 11 | 11 | 0 | 0 | 0 |
| 7_54_HTML | 13 | 13 | 0 | 0 | 0 |
| 8_05_HTML | 10 | 10 | 0 | 0 | 0 |
| 8_06_HTML | 8 | 8 | 0 | 0 | 0 |
| 8_07_HTML | 8 | 6 | 0 | 0 | 2 |
| 8_09_HTML | 10 | 10 | 0 | 0 | 0 |
| 8_11_HTML | 17 | 17 | 0 | 0 | 0 |
| 8_12_HTML | 19 | 19 | 0 | 0 | 0 |
| 8_14_HTML | 8 | 8 | 0 | 0 | 0 |
| 8_17_HTML | 6 | 0 | 0 | 0 | 6 |
| 8_18_HTML | 8 | 8 | 0 | 0 | 0 |
| 8_19_HTML | 15 | 15 | 0 | 0 | 0 |
| 8_20_HTML | 7 | 7 | 0 | 0 | 0 |
| 8_21_HTML | 15 | 15 | 0 | 0 | 0 |
| 8_22_HTML | 34 | 0 | 0 | 0 | 34 |
| 8_26_HTML | 8 | 8 | 0 | 0 | 0 |
| 8_28_HTML | 8 | 8 | 0 | 0 | 0 |
