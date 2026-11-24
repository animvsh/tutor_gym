"""Shared test fixtures: pre-render HTML via scripts/ctat_coverage so tests
don't need a browser to spin up the CTAT_Tutor wrapper."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

MATHTUTOR = REPO_ROOT / "tutorgym" / "envs" / "CTAT" / "Mathtutor"


def prerender(html_path):
    from scripts.ctat_coverage import prerender_html_to_json
    prerender_html_to_json(Path(html_path))
