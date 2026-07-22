from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paulsha_hippo.importer import pipeline, title
from paulsha_hippo.importer.adapters import base
from paulsha_hippo.importer.frontmatter import render_markdown
from paulsha_hippo.importer.sanitizer import SanitizationError


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tool": "codex",
        "session_id": "capture-contract",
        "capture_scope": "turn",
        "capture_id": "cap-1",
        "parent_session_id": None,
        "user_prompts": ["first prompt", "second prompt"],
        "assistant_messages": ["first outcome", "second outcome"],
        "assistant_summary": "second outcome",
        "touched_files": ["b.py", "a.py"],
        "referenced_artifacts": ["docs/spec.md"],
        "turn_count": 2,
    }
    payload.update(overrides)
    return payload


def test_claude_transcript_preserves_all_ordered_full_assistant_messages(tmp_path):
    long_outcome = "x" * 2501
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "question"}}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": long_outcome}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "final outcome"}]},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = base.read_claude_transcript(transcript)

    assert result["assistant_messages"] == [long_outcome, "final outcome"]
    assert result["assistant_summary"] == "final outcome"
    assert len(result["assistant_messages"][0]) == 2501


def test_legacy_capture_id_is_raw_payload_sha256(tmp_path):
    queue = tmp_path / "legacy.json"
    raw = b'{"tool":"codex","session_id":"legacy","assistant_summary":"done"}\n'
    queue.write_bytes(raw)
    payload = json.loads(raw)

    result = base.build_session(
        payload=payload,
        queue_path=queue,
        tool="codex",
        session_id="legacy",
        default_capture_scope="turn",
        ended_at=None,
    )

    assert result.session["capture_id"] == hashlib.sha256(raw).hexdigest()
    assert result.session["parent_session_id"] is None


def test_title_apply_preserves_semantic_outcomes_and_keys_cache_by_inputs(tmp_path):
    calls: list[str] = []

    def runner(prompt: str, command: tuple[str, ...], timeout: int) -> str:
        del command, timeout
        calls.append(prompt)
        return f"title-{len(calls)}"

    first = _payload(session_id="same", assistant_messages=["outcome-a"], assistant_summary="outcome-a")
    second = _payload(session_id="same", assistant_messages=["outcome-b"], assistant_summary="outcome-b")

    titled_a = title.apply(first, memory_root=tmp_path, runner=runner)
    titled_b = title.apply(second, memory_root=tmp_path, runner=runner)

    assert titled_a["session_title"] == "title-1"
    assert titled_a["assistant_messages"] == ["outcome-a"]
    assert titled_a["assistant_summary"] == "outcome-a"
    assert titled_b["session_title"] == "title-2"
    assert len(calls) == 2


def test_semantic_hash_covers_ordered_prompts_and_full_outcomes_but_not_json_key_order():
    session_a = _payload()
    session_b = dict(reversed(list(session_a.items())))
    session_changed = _payload(assistant_messages=["first outcome", "changed outcome"], assistant_summary="changed outcome")

    assert pipeline.content_hash(session_a, "turn") == pipeline.content_hash(session_b, "turn")
    assert pipeline.content_hash(session_a, "turn") != pipeline.content_hash(session_changed, "turn")


def test_different_capture_with_changed_content_updates_even_if_completeness_is_lower(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "paulsha_hippo.importer.title._default_runner",
        lambda prompt, command, timeout: "capture title",
    )
    queue = tmp_path / "runtime" / "queue"
    queue.mkdir(parents=True)

    first_payload = _payload(
        tool="copilot-cli",
        capture_id="cap-rich",
        turn_count=9,
        user_prompts=["same prompt"],
        assistant_messages=["old outcome"],
        assistant_summary="old outcome",
    )
    second_payload = _payload(
        tool="copilot-cli",
        capture_id="cap-new",
        turn_count=1,
        user_prompts=["same prompt"],
        assistant_messages=["new outcome"],
        assistant_summary="new outcome",
    )
    first = queue / "first.json"
    second = queue / "second.json"
    first.write_text(json.dumps(first_payload), encoding="utf-8")
    second.write_text(json.dumps(second_payload), encoding="utf-8")

    first_result = pipeline.ingest_queue_item(first, memory_root=tmp_path)
    second_result = pipeline.ingest_queue_item(second, memory_root=tmp_path)

    assert first_result["status"] == "written"
    assert second_result["status"] == "updated"
    assert first_result["idempotency_key"].endswith(":cap-rich")
    assert second_result["idempotency_key"].endswith(":cap-new")
    rendered = next((tmp_path / "inbox").rglob("*.md")).read_text(encoding="utf-8")
    assert "new outcome" in rendered


def test_different_capture_with_same_semantics_is_hash_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "paulsha_hippo.importer.title._default_runner",
        lambda prompt, command, timeout: "capture title",
    )
    queue = tmp_path / "runtime" / "queue"
    queue.mkdir(parents=True)
    first_payload = _payload(tool="copilot-cli", capture_id="cap-a")
    second_payload = _payload(tool="copilot-cli", capture_id="cap-b")
    first = queue / "first.json"
    second = queue / "second.json"
    first.write_text(json.dumps(first_payload), encoding="utf-8")
    second.write_text(json.dumps(second_payload), encoding="utf-8")

    first_result = pipeline.ingest_queue_item(first, memory_root=tmp_path)
    second_result = pipeline.ingest_queue_item(second, memory_root=tmp_path)

    assert first_result["status"] == "written"
    assert second_result["status"] == "hash-duplicate"
    assert first_result["idempotency_key"] != second_result["idempotency_key"]
    assert first_result["content_hash"] == second_result["content_hash"]


def test_older_capture_is_archived_without_replacing_newer_canonical_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "paulsha_hippo.importer.title._default_runner",
        lambda prompt, command, timeout: "capture title",
    )
    queue = tmp_path / "runtime" / "queue"
    queue.mkdir(parents=True)
    newer = queue / "newer.json"
    older = queue / "older.json"
    newer.write_text(
        json.dumps(
            _payload(
                capture_id="cap-newer",
                ended_at="2026-07-17T02:00:00Z",
                assistant_messages=["newer outcome"],
                assistant_summary="newer outcome",
            )
        ),
        encoding="utf-8",
    )
    older.write_text(
        json.dumps(
            _payload(
                capture_id="cap-older",
                ended_at="2026-07-17T01:00:00Z",
                assistant_messages=["older outcome"],
                assistant_summary="older outcome",
            )
        ),
        encoding="utf-8",
    )

    first_result = pipeline.ingest_queue_item(newer, memory_root=tmp_path)
    second_result = pipeline.ingest_queue_item(older, memory_root=tmp_path)

    assert first_result["status"] == "written"
    assert second_result["status"] == "stale-skip"
    assert Path(second_result["archive_path"]).is_file()
    rendered = Path(first_result["inbox_path"]).read_text(encoding="utf-8")
    assert "newer outcome" in rendered
    assert "older outcome" not in rendered


def test_sanitizer_failure_keeps_raw_queue_and_writes_no_derived_artifact(tmp_path, monkeypatch):
    queue = tmp_path / "runtime" / "queue"
    queue.mkdir(parents=True)
    item = queue / "raw.json"
    item.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setattr(
        "paulsha_hippo.importer.pipeline.sanitize_session",
        lambda session: (_ for _ in ()).throw(SanitizationError("sanitizer down")),
    )

    with pytest.raises(pipeline.PipelineError):
        pipeline.ingest_queue_item(item, memory_root=tmp_path)

    assert item.exists()
    assert not (tmp_path / "archive").exists()
    assert not (tmp_path / "inbox").exists()


def test_frontmatter_uses_title_but_conversation_keeps_all_outcomes():
    session = _payload(session_title="specific title", title_source="external-agent")

    markdown = render_markdown(session, project="paulsha-hippo")

    assert "title: specific title" in markdown
    assert "## Summary\nsecond outcome" in markdown
    assert "## Conversation\n1. first outcome\n2. second outcome" in markdown
