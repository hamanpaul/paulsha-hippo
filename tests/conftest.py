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
