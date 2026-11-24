# CTAT Coverage Report

Measurement of how many CTAT Mathtutor problems load and complete through the TutorGym `CTAT_Tutor` wrapper when stepped by the oracle agent — a demo per step, graded CORRECT by the tutor's own `check()`, until `is_done` fires.

## Summary

- Total problems: **691**
- PASS (load + complete): **638** (92.3%)
- LOAD_FAIL: **0** (0.0%)
- STEP_FAIL: **0** (0.0%)
- INCOMPLETE: **53** (7.7%)

## Baseline vs now

The baseline was recorded at commit `7b20b8fd` on the same corpus (691 problems) with a 30s timeout. This run includes the null-matcher, ExpressionMatcher, and RegexMatcher fixes and uses a 60s timeout.

| Metric | Baseline | Now | Delta |
|---|---:|---:|---:|
| PASS | 618 | 638 | +20 |
| LOAD_FAIL | 12 | 0 | -12 |
| STEP_FAIL | 8 | 0 | -8 |
| INCOMPLETE | 53 | 53 | +0 |

Per-category (non-PASS):

| Category | Baseline | Now | Delta |
|---|---:|---:|---:|
| `brd_expression_matcher` | 52 | 52 | +0 |
| `brd_old_matcher_null_input` | 12 | 0 | -12 |
| `brd_regex_matcher_unresolved` | 6 | 0 | -6 |
| `brd_unordered_group` | 1 | 1 | +0 |
| `timeout` | 2 | 0 | -2 |

## Scope used by the real evals

The LLM evaluation pipeline (`tutorgym/eval/gen_ctat_compl_prof.py` → `collect_ctat_problems` → `tutorgym.envs.CTAT.Mathtutor.env_registry.WORKING_DOMAINS`) only consumes **139 problems across 10 units** out of the 691 measured here: `6_02_HTML, 6_07_HTML, 6_09_HTML, 6_11_HTML, 6_15_HTML, 6_27_HTML, 6_30_HTML, 6_34_HTML, 8_19_HTML, 8_20_HTML`. This is the author-whitelisted "working" slice and is the operational meaning of "only a fraction of the CTAT tutors work with the TutorGym interface". The coverage sweep intentionally runs the full corpus so new matcher features can be measured beyond that slice.

## Failure categories

| Count | Category | Description | Example |
|------:|----------|-------------|---------|
| 52 | `brd_expression_matcher` | BRD ExpressionMatcher on a needed edge — formula like expressionMatches(input,x+1), polyTermsEqual(...), eval()/ifThen/isVar. Wrapper has a TODO stub; the oracle can only take the edge when the target value happens to be literal. | `tutorgym/envs/CTAT/Mathtutor/6_01_HTML/FinalBRDs/Problem1.brd` |
| 1 | `brd_unordered_group` | BRD EdgeGroup with ordered='false' (unordered group) blocks oracle progress. FSM's _group_satisfied / add_unordered doesn't emit the right next actions. | `tutorgym/envs/CTAT/Mathtutor/6_07_HTML/FinalBRDs/bank-account.brd` |

## Examples per category (up to 3)

### `brd_expression_matcher` (52)

BRD ExpressionMatcher on a needed edge — formula like expressionMatches(input,x+1), polyTermsEqual(...), eval()/ifThen/isVar. Wrapper has a TODO stub; the oracle can only take the edge when the target value happens to be literal.

- `tutorgym/envs/CTAT/Mathtutor/6_01_HTML/FinalBRDs/Problem1.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _stuck_at=18_  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher multi EdgeGroup_
- `tutorgym/envs/CTAT/Mathtutor/6_01_HTML/FinalBRDs/Problem2.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _stuck_at=18_  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher multi EdgeGroup_
- `tutorgym/envs/CTAT/Mathtutor/6_01_HTML/FinalBRDs/Problem3.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _stuck_at=16_  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher multi EdgeGroup_

### `brd_unordered_group` (1)

BRD EdgeGroup with ordered='false' (unordered group) blocks oracle progress. FSM's _group_satisfied / add_unordered doesn't emit the right next actions.

- `tutorgym/envs/CTAT/Mathtutor/6_07_HTML/FinalBRDs/bank-account.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _matchers=ExactMatcher; stuck_at=39; in_group_  _brd: unordered_group MassProduction template multi EdgeGroup_

## Recommendations

Ranked by "problems unblocked per unit of implementation effort":

- **2. Implement `ExpressionMatcher.check` (expressionMatches / eval / ifThen / isVar / polyTermsEqual)** (52 problems unblocked)  
  The single biggest coverage lever. `ExpressionMatcher.check` in `brd_tools.py` is literally a `TODO actually make work` returning `inp == self.value`. Mathtutor BRDs express tutor checks as `expressionMatches(input, 8*x+12)`, `polyTermsEqual(input, "3x+4")`, `ifThen(...)`, `isVar(...)`, `eval(...)`. A sympy-based evaluator covers the common cases: parse the RHS, substitute vars from `state`, compare to `inp`. Units 6_01, 8_07, 8_17, 8_22 are fully gated on this.

- **4. Audit unordered-group exit logic in `fsm_tutor._group_satisfied`** (1 problem unblocked)  
  Only `6_07_HTML/bank-account.brd` (unordered group + MassProduction) is stranded this way, suggesting unordered-group support is largely in place and a targeted fix suffices.

If implemented in the order above, projected pass rate rises from 638/691 (92.3%) to 691/691.

## Methodology & caveats

**What PASS means.** Each problem is loaded through `CTAT_Tutor.set_problem(...)` and stepped by repeatedly taking `tutor.get_all_demos()[0]`. On every step we require `tutor.check(demo) == 1` (the tutor grades the demo CORRECT) before applying it; a problem is PASS only when `is_done` becomes True. Capped at 500 steps and 60s wall time, in an isolated subprocess so a hang doesn't kill the sweep.

**HTML pre-render instead of browser.** The CTAT wrapper's `HTML_Preprocessor` normally runs Firefox via Flask + Selenium to snapshot the live DOM into JSON (+ a PNG). We bypass that with an lxml/BeautifulSoup walk that writes the same `.<name>.tgym_<hash14>.json` cache file the wrapper looks for, plus a 1×1 PNG placeholder. Side-by-side with `tutorgym/html_tools/html_tools.js` the lxml walk is a subset of the browser output: bounding boxes are zeroed and `value` carries the stripped text node rather than `innerHTML`. CTAT_Tutor disables bounding-box use via `include_obj_bounds=False` and overrides `locked` based on empty value, so this shouldn't matter for *static* HTML pages — but any component rendered by CTAT's JavaScript widgets at runtime (canvas graphers, dynamic rows, ctatjs-wrapped inputs) won't appear in the lxml snapshot. A PASS here is therefore proof the BRD matcher and FSM logic work on the *static* DOM of each problem; it is **not** proof that the UI would load the same way in a real browser. Selenium/Firefox are not installed on the CI host, so a browser cross-check wasn't possible for this run.

**Enumeration.** Every CTAT problem is enumerated from each unit's `package.xml` under `tutorgym/envs/CTAT/Mathtutor/`. The real evaluation pipeline (`tutorgym/eval/gen_ctat_compl_prof.py` → `tutorgym.helpers.collect_problems.collect_ctat_problems` → `tutorgym.envs.CTAT.Mathtutor.env_registry.WORKING_DOMAINS`) only consumes 139 problems across 10 units (6_02_HTML, 6_07_HTML, 6_09_HTML, 6_11_HTML, 6_15_HTML, 6_27_HTML, 6_30_HTML, 6_34_HTML, 8_19_HTML, 8_20_HTML). That's ~20% of the available corpus and is the operational meaning of 'only a fraction of the CTAT tutors work': the author registry deliberately whitelists the units whose BRDs the wrapper could already drive end-to-end. This coverage sweep ignores that registry and measures every problem, so the headline number here is higher than the registry's working-set size.

**Failure classification.** Errors are matched first by regex; `INCOMPLETE` runs are then classified from a structured diagnostic that records the stuck node id, whether it's in an edge group, the matcher classes on its outgoing edges, actor, and whether any candidate input still contains a `%(...)%` MassProduction template.

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
| 6_11_HTML | 27 | 27 | 0 | 0 | 0 |
| 6_14_HTML | 5 | 5 | 0 | 0 | 0 |
| 6_15_HTML | 9 | 9 | 0 | 0 | 0 |
| 6_16_HTML | 35 | 35 | 0 | 0 | 0 |
| 6_17_HTML | 24 | 24 | 0 | 0 | 0 |
| 6_18_HTML | 20 | 20 | 0 | 0 | 0 |
| 6_19_HTML | 23 | 23 | 0 | 0 | 0 |
| 6_20_HTML | 19 | 19 | 0 | 0 | 0 |
| 6_21_HTML | 10 | 10 | 0 | 0 | 0 |
| 6_24_HTML | 17 | 17 | 0 | 0 | 0 |
| 6_25_HTML | 12 | 12 | 0 | 0 | 0 |
| 6_26_HTML | 13 | 13 | 0 | 0 | 0 |
| 6_27_HTML | 15 | 15 | 0 | 0 | 0 |
| 6_28_HTML | 17 | 17 | 0 | 0 | 0 |
| 6_30_HTML | 11 | 11 | 0 | 0 | 0 |
| 6_34_HTML | 32 | 32 | 0 | 0 | 0 |
| 7_01_HTML | 9 | 9 | 0 | 0 | 0 |
| 7_02_HTML | 10 | 10 | 0 | 0 | 0 |
| 7_04_HTML | 10 | 10 | 0 | 0 | 0 |
| 7_05_HTML | 8 | 8 | 0 | 0 | 0 |
| 7_06_HTML | 10 | 10 | 0 | 0 | 0 |
| 7_10_HTML | 9 | 9 | 0 | 0 | 0 |
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
