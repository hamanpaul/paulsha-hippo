# tests/test_usage_mark_applied.py - applied 顯式訊號（契約 8＋參照完整性 anti-forgery）
import json
from pathlib import Path

from paulsha_hippo import cli


def _seed_offered(
    mr: Path,
    session_id: str = "s1",
    tool: str = "claude-code",
    slice_ids: tuple[str, ...] = ("sl-aaaaaaaaaaaaaaaa",),
):
    """寫一筆 offered 事件（schema 同 _record_offered）——mark-applied 驗證的反查來源。"""
    led = mr / "runtime" / "ledger"
    led.mkdir(parents=True, exist_ok=True)
    ev = {
        "ts": "2026-07-10T00:00:00Z",
        "session_id": session_id,
        "tool": tool,
        "project": "p",
        "offered": [{"sl_id": s, "path": f"/k/{s}.md"} for s in slice_ids],
    }
    with (led / "offered.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev) + "\n")


def _usage_ledger(mr: Path) -> Path:
    return mr / "runtime" / "ledger" / "memory_usage.jsonl"


def test_mark_applied_appends_contract_event(tmp_path, capsys):
    _seed_offered(tmp_path)  # 先行 offered：s1 / claude-code / sl-aaaaaaaaaaaaaaaa
    rc = cli.main(
        [
            "usage",
            "mark-applied",
            "--memory-root",
            str(tmp_path),
            "--session-id",
            "s1",
            "--slice-id",
            "sl-aaaaaaaaaaaaaaaa",
            "--tool",
            "claude-code",
        ]
    )
    assert rc == 0
    lines = _usage_ledger(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["kind"] == "applied"
    assert ev["slice_id"] == "sl-aaaaaaaaaaaaaaaa"
    assert ev["session_id"] == "s1"
    assert ev["tool"] == "claude-code"
    assert ev["ts"]  # ISO timestamp 非空


def test_mark_applied_appends_not_truncates(tmp_path, capsys):
    _seed_offered(tmp_path, session_id="s1", tool="codex", slice_ids=("sl-y",))
    _usage_ledger(tmp_path).write_text(
        json.dumps(
            {
                "ts": "2026-07-10T00:00:00Z",
                "session_id": "s0",
                "tool": "claude-code",
                "project": "p",
                "sl_id": "sl-x",
                "path": "/k/x.md",
                "source": "read",
                "offered": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rc = cli.main(
        [
            "usage",
            "mark-applied",
            "--memory-root",
            str(tmp_path),
            "--session-id",
            "s1",
            "--slice-id",
            "sl-y",
            "--tool",
            "codex",
        ]
    )
    assert rc == 0
    lines = _usage_ledger(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["source"] == "read"


# ---- 參照完整性 negative cases：偽造 applied 一律拒絕（exit 1、不寫入、stderr 說明）----


def test_mark_applied_rejects_unknown_session(tmp_path, capsys):
    _seed_offered(tmp_path, session_id="s1")
    rc = cli.main(
        [
            "usage",
            "mark-applied",
            "--memory-root",
            str(tmp_path),
            "--session-id",
            "s-forged",
            "--slice-id",
            "sl-aaaaaaaaaaaaaaaa",
            "--tool",
            "claude-code",
        ]
    )
    assert rc == 1
    assert "offered" in capsys.readouterr().err
    assert not _usage_ledger(tmp_path).exists()  # 偽造事件未落 ledger


def test_mark_applied_rejects_unknown_slice(tmp_path, capsys):
    _seed_offered(tmp_path, slice_ids=("sl-aaaaaaaaaaaaaaaa",))
    rc = cli.main(
        [
            "usage",
            "mark-applied",
            "--memory-root",
            str(tmp_path),
            "--session-id",
            "s1",
            "--slice-id",
            "sl-neveroffered0001",
            "--tool",
            "claude-code",
        ]
    )
    assert rc == 1
    assert "slice_id" in capsys.readouterr().err
    assert not _usage_ledger(tmp_path).exists()


def test_mark_applied_rejects_tool_mismatch(tmp_path, capsys):
    _seed_offered(tmp_path, tool="claude-code")
    rc = cli.main(
        [
            "usage",
            "mark-applied",
            "--memory-root",
            str(tmp_path),
            "--session-id",
            "s1",
            "--slice-id",
            "sl-aaaaaaaaaaaaaaaa",
            "--tool",
            "codex",
        ]
    )  # 同 session/slice、tool 不符 -> 拒
    assert rc == 1
    assert "offered" in capsys.readouterr().err
    assert not _usage_ledger(tmp_path).exists()


def test_mark_applied_rejects_when_no_offered_ledger(tmp_path, capsys):
    # 全新 memory root、無任何 offer——「任何 shell agent 盲寫假事件」的原始攻擊面
    rc = cli.main(
        [
            "usage",
            "mark-applied",
            "--memory-root",
            str(tmp_path),
            "--session-id",
            "s1",
            "--slice-id",
            "sl-aaaaaaaaaaaaaaaa",
            "--tool",
            "claude-code",
        ]
    )
    assert rc == 1
    assert "offered" in capsys.readouterr().err
    assert not _usage_ledger(tmp_path).exists()


def test_usage_without_memory_root_errors_exit2(tmp_path, capsys):
    assert cli.main(["usage"]) == 2
    assert "memory-root" in capsys.readouterr().err


def test_usage_report_still_works_without_subcommand(tmp_path, capsys):
    assert cli.main(["usage", "--memory-root", str(tmp_path), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["summary"]["sessions"] == 0
