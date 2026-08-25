from pathlib import Path
from paulsha_hippo import followups as fu

NOW = "2026-08-25T00:00:00Z"


def _open(root: Path, fid: str, path: str, line: int, stale: str | None, project="ot-ti-mirror"):
    fu.append_event(root, {"id": fid, "event": "opened", "slice_id": "sl-1", "project": project,
                           "target": {"path": path, "line": line}, "expected_stale": stale, "claim": "c", "source": "regex"}, now=NOW)


def test_verify_state_transitions(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    readme = repo / "README-ARC.md"
    readme.write_text("\n".join(f"line {i}" for i in range(1, 108)) + "\nmemory report (FLASH 133,604 B)\nx\n", encoding="utf-8")
    _open(tmp_path, "fu-a", "README-ARC.md", 108, "133,604")   # 實際在第 108 行 → verified-open
    _open(tmp_path, "fu-b", "README-ARC.md", 110, "133,604")   # 漂移 ±2 內 → 仍找到
    _open(tmp_path, "fu-c", "README-ARC.md", 50, "133,604")    # 不在 → resolved
    _open(tmp_path, "fu-d", "nope.md", 1, "x")                 # 檔案不存在 → unverifiable
    _open(tmp_path, "fu-e", "README-ARC.md", 108, None)        # 無預期值 → unverifiable
    mtime = readme.stat().st_mtime_ns
    s = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW)
    assert s == {"checked": 5, "verified_open": 2, "resolved": 1, "unverifiable": 2}
    st = fu.fold(tmp_path)
    assert st["fu-a"]["state"] == "verified-open" and st["fu-c"]["state"] == "resolved-in-source"
    assert readme.stat().st_mtime_ns == mtime
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 2


def test_close_manual_and_missing_root(tmp_path):
    _open(tmp_path, "fu-a", "README-ARC.md", 1, "x", project="unknown-proj")
    s = fu.verify(tmp_path, roots_by_project={}, now=NOW)
    assert s["unverifiable"] == 1
    assert fu.close(tmp_path, "fu-a", reason="moved to generated", now=NOW) is True
    assert fu.close(tmp_path, "fu-zz", reason="x", now=NOW) is False
    assert fu.fold(tmp_path)["fu-a"]["state"] == "closed-manual"


def test_verify_path_escape_stays_unverifiable_not_raise(tmp_path):
    """相對 target path 逃出 project root（`../outside.md`）：不得讀出 root 外的檔案，
    也不得 raise——即使該檔真的存在且含 expected_stale 字樣，仍歸 unverifiable
    （binding constraint：is_relative_to 圍籬，比照 recovery.py／provenance_backfill.py）。
    """
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "README-ARC.md").write_text("in-root\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("133,604 leaked from outside the project root\n", encoding="utf-8")
    _open(tmp_path, "fu-esc", "../outside.md", 1, "133,604")
    s = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW)
    assert s == {"checked": 1, "verified_open": 0, "resolved": 0, "unverifiable": 1}
    assert fu.fold(tmp_path)["fu-esc"]["state"] == "unverifiable"


def test_verify_line_far_past_eof_is_unverifiable_not_falsely_resolved(tmp_path):
    """target.line 遠超出檔案目前行數（裁切後 ±window 視窗為空、0 行可比對）：
    不得誤判為「過時值已不在」而自動 resolved-in-source（那是偽陽性的自動關閉，
    真正原因是檔案被大幅精簡、referenced 內容根本不在了，查無結果才是誠實狀態）。
    """
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "a.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _open(tmp_path, "fu-far", "a.md", 500, "zzz")
    s = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW)
    assert s == {"checked": 1, "verified_open": 0, "resolved": 0, "unverifiable": 1}
    assert fu.fold(tmp_path)["fu-far"]["state"] == "unverifiable"
