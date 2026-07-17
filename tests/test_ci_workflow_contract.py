from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tests.yml"


def test_ci_does_not_false_green_test_detection_or_install_failure():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "find tests -type f" in text
    assert "ls tests/test_*.py tests/*_test.py" not in text
    install_block = text.split("- name: Install project and test dependencies", 1)[1].split(
        "- name: Run test suite", 1
    )[0]
    assert "|| true" not in install_block
    assert "python -m pytest tests/ -q" in text


def test_ci_clean_install_smoke_runs_outside_checkout():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "python -m pip wheel . --wheel-dir dist" in text
    assert "python -m venv .wheel-smoke" in text
    assert "cd /tmp" in text
    assert '"$GITHUB_WORKSPACE/.wheel-smoke/bin/hippo" recovery --help' in text
