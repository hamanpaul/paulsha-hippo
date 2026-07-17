import json
from pathlib import Path
from paulsha_hippo.importer.adapters import base
from paulsha_hippo.importer.adapters import claude as claude_adapter
from paulsha_hippo.importer.adapters import codex as codex_adapter
from paulsha_hippo.importer.adapters import copilot as copilot_adapter

FIX = Path(__file__).parent / "fixtures"


def test_read_claude_transcript_extracts_prompts_summary_touched():
    out = base.read_claude_transcript(FIX / "claude_transcript.jsonl")
    assert out["user_prompts"] == ["幫我修 UART 升級流程"]
    assert out["assistant_summary"] == "已修好 UART 升級流程並加上重試。"
    assert out["touched_files"] == ["/repo/uart.py"]  # deduped, order-preserved


def test_read_claude_transcript_missing_file_is_empty():
    out = base.read_claude_transcript(FIX / "does_not_exist.jsonl")
    assert out == {
        "user_prompts": [],
        "assistant_messages": [],
        "assistant_summary": "",
        "touched_files": [],
    }


def test_read_copilot_history_extracts_from_chatmessages(tmp_path):
    d = tmp_path / ".copilot" / "history-session-state"
    d.mkdir(parents=True)
    (d / "session_cop-1_123.json").write_text((FIX / "copilot_history.json").read_text(), encoding="utf-8")
    out = base.read_copilot_history(tmp_path, "cop-1")
    assert out["user_prompts"] == ["列出 PON HLAPI"]
    assert out["assistant_summary"] == "已整理 PON HLAPI 對照表。"


def test_read_copilot_history_accepts_contract_config_root(tmp_path):
    d = tmp_path / ".copilot" / "history-session-state"
    d.mkdir(parents=True)
    (d / "session_cop-1_123.json").write_text((FIX / "copilot_history.json").read_text(), encoding="utf-8")
    config_root = tmp_path / ".config" / "paulshaclaw"
    config_root.mkdir(parents=True)

    out = base.read_copilot_history(config_root, "cop-1")

    assert out["user_prompts"] == ["列出 PON HLAPI"]
    assert out["assistant_summary"] == "已整理 PON HLAPI 對照表。"


def test_read_codex_rollout_extracts_prompts_and_empty_assistant_contract():
    out = base.read_codex_rollout(FIX / "codex_rollout.jsonl")
    assert out["user_prompts"] == ["請幫我產生 codex 範例"]
    assert out["assistant_messages"] == []
    assert out["assistant_summary"] == ""


def test_claude_adapter_enriches_from_transcript(tmp_path):
    payload = {"tool": "claude-code", "session_id": "s1", "cwd": "/repo",
               "transcript_path": str(FIX / "claude_transcript.jsonl")}
    qp = tmp_path / "q.json"
    qp.write_text(json.dumps(payload), encoding="utf-8")
    result = claude_adapter.extract(qp)
    assert result.session["user_prompts"] == ["幫我修 UART 升級流程"]
    assert result.session["touched_files"] == ["/repo/uart.py"]
    assert result.session["assistant_summary"] == "已修好 UART 升級流程並加上重試。"


def test_codex_adapter_uses_last_assistant_message_and_rollout_prompts(tmp_path):
    payload = {"tool": "codex", "session_id": "cdx1", "cwd": "/repo",
               "last_assistant_message": "已完成 codex 任務摘要。",
               "transcript_path": str(FIX / "codex_rollout.jsonl")}
    qp = tmp_path / "q.json"
    qp.write_text(json.dumps(payload), encoding="utf-8")
    result = codex_adapter.extract(qp)
    assert result.session["assistant_summary"] == "已完成 codex 任務摘要。"
    assert result.session["user_prompts"] == ["請幫我產生 codex 範例"]


def test_copilot_adapter_enriches_from_history(tmp_path):
    hist = tmp_path / ".copilot" / "history-session-state"
    hist.mkdir(parents=True)
    (hist / "session_cop-1_9.json").write_text((FIX / "copilot_history.json").read_text(), encoding="utf-8")
    payload = {"tool": "copilot-cli", "sessionId": "cop-1", "cwd": "/repo",
               "psc_config_root": str(tmp_path)}
    qp = tmp_path / "q.json"
    qp.write_text(json.dumps(payload), encoding="utf-8")
    result = copilot_adapter.extract(qp)
    assert result.session["user_prompts"] == ["列出 PON HLAPI"]
    assert result.session["assistant_summary"] == "已整理 PON HLAPI 對照表。"


def test_claude_adapter_missing_transcript_keeps_inline_content(tmp_path):
    # A dead/empty transcript pointer must NOT wipe inline payload content.
    payload = {"tool": "claude-code", "session_id": "s2", "cwd": "/repo",
               "transcript_path": "/nonexistent.jsonl",
               "touched_files": ["a.py"], "user_prompts": ["inline q"]}
    qp = tmp_path / "q.json"
    qp.write_text(json.dumps(payload), encoding="utf-8")
    result = claude_adapter.extract(qp)
    assert result.session["touched_files"] == ["a.py"]
    assert result.session["user_prompts"] == ["inline q"]


def test_end_to_end_inbox_has_content_and_title(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "paulsha_hippo.importer.title._default_runner",
        lambda t, c, to: "端到端標題",
    )
    from paulsha_hippo.importer import pipeline

    qp = tmp_path / "q.json"
    qp.write_text(json.dumps({"tool": "claude-code", "session_id": "e2e", "cwd": "/repo",
                              "transcript_path": str(FIX / "claude_transcript.jsonl")}), encoding="utf-8")
    pipeline.ingest_queue_item(qp, memory_root=tmp_path, dry_run=False)
    md = list((tmp_path / "inbox").rglob("*.md"))[0].read_text(encoding="utf-8")
    assert "title: 端到端標題" in md
    assert "## Summary\n已修好 UART 升級流程並加上重試。" in md
    assert "## Conversation\n1. 已修好 UART 升級流程並加上重試。" in md
    assert "1. 幫我修 UART 升級流程" in md
    assert "- /repo/uart.py" in md
