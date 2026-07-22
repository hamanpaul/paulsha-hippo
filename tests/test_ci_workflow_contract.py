from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tests.yml"


def test_ci_does_not_false_green_test_detection_or_install_failure():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Detect test suite" not in text
    assert "steps.detect" not in text
    install_block = text.split("- name: Install project and test dependencies", 1)[1].split(
        "- name: Collect test suite", 1
    )[0]
    assert "|| true" not in install_block
    assert "python -m pip install \".[test]\"" in install_block
    assert "python -m pytest --collect-only -q" in text
    assert "python -m pytest tests/ -q" in text


def test_ci_clean_install_smoke_runs_outside_checkout():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "python -m pip wheel . --wheel-dir dist" in text
    assert "python -m venv .wheel-smoke" in text
    assert "cd /tmp" in text
    assert '"$GITHUB_WORKSPACE/.wheel-smoke/bin/hippo" recovery --help' in text
    assert '"$GITHUB_WORKSPACE/.wheel-smoke/bin/hippo" install hooks' in text
    assert '"$GITHUB_WORKSPACE/.wheel-smoke/bin/hippo" doctor' in text
    assert "Traceback|TypeError" in text


def test_ci_wheel_build_uses_isolated_forced_build_base():
    root = WORKFLOW.parents[2]
    setup = (root / "setup.cfg").read_text(encoding="utf-8")
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "build_base = .setuptools-build" in setup
    assert "force = 1" in setup
    assert "build/" in ignored
    assert ".setuptools-build/" in ignored
