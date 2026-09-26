"""Production task-memory provider contract tests (#155)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from pathlib import Path

import pytest

from paulsha_hippo.importer.config import ProjectConfig, ProjectsConfig
from paulsha_hippo.ledger.processing import redact_secret_text
from paulsha_hippo.task_memory_provider import (
    MAX_CANDIDATES,
    TaskMemoryProvider,
    TaskMemoryProviderError,
    deadline,
)


FIXTURE = Path(__file__).parent / "fixtures" / "task_memory" / "cortex-note-fetch-request.json"
NOTE_BODY = "Use the shared parser and keep the read boundary scoped.\n"
NOTE_TEXT = (
    "---\nslice_id: note-1\ntitle: Scoped parser boundary\n"
    "captured_at: '2026-09-25T12:00:00Z'\n---\n" + NOTE_BODY
)


def _envelope(mode: str = "note_fetch") -> dict:
    envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))
    envelope["delivery"]["mode"] = mode
    return envelope


def _projects(*remotes: str) -> ProjectsConfig:
    return ProjectsConfig(
        projects=(ProjectConfig(slug="hippo-demo", remotes=remotes or ("github.com/acme/demo",)),)
    )


def _provider(tmp_path: Path, *, search_fn=None, projects=None) -> TaskMemoryProvider:
    note_path = tmp_path / "knowledge" / "note-1.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(NOTE_TEXT, encoding="utf-8")

    def search(_root, _query, *, project, limit, include_decayed):
        assert project == "hippo-demo"
        assert limit == MAX_CANDIDATES
        assert include_decayed is False
        return [{
            "slice_id": "note-1",
            "project": project,
            "title": "Scoped parser boundary",
            "path": str(note_path),
            "captured_at": "2026-09-25T12:00:00Z",
        }]

    return TaskMemoryProvider(
        memory_root=tmp_path,
        projects=projects or _projects(),
        search_fn=search_fn or search,
    )


def _manifest_digest(manifest: dict) -> str:
    unsigned = {key: value for key, value in manifest.items() if key != "sha256"}
    encoded = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_with_cortex_contract(payload: dict, request: dict) -> None:
    """Fixture copy of Cortex _validate_payload/_validate_manifest contract checks.

    Keep this independent of paulsha-cortex imports so Hippo pins the public JSON
    boundary without acquiring a runtime dependency on the consumer repository.
    """
    assert re.fullmatch(r"(?:^|/)v?1(?:\.\d+)?$", str(payload["schema_version"]))
    assert payload["task_id"] == request["task_id"]
    assert payload["project"] == request["project"]
    assert isinstance(payload["intent"], str) and len(payload["intent"]) <= 280
    delivery = payload["delivery"]
    mode = request["delivery"]["mode"]
    assert delivery["mode"] == mode
    assert delivery["capabilities"][mode] is True
    candidates = payload["candidates"]
    assert isinstance(candidates, list) and len(candidates) <= 3
    seen = set()
    for candidate in candidates:
        assert candidate["authorization"]["status"] == "authorized"
        assert candidate["availability"]["status"] == "available"
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", candidate["note_id"])
        assert re.fullmatch(r"[0-9a-f]{64}", candidate["content_hash"])
        assert isinstance(candidate["content_version"], str) and candidate["content_version"]
        assert candidate["project"] == request["project"]
        assert isinstance(candidate["summary"], str) and candidate["summary"]
        assert isinstance(candidate["source_time"], str) and candidate["source_time"]
        assert candidate["note_id"] not in seen
        seen.add(candidate["note_id"])
    manifest = delivery["manifest"]
    assert manifest["schema"] == "hippo/task-memory-manifest/v1"
    assert manifest["task_id"] == request["task_id"]
    assert manifest["project"] == request["project"]
    assert manifest["sha256"] == _manifest_digest(manifest)
    entries = {entry["note_id"]: entry for entry in manifest["entries"]}
    assert set(entries) == seen
    for candidate in candidates:
        entry = entries[candidate["note_id"]]
        assert entry["project"] == request["project"]
        assert entry["content_hash"] == candidate["content_hash"]
        assert entry["content_version"] == candidate["content_version"]
        if mode == "snapshot":
            assert hashlib.sha256(entry["content"].encode("utf-8")).hexdigest() == candidate["content_hash"]
        if mode == "inline":
            delivered = candidate.get("excerpt") or candidate["summary"]
            assert hashlib.sha256(delivered.encode("utf-8")).hexdigest() == candidate["content_hash"]


@pytest.mark.parametrize("mode", ["note_fetch", "snapshot", "inline"])
def test_provider_modes_satisfy_cortex_contract_and_manifest_hash(tmp_path, mode):
    request = _envelope(mode)
    payload = _provider(tmp_path).provide(request)

    _validate_with_cortex_contract(payload, request)
    assert len(payload["candidates"]) == 1
    assert payload["delivery"]["manifest"]["sha256"] == _manifest_digest(
        payload["delivery"]["manifest"]
    )
    if mode == "note_fetch":
        assert "content" not in payload["delivery"]["manifest"]["entries"][0]
    elif mode == "snapshot":
        assert payload["delivery"]["manifest"]["entries"][0]["content"] == redact_secret_text(NOTE_BODY)
    else:
        assert payload["candidates"][0]["excerpt"] == redact_secret_text(NOTE_BODY).strip()


def test_no_candidates_returns_valid_empty_manifest(tmp_path):
    provider = _provider(tmp_path, search_fn=lambda *_args, **_kwargs: [])

    payload = provider.provide(_envelope())

    _validate_with_cortex_contract(payload, _envelope())
    assert payload["candidates"] == []
    assert payload["delivery"]["manifest"]["entries"] == []


def test_note_content_size_is_bounded(tmp_path):
    provider = _provider(tmp_path)
    (tmp_path / "knowledge" / "note-1.md").write_text(
        "---\ntitle: Scoped parser boundary\n---\n" + "x" * 70_000,
        encoding="utf-8",
    )

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.provide(_envelope())

    assert exc.value.code == "size-limit"


def test_missing_hippo_evidence_source_denies_without_global_search(tmp_path):
    calls = []
    provider = _provider(tmp_path, search_fn=lambda *args, **kwargs: calls.append((args, kwargs)))
    request = _envelope()
    request["delivery"]["host_scope"]["allowed_evidence_sources"].remove("hippo")

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.provide(request)

    assert exc.value.code == "permission-denied"
    assert calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [(PermissionError("denied"), "permission-denied"), (TimeoutError("late"), "timeout")],
)
def test_permission_and_timeout_are_classified_without_logging_note_content(tmp_path, error, code):
    def fail_search(*_args, **_kwargs):
        raise error

    provider = _provider(tmp_path, search_fn=fail_search)

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.provide(_envelope())

    assert exc.value.code == code
    assert NOTE_BODY not in str(exc.value)


def test_project_registry_mismatch_fails_before_search(tmp_path):
    calls = []
    provider = _provider(
        tmp_path,
        search_fn=lambda *args, **kwargs: calls.append((args, kwargs)),
        projects=_projects("github.com/other/repo"),
    )

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.provide(_envelope())

    assert exc.value.code == "scope-mismatch"
    assert calls == []


def test_search_result_path_outside_knowledge_root_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text(NOTE_TEXT, encoding="utf-8")
    provider = _provider(
        tmp_path,
        search_fn=lambda _root, _query, **_kwargs: [{
            "slice_id": "note-1",
            "project": "hippo-demo",
            "title": "Scoped parser boundary",
            "path": str(outside),
        }],
    )

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.provide(_envelope())

    assert exc.value.code == "scope-mismatch"


def test_ambiguous_remote_mapping_fails_closed(tmp_path):
    projects = ProjectsConfig(projects=(
        ProjectConfig(slug="one", remotes=("github.com/acme/demo",)),
        ProjectConfig(slug="two", remotes=("github.com/acme/demo",)),
    ))
    provider = _provider(tmp_path, projects=projects)

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.provide(_envelope())

    assert exc.value.code == "scope-mismatch"


def test_top_level_and_host_repo_mismatch_is_rejected(tmp_path):
    provider = _provider(tmp_path)
    request = _envelope()
    request["project"] = "acme/other"

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.provide(request)

    assert exc.value.code == "scope-mismatch"


def test_unknown_schema_major_is_structured_error(tmp_path):
    request = _envelope()
    request["schema_version"] = "2"

    with pytest.raises(TaskMemoryProviderError) as exc:
        _provider(tmp_path).provide(request)

    assert exc.value.code == "unsupported-schema"


def test_deadline_converts_expiration_to_timeout_code():
    with pytest.raises(TaskMemoryProviderError) as exc:
        with deadline(0.05):
            time.sleep(0.1)

    assert exc.value.code == "timeout"
    assert exc.value.exit_code != 0


def test_fetch_returns_only_manifest_bound_note_with_redacted_hash(tmp_path):
    provider = _provider(tmp_path)
    request = _envelope()
    payload = provider.provide(request)
    wrapper = {
        "envelope": request,
        "manifest": payload["delivery"]["manifest"],
        "note_id": "note-1",
    }

    fetched = provider.fetch(wrapper)

    expected = redact_secret_text(NOTE_BODY)
    assert fetched["content"] == expected
    assert fetched["content_hash"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert fetched["content_version"] == payload["candidates"][0]["content_version"]


def test_fetch_rejects_note_outside_manifest_and_path_injection(tmp_path):
    provider = _provider(tmp_path)
    request = _envelope()
    payload = provider.provide(request)
    manifest = copy.deepcopy(payload["delivery"]["manifest"])
    wrapper = {"envelope": request, "manifest": manifest, "note_id": "../note-1"}

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.fetch(wrapper)
    assert exc.value.code == "manifest-mismatch"

    manifest["entries"][0]["path"] = "/outside/secret.md"
    manifest["sha256"] = _manifest_digest(manifest)
    wrapper["note_id"] = "note-1"
    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.fetch(wrapper)
    assert exc.value.code == "manifest-mismatch"


def test_fetch_reports_hash_mismatch_after_note_changes(tmp_path):
    provider = _provider(tmp_path)
    request = _envelope()
    payload = provider.provide(request)
    wrapper = {
        "envelope": request,
        "manifest": payload["delivery"]["manifest"],
        "note_id": "note-1",
    }
    (tmp_path / "knowledge" / "note-1.md").write_text(
        NOTE_TEXT.replace(NOTE_BODY, "Changed note content.\n"), encoding="utf-8"
    )

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.fetch(wrapper)

    assert exc.value.code == "hash-mismatch"


def test_fetch_reports_hash_mismatch_for_manifest_version_change(tmp_path):
    provider = _provider(tmp_path)
    request = _envelope()
    payload = provider.provide(request)
    manifest = copy.deepcopy(payload["delivery"]["manifest"])
    manifest["entries"][0]["content_version"] = "sha256:" + "0" * 64
    manifest["sha256"] = _manifest_digest(manifest)

    with pytest.raises(TaskMemoryProviderError) as exc:
        provider.fetch({"envelope": request, "manifest": manifest, "note_id": "note-1"})

    assert exc.value.code == "hash-mismatch"
