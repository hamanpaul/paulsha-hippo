import pytest

from paulsha_hippo.importer import title


@pytest.fixture(autouse=True)
def _disable_live_gemma4(monkeypatch):
    """Tests must never call the real gemma4 backend (it would be a slow, environment-
    dependent network call inside the ingest path). Force the default title runner to
    raise so title generation deterministically falls back. Tests that exercise the
    gemma4-success path override ``title._default_runner`` explicitly in the test body.
    """

    def _offline(text, command, timeout):
        raise RuntimeError("gemma4 disabled in tests")

    monkeypatch.setattr(title, "_default_runner", _offline)
