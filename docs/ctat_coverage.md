# CTAT Coverage Report

Measurement of how many CTAT Mathtutor problems load and complete through the TutorGym `CTAT_Tutor` wrapper when stepped by the oracle agent — a demo per step, graded CORRECT by the tutor's own `check()`, until `is_done` fires.

## Summary

- Total problems: **691**
- PASS (load + complete): **670** (97.0%)
- PASS_STRICT (PASS AND wrong inputs rejected at every step): **670** (97.0%)
- LOAD_FAIL: **0** (0.0%)
- STEP_FAIL: **0** (0.0%)
- INCOMPLETE: **21** (3.0%)

## Baseline vs now

Baseline numbers are measured with this same script (including PASS_STRICT) against commit `7b20b8fd` in a sibling git worktree. Both columns use the same methodology, timeout (60s per problem), and pre-render/strict-grading logic.

| Metric | Baseline | Now | Delta |
|---|---:|---:|---:|
| PASS | 620 | 670 | +50 |
| PASS_STRICT | 620 | 670 | +50 |
| LOAD_FAIL | 12 | 0 | -12 |
| STEP_FAIL | 7 | 0 | -7 |
| INCOMPLETE | 52 | 21 | -31 |

Per-category (non-PASS):

| Category | Baseline | Now | Delta |
|---|---:|---:|---:|
| `brd_expression_matcher` | 51 | 20 | -31 |
| `brd_old_matcher_null_input` | 12 | 0 | -12 |
| `brd_regex_matcher_unresolved` | 7 | 0 | -7 |
| `brd_unordered_group` | 1 | 1 | +0 |

## Scope used by the real evals

The LLM evaluation pipeline (`tutorgym/eval/gen_ctat_compl_prof.py` → `collect_ctat_problems` → `tutorgym.envs.CTAT.Mathtutor.env_registry.WORKING_DOMAINS`) only consumes **139 problems across 10 units** out of the 691 measured here: `6_02_HTML, 6_07_HTML, 6_09_HTML, 6_11_HTML, 6_15_HTML, 6_27_HTML, 6_30_HTML, 6_34_HTML, 8_19_HTML, 8_20_HTML`. This is the author-whitelisted "working" slice and is the operational meaning of "only a fraction of the CTAT tutors work with the TutorGym interface". The coverage sweep intentionally runs the full corpus so new matcher features can be measured beyond that slice.

## Failure categories

| Count | Category | Description | Example |
|------:|----------|-------------|---------|
| 20 | `brd_expression_matcher` | BRD ExpressionMatcher on a needed edge — formula like expressionMatches(input,x+1), polyTermsEqual(...), eval()/ifThen/isVar. Wrapper has a TODO stub; the oracle can only take the edge when the target value happens to be literal. | `tutorgym/envs/CTAT/Mathtutor/8_07_HTML/FinalBRDs/8.7.1.brd` |
| 1 | `brd_unordered_group` | BRD EdgeGroup with ordered='false' (unordered group) blocks oracle progress. FSM's _group_satisfied / add_unordered doesn't emit the right next actions. | `tutorgym/envs/CTAT/Mathtutor/6_07_HTML/FinalBRDs/bank-account.brd` |

## Examples per category (up to 3)

### `brd_expression_matcher` (20)

BRD ExpressionMatcher on a needed edge — formula like expressionMatches(input,x+1), polyTermsEqual(...), eval()/ifThen/isVar. Wrapper has a TODO stub; the oracle can only take the edge when the target value happens to be literal.

- `tutorgym/envs/CTAT/Mathtutor/8_07_HTML/FinalBRDs/8.7.1.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _matchers=ExactMatcher,ExpressionMatcher; stuck_at=7; in_group_  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher MassProduction template multi EdgeGroup_
- `tutorgym/envs/CTAT/Mathtutor/8_07_HTML/FinalBRDs/8.7.2.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _matchers=ExactMatcher,ExpressionMatcher; stuck_at=7; in_group_  _brd: unordered_group ExpressionMatcher polyTermsEqual RegexMatcher MassProduction template multi EdgeGroup_
- `tutorgym/envs/CTAT/Mathtutor/8_22_HTML/FinalBRDs/02circleCirc.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _stuck_at=1_  _brd: unordered_group ExpressionMatcher MassProduction template Buggy Action_

### `brd_unordered_group` (1)

BRD EdgeGroup with ordered='false' (unordered group) blocks oracle progress. FSM's _group_satisfied / add_unordered doesn't emit the right next actions.

- `tutorgym/envs/CTAT/Mathtutor/6_07_HTML/FinalBRDs/bank-account.brd`  (INCOMPLETE): __INCOMPLETE__ no demos available _matchers=ExactMatcher; stuck_at=39; in_group_  _brd: unordered_group MassProduction template multi EdgeGroup_

## Recommendations

Ranked by "problems unblocked per unit of implementation effort":

- **2. Implement `ExpressionMatcher.check` (expressionMatches / eval / ifThen / isVar / polyTermsEqual)** (20 problems unblocked)  
  The single biggest coverage lever. `ExpressionMatcher.check` in `brd_tools.py` is literally a `TODO actually make work` returning `inp == self.value`. Mathtutor BRDs express tutor checks as `expressionMatches(input, 8*x+12)`, `polyTermsEqual(input, "3x+4")`, `ifThen(...)`, `isVar(...)`, `eval(...)`. A sympy-based evaluator covers the common cases: parse the RHS, substitute vars from `state`, compare to `inp`. Units 6_01, 8_07, 8_17, 8_22 are fully gated on this.

- **4. Audit unordered-group exit logic in `fsm_tutor._group_satisfied`** (1 problem unblocked)  
  Only `6_07_HTML/bank-account.brd` (unordered group + MassProduction) is stranded this way, suggesting unordered-group support is largely in place and a targeted fix suffices.

If implemented in the order above, projected pass rate rises from 670/691 (97.0%) to 691/691.

## Methodology & caveats

**What PASS means.** Each problem is loaded through `CTAT_Tutor.set_problem(...)` and stepped by repeatedly taking `tutor.get_all_demos()[0]`. On every step we require `tutor.check(demo) == 1` (the tutor grades the demo CORRECT) before applying it; a problem is PASS only when `is_done` becomes True. Capped at 500 steps and 60s wall time, in an isolated subprocess so a hang doesn't kill the sweep.

**HTML pre-render instead of browser.** The CTAT wrapper's `HTML_Preprocessor` normally runs Firefox via Flask + Selenium to snapshot the live DOM into JSON (+ a PNG). We bypass that with an lxml/BeautifulSoup walk that writes the same `.<name>.tgym_<hash14>.json` cache file the wrapper looks for, plus a 1×1 PNG placeholder. Side-by-side with `tutorgym/html_tools/html_tools.js` the lxml walk is a subset of the browser output: bounding boxes are zeroed and `value` carries the stripped text node rather than `innerHTML`. CTAT_Tutor disables bounding-box use via `include_obj_bounds=False` and overrides `locked` based on empty value, so this shouldn't matter for *static* HTML pages — but any component rendered by CTAT's JavaScript widgets at runtime (canvas graphers, dynamic rows, ctatjs-wrapped inputs) won't appear in the lxml snapshot. A PASS here is therefore proof the BRD matcher and FSM logic work on the *static* DOM of each problem; it is **not** proof that the UI would load the same way in a real browser. Selenium/Firefox are not installed on the CI host, so a browser cross-check wasn't possible for this run.

**Enumeration.** Every CTAT problem is enumerated from each unit's `package.xml` under `tutorgym/envs/CTAT/Mathtutor/`. The real evaluation pipeline (`tutorgym/eval/gen_ctat_compl_prof.py` → `tutorgym.helpers.collect_problems.collect_ctat_problems` → `tutorgym.envs.CTAT.Mathtutor.env_registry.WORKING_DOMAINS`) only consumes 139 problems across 10 units (6_02_HTML, 6_07_HTML, 6_09_HTML, 6_11_HTML, 6_15_HTML, 6_27_HTML, 6_30_HTML, 6_34_HTML, 8_19_HTML, 8_20_HTML). That's ~20% of the available corpus and is the operational meaning of 'only a fraction of the CTAT tutors work': the author registry deliberately whitelists the units whose BRDs the wrapper could already drive end-to-end. This coverage sweep ignores that registry and measures every problem, so the headline number here is higher than the registry's working-set size.

**Failure classification.** Errors are matched first by regex; `INCOMPLETE` runs are then classified from a structured diagnostic that records the stuck node id, whether it's in an edge group, the matcher classes on its outgoing edges, actor, and whether any candidate input still contains a `%(...)%` MassProduction template.

**Baseline passes that were pseudo-passes.** A sweep of the baseline worktree (`7b20b8fd`) captures every demo input the oracle typed; matching those against formula fingerprints (`polyTermsEqual(`, `expressionMatches(`, `and(`, `or(`, `concat(`, `regExMatch(`, `dollarEquals(`, `equals(`) shows that **273 of baseline's 617 PASSes (~44%) typed the raw formula string as the demo**, and the loose `inp == self.value` check happened to accept it. Full data and 10 example inputs are in `docs/ctat_baseline_pseudo_pass.json`. After this round of matcher work, the honest PASS_STRICT count stands at 670, above the baseline's 617 (and well above the ~344 real baseline passes once pseudo-passes are deducted).

**Why PASS_STRICT can decrease vs. baseline.** The baseline's `ExpressionMatcher.check()` was the literal `inp == self.value` with `self.value` kept as the raw formula string (e.g. `and(polyTermsEqual(input, concat(link.input, "*6")))`). The oracle typed that same raw string as its demo, and check happened to accept it — a PASS in name only. The wrong-input probe `tgym_wrong_...` didn't match the raw formula string either, so PASS_STRICT was True. Those pseudo-passes now fail honestly: the oracle extracts a real literal when possible and otherwise lets the step fail, so no synthetic demos sneak through as correct.

## Candidate units for WORKING_DOMAINS

Units where **every** problem currently passes strictly (PASS_STRICT == total) but that are NOT present in `tutorgym.envs.CTAT.Mathtutor.env_registry.WORKING_DOMAINS`. These are candidates for Danny to review; this script does not edit `WORKING_DOMAINS`.

| Unit | Problems |
|------|---------:|
| `6_01_HTML` | 25 |
| `6_05_HTML` | 8 |
| `6_06_HTML` | 9 |
| `6_08_HTML` | 8 |
| `6_10_HTML` | 8 |
| `6_14_HTML` | 5 |
| `6_16_HTML` | 35 |
| `6_17_HTML` | 24 |
| `6_18_HTML` | 20 |
| `6_19_HTML` | 23 |
| `6_20_HTML` | 19 |
| `6_21_HTML` | 10 |
| `6_24_HTML` | 17 |
| `6_25_HTML` | 12 |
| `6_26_HTML` | 13 |
| `6_28_HTML` | 17 |
| `7_01_HTML` | 9 |
| `7_02_HTML` | 10 |
| `7_04_HTML` | 10 |
| `7_05_HTML` | 8 |
| `7_06_HTML` | 10 |
| `7_10_HTML` | 9 |
| `7_12_HTML` | 17 |
| `7_15_HTML` | 11 |
| `7_16_HTML` | 8 |
| `7_50_HTML` | 8 |
| `7_51_HTML` | 8 |
| `7_52_HTML` | 8 |
| `7_53_HTML` | 11 |
| `7_54_HTML` | 13 |
| `8_05_HTML` | 10 |
| `8_06_HTML` | 8 |
| `8_09_HTML` | 10 |
| `8_11_HTML` | 17 |
| `8_12_HTML` | 19 |
| `8_14_HTML` | 8 |
| `8_17_HTML` | 6 |
| `8_18_HTML` | 8 |
| `8_21_HTML` | 15 |
| `8_26_HTML` | 8 |
| `8_28_HTML` | 8 |

## Per-unit table

| Unit | Total | PASS | PASS_STRICT | LOAD_FAIL | STEP_FAIL | INCOMPLETE |
|------|------:|-----:|------------:|----------:|----------:|-----------:|
| 6_01_HTML | 25 | 25 | 25 | 0 | 0 | 0 |
| 6_02_HTML | 11 | 11 | 11 | 0 | 0 | 0 |
| 6_05_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 6_06_HTML | 9 | 9 | 9 | 0 | 0 | 0 |
| 6_07_HTML | 7 | 6 | 6 | 0 | 0 | 1 |
| 6_08_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 6_09_HTML | 5 | 5 | 5 | 0 | 0 | 0 |
| 6_10_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 6_11_HTML | 27 | 27 | 27 | 0 | 0 | 0 |
| 6_14_HTML | 5 | 5 | 5 | 0 | 0 | 0 |
| 6_15_HTML | 9 | 9 | 9 | 0 | 0 | 0 |
| 6_16_HTML | 35 | 35 | 35 | 0 | 0 | 0 |
| 6_17_HTML | 24 | 24 | 24 | 0 | 0 | 0 |
| 6_18_HTML | 20 | 20 | 20 | 0 | 0 | 0 |
| 6_19_HTML | 23 | 23 | 23 | 0 | 0 | 0 |
| 6_20_HTML | 19 | 19 | 19 | 0 | 0 | 0 |
| 6_21_HTML | 10 | 10 | 10 | 0 | 0 | 0 |
| 6_24_HTML | 17 | 17 | 17 | 0 | 0 | 0 |
| 6_25_HTML | 12 | 12 | 12 | 0 | 0 | 0 |
| 6_26_HTML | 13 | 13 | 13 | 0 | 0 | 0 |
| 6_27_HTML | 15 | 15 | 15 | 0 | 0 | 0 |
| 6_28_HTML | 17 | 17 | 17 | 0 | 0 | 0 |
| 6_30_HTML | 11 | 11 | 11 | 0 | 0 | 0 |
| 6_34_HTML | 32 | 32 | 32 | 0 | 0 | 0 |
| 7_01_HTML | 9 | 9 | 9 | 0 | 0 | 0 |
| 7_02_HTML | 10 | 10 | 10 | 0 | 0 | 0 |
| 7_04_HTML | 10 | 10 | 10 | 0 | 0 | 0 |
| 7_05_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 7_06_HTML | 10 | 10 | 10 | 0 | 0 | 0 |
| 7_10_HTML | 9 | 9 | 9 | 0 | 0 | 0 |
| 7_12_HTML | 17 | 17 | 17 | 0 | 0 | 0 |
| 7_15_HTML | 11 | 11 | 11 | 0 | 0 | 0 |
| 7_16_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 7_50_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 7_51_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 7_52_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 7_53_HTML | 11 | 11 | 11 | 0 | 0 | 0 |
| 7_54_HTML | 13 | 13 | 13 | 0 | 0 | 0 |
| 8_05_HTML | 10 | 10 | 10 | 0 | 0 | 0 |
| 8_06_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 8_07_HTML | 8 | 6 | 6 | 0 | 0 | 2 |
| 8_09_HTML | 10 | 10 | 10 | 0 | 0 | 0 |
| 8_11_HTML | 17 | 17 | 17 | 0 | 0 | 0 |
| 8_12_HTML | 19 | 19 | 19 | 0 | 0 | 0 |
| 8_14_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 8_17_HTML | 6 | 6 | 6 | 0 | 0 | 0 |
| 8_18_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 8_19_HTML | 15 | 15 | 15 | 0 | 0 | 0 |
| 8_20_HTML | 7 | 7 | 7 | 0 | 0 | 0 |
| 8_21_HTML | 15 | 15 | 15 | 0 | 0 | 0 |
| 8_22_HTML | 34 | 16 | 16 | 0 | 0 | 18 |
| 8_26_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
| 8_28_HTML | 8 | 8 | 8 | 0 | 0 | 0 |
