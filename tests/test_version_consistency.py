import re
from pathlib import Path

import paulsha_hippo

ROOT = Path(__file__).resolve().parents[1]


def test_version_file_matches_package():
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == paulsha_hippo.__version__


def test_pyproject_matches_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, flags=re.M)
    assert m and m.group(1) == paulsha_hippo.__version__
