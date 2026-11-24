"""Run coverage for selected units only. Usage: scripts/_cov_units.py UNIT [UNIT...]"""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.ctat_coverage import enumerate_problems, run_with_timeout


def main(argv):
    if not argv:
        from tutorgym.envs.CTAT.Mathtutor.env_registry import WORKING_DOMAINS
        units = sorted(WORKING_DOMAINS)
    else:
        units = argv
    problems = [p for p in enumerate_problems() if p['unit'] in set(units)]
    by_unit = {}
    for p in problems:
        by_unit.setdefault(p['unit'], []).append(p)
    totals = {'pass': 0, 'strict': 0, 'n': 0}
    for u in sorted(by_unit):
        passes = strict = 0
        fails = []
        for p in by_unit[u]:
            r = run_with_timeout(p['html_path'], p['model_path'], 60)
            if r['status'] == 'PASS':
                passes += 1
                if r.get('strict_ok'):
                    strict += 1
            else:
                fails.append((p['problem_name'], r['status'], (r.get('error_msg') or '').splitlines()[0][:120]))
        total = len(by_unit[u])
        totals['pass'] += passes
        totals['strict'] += strict
        totals['n'] += total
        print(f'{u}: {passes}/{total} pass, {strict} strict')
        for pn, st, em in fails:
            print(f'    FAIL {pn}  {st}  {em}')
    print(f'\nTOTAL: {totals["pass"]}/{totals["n"]} pass, {totals["strict"]} strict')


if __name__ == '__main__':
    main(sys.argv[1:])
