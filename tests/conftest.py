from pathlib import Path
import shutil

import pytest

from paulsha_hippo.importer import title


@pytest.fixture(autouse=True)
def _disable_live_external_agent(monkeypatch):
    """Tests must never call a real external agent (it would be a slow, environment-
    dependent network call inside the ingest path). Force the default title runner to
    raise so title generation deterministically falls back. Tests that exercise the
    external-agent success path override ``title._default_runner`` explicitly in the test body.
    """

    def _offline(text, command, timeout):
        raise RuntimeError("external agent disabled in tests")

    monkeypatch.setattr(title, "_default_runner", _offline)


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch, tmp_path):
    """Never let unit tests consume this workstation's deployed Hippo config."""

    root = tmp_path / "hippo-config"
    root.mkdir()
    source = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "atomizer" / "atomizer.yaml"
    shutil.copyfile(source, root / "config.yaml")
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(root))


@pytest.fixture(scope="session", autouse=True)
def _guard_real_hippo_config_untouched():
    """Regression guard for the 2026-08-25 live-config-overwrite incident
    (paulsha-hippo#141): a test called ``paths.atomizer_config_path()`` while
    ``HIPPO_CONFIG_ROOT`` was not overridden (the test was invoked outside
    pytest, bypassing ``_isolate_runtime_config`` above) and clobbered the
    operator's real ``~/.config/paulsha-hippo/config.yaml``.

    This checks the real, home-based path directly -- deliberately ignoring
    any ``HIPPO_CONFIG_ROOT``/``PSC_CONFIG_ROOT`` override -- so it still
    catches the bug even if a future test's own isolation is itself broken.
    Every test that needs a bespoke atomizer config must isolate via
    ``tests/atomizer_config_testutil.isolated_atomizer_config``; this
    fixture is only the safety net for a regression of the same class.
    """

    real_config = Path.home() / ".config" / "paulsha-hippo" / "config.yaml"
    before = real_config.read_bytes() if real_config.is_file() else None
    yield
    after = real_config.read_bytes() if real_config.is_file() else None
    assert before == after, (
        f"{real_config} changed during this pytest session -- a test wrote "
        "to the operator's real Hippo config instead of isolating via "
        "HIPPO_CONFIG_ROOT (see tests/atomizer_config_testutil.py). This is "
        "the 2026-08-25 live-config-overwrite regression class "
        "(paulsha-hippo#141); bisect with -k to find the offending test."
    )
