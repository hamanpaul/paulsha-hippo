"""offered -> read -> applied 全鏈 hermetic 驗證（無真 CLI、無 credential，進 CI）。

fake hook harness：以假 hook stdin payload 直接跑 hook 腳本入口——
  A. UserPromptSubmit payload -> claude_user_prompt_submit.py -> shortlist 注入 + offered 記錄
  B. PostToolUse(Read) payload -> claude_post_tool_use.py -> read 事件（offered=true、同 session）
  C. shortlist 尾行指引對應的 mark-applied -> applied 事件（參照完整性驗證通過）
  D. hippo usage --json -> by_tool 漏斗三欄齊備
copilot 對稱鏈（capability matrix 2026-07-11 復測 supported 後接線）：
  userPromptSubmitted payload -> copilot_user_prompt_submit.py；
  postToolUse(view) payload -> copilot_post_tool_use.py；payload 形以實測為準
  （camelCase sessionId、toolArgs 為 JSON 字串）。
live credentialed 腳本（tests/cross_cli_live_check.sh）僅為補充證據；本測試是 #18 的 CI 迴歸保護。
"""

import json
import subprocess
import sys
from pathlib import Path

from paulsha_hippo import cli

PROMPT_HOOK = Path("paulsha_hippo/hooks/claude_user_prompt_submit.py").resolve()
READ_HOOK = Path("paulsha_hippo/hooks/claude_post_tool_use.py").resolve()
COPILOT_PROMPT_HOOK = Path("paulsha_hippo/hooks/copilot_user_prompt_submit.py").resolve()
COPILOT_READ_HOOK = Path("paulsha_hippo/hooks/copilot_post_tool_use.py").resolve()


def _seed(mr: Path) -> Path:
    from paulsha_hippo.moc import search as S

    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    note = k / "a.md"
    note.write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8",
    )
    S.build_index(mr, link_weights={})
    return note


def _run_hook(hook: Path, mr: Path, payload: dict) -> dict:
    env = {
        "PSC_MEMORY_ROOT": str(mr),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path.cwd()),
    }
    p = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def _events(mr: Path, name: str) -> list[dict]:
    f = mr / "runtime" / "ledger" / name
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_funnel_offered_read_applied_full_chain(tmp_path, capsys):
    note = _seed(tmp_path)
    proj_cwd = tmp_path / "proj"
    proj_cwd.mkdir(exist_ok=True)
    sid = "it-funnel-1"

    # A. 模擬 UserPromptSubmit：shortlist 注入 + offered 記錄（fake stdin payload）
    out = _run_hook(
        PROMPT_HOOK,
        tmp_path,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": sid,
            "cwd": str(proj_cwd),
            "prompt": "SerialWrap 執行",
        },
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert str(note) in ctx and "Read" in ctx
    assert "usage mark-applied" in ctx  # 尾行回報指引（Task 3）
    offered = _events(tmp_path, "offered.jsonl")
    assert len(offered) == 1
    assert offered[0]["tool"] == "claude-code" and offered[0]["session_id"] == sid
    assert offered[0]["offered"] == [{"sl_id": "sl-aaaaaaaaaaaaaaaa", "path": str(note)}]

    # B. 模擬 PostToolUse(Read)：read 事件（offered=true、同 session 綁定）
    _run_hook(
        READ_HOOK,
        tmp_path,
        {
            "hook_event_name": "PostToolUse",
            "session_id": sid,
            "tool_name": "Read",
            "tool_input": {"file_path": str(note)},
        },
    )
    reads = [e for e in _events(tmp_path, "memory_usage.jsonl") if e.get("source") == "read"]
    assert len(reads) == 1
    assert reads[0]["offered"] is True and reads[0]["session_id"] == sid
    assert reads[0]["sl_id"] == "sl-aaaaaaaaaaaaaaaa"

    # C. mark-applied（同 session/tool/slice -> 參照完整性驗證通過）
    rc = cli.main(
        [
            "usage",
            "mark-applied",
            "--memory-root",
            str(tmp_path),
            "--session-id",
            sid,
            "--slice-id",
            "sl-aaaaaaaaaaaaaaaa",
            "--tool",
            "claude-code",
        ]
    )
    assert rc == 0
    applied = [e for e in _events(tmp_path, "memory_usage.jsonl") if e.get("kind") == "applied"]
    assert len(applied) == 1 and applied[0]["session_id"] == sid
    capsys.readouterr()  # 清掉 mark-applied 的 stdout 回顯

    # D. usage 報表：漏斗三欄齊備（offered / read / applied 全 1）
    assert cli.main(["usage", "--memory-root", str(tmp_path), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["by_tool"]["claude-code"] == {"offered": 1, "read": 1, "applied": 1}


def test_funnel_copilot_offered_read_applied_full_chain(tmp_path, capsys):
    """copilot adapter 對稱鏈：userPromptSubmitted -> postToolUse(view) -> mark-applied。"""
    note = _seed(tmp_path)
    proj_cwd = tmp_path / "proj"
    proj_cwd.mkdir(exist_ok=True)
    sid = "it-funnel-cp1"

    # A. userPromptSubmitted（實測 payload 形：camelCase sessionId）
    out = _run_hook(
        COPILOT_PROMPT_HOOK,
        tmp_path,
        {"sessionId": sid, "timestamp": 0, "cwd": str(proj_cwd), "prompt": "SerialWrap 執行"},
    )
    ctx = out["additionalContext"]
    assert str(note) in ctx and "usage mark-applied" in ctx
    offered = _events(tmp_path, "offered.jsonl")
    assert len(offered) == 1
    assert offered[0]["tool"] == "copilot-cli" and offered[0]["session_id"] == sid

    # B. postToolUse(view)（實測 payload 形：toolArgs 為 JSON 字串）
    _run_hook(
        COPILOT_READ_HOOK,
        tmp_path,
        {
            "sessionId": sid,
            "timestamp": 0,
            "cwd": str(proj_cwd),
            "toolName": "view",
            "toolArgs": json.dumps({"path": str(note), "view_range": [1, 5]}),
            "toolResult": {"resultType": "success", "textResultForLlm": "1. x"},
        },
    )
    reads = [e for e in _events(tmp_path, "memory_usage.jsonl") if e.get("source") == "read"]
    assert len(reads) == 1
    assert reads[0]["tool"] == "copilot-cli" and reads[0]["offered"] is True
    assert reads[0]["session_id"] == sid and reads[0]["sl_id"] == "sl-aaaaaaaaaaaaaaaa"

    # C+D. mark-applied 參照完整性通過 -> usage 漏斗三欄齊備
    rc = cli.main(
        ["usage", "mark-applied", "--memory-root", str(tmp_path),
         "--session-id", sid, "--slice-id", "sl-aaaaaaaaaaaaaaaa", "--tool", "copilot-cli"]
    )
    assert rc == 0
    capsys.readouterr()
    assert cli.main(["usage", "--memory-root", str(tmp_path), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["by_tool"]["copilot-cli"] == {"offered": 1, "read": 1, "applied": 1}


def test_funnel_forged_applied_rejected_end_to_end(tmp_path, capsys):
    # 全鏈情境下的偽造拒絕：真的 offer 過的 session，換一個未 offer 的 slice -> 拒寫
    _seed(tmp_path)
    proj_cwd = tmp_path / "proj"
    proj_cwd.mkdir(exist_ok=True)
    _run_hook(
        PROMPT_HOOK,
        tmp_path,
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "it-funnel-2",
            "cwd": str(proj_cwd),
            "prompt": "SerialWrap 執行",
        },
    )
    rc = cli.main(
        [
            "usage",
            "mark-applied",
            "--memory-root",
            str(tmp_path),
            "--session-id",
            "it-funnel-2",
            "--slice-id",
            "sl-forged0000000001",
            "--tool",
            "claude-code",
        ]
    )
    assert rc == 1
    assert not any(e.get("kind") == "applied" for e in _events(tmp_path, "memory_usage.jsonl"))
