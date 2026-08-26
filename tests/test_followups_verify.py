import json
import os
from pathlib import Path

from paulsha_hippo import cli
from paulsha_hippo import followups as fu
from paulsha_hippo.importer import registry

NOW = "2026-08-25T00:00:00Z"


def _ledger_lines(root: Path) -> list[str]:
    return fu.ledger_path(root).read_text(encoding="utf-8").splitlines()


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
    # 2 筆 verified-open ＋ 2 筆 unverifiable 都還沒解決；只有 resolved-in-source
    # 的 fu-c 離開 open_count（unverifiable 是可重查狀態，見 OPEN_STATES）。
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 4
    # review round 1, finding 5: distinct `reason` per unverifiable cause, surfaced via fold().
    assert st["fu-d"]["detail"]["reason"] == "missing-file"
    assert st["fu-e"]["detail"]["reason"] == "no-expected-stale"
    assert "reason" not in st["fu-a"].get("detail", {})
    assert "reason" not in st["fu-c"].get("detail", {})


def test_close_manual_and_missing_root(tmp_path):
    _open(tmp_path, "fu-a", "README-ARC.md", 1, "x", project="unknown-proj")
    s = fu.verify(tmp_path, roots_by_project={}, now=NOW)
    assert s["unverifiable"] == 1
    assert fu.fold(tmp_path)["fu-a"]["detail"]["reason"] == "no-root"
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
    assert fu.fold(tmp_path)["fu-esc"]["detail"]["reason"] == "path-escape"


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
    assert fu.fold(tmp_path)["fu-far"]["detail"]["reason"] == "line-out-of-range"


# --- review round 1 -----------------------------------------------------------------


def test_fold_orders_events_by_timestamp_not_append_position(tmp_path):
    """同一 id 的事件依 (ts, original_index) 排序後折疊——即使『verified-open』事件
    append 在『resolved-in-source』之後，只要它的 ts 較早，仍不得贏過 ts 較晚的
    resolved-in-source（比照 ledger/processing.py 的 _fold_indexed／fold_events 慣例：
    sort by (ts, original_index), later ts wins even if appended earlier）。
    """
    fu.append_event(tmp_path, {"id": "fu-x", "event": "opened", "slice_id": "sl-1",
                               "project": "ot-ti-mirror", "target": {"path": "a.md", "line": 1},
                               "expected_stale": "v", "claim": "c", "source": "regex"},
                    now="2026-08-25T00:00:00Z")
    fu.append_event(tmp_path, {"id": "fu-x", "event": "resolved-in-source", "detail": {}},
                    now="2026-08-25T00:10:00Z")
    fu.append_event(tmp_path, {"id": "fu-x", "event": "verified-open", "detail": {}},
                    now="2026-08-25T00:05:00Z")
    st = fu.fold(tmp_path)
    assert st["fu-x"]["state"] == "resolved-in-source"
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 0


def test_verify_nul_byte_path_is_unverifiable_not_raise(tmp_path):
    """target.path 含 NUL byte（`\\x00`）：`Path.resolve()` 對這種字串丟的是 ValueError
    不是 OSError，`_resolve` 原本只接 `except OSError` 會漏接，讓整輪 verify 崩掉
    （T11 leftover minor）。必須跟其他圍籬失敗一樣歸 unverifiable／path-escape，不 raise。
    """
    repo = tmp_path / "repo"; repo.mkdir()
    _open(tmp_path, "fu-nul", "a\x00b.md", 1, "x")
    s = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW)
    assert s == {"checked": 1, "verified_open": 0, "resolved": 0, "unverifiable": 1}
    st = fu.fold(tmp_path)
    assert st["fu-nul"]["state"] == "unverifiable"
    assert st["fu-nul"]["detail"]["reason"] == "path-escape"


def test_verify_absolute_path_outside_roots_is_path_escape(tmp_path):
    """絕對路徑 cite 也必須落在其中一個 configured root 內；否則 unverifiable、
    reason: path-escape——不得繞過只有相對路徑才會走到的圍籬檢查（binding constraint 6）。
    """
    repo = tmp_path / "repo"; repo.mkdir()
    outside = tmp_path / "outside-abs.md"
    outside.write_text("133,604 stale value living outside any configured root\n", encoding="utf-8")
    _open(tmp_path, "fu-abs", str(outside), 1, "133,604")
    s = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW)
    assert s == {"checked": 1, "verified_open": 0, "resolved": 0, "unverifiable": 1}
    st = fu.fold(tmp_path)
    assert st["fu-abs"]["state"] == "unverifiable"
    assert st["fu-abs"]["detail"]["reason"] == "path-escape"


def test_verify_sequential_transitions_open_then_resolved(tmp_path):
    """同一 id：verify → verified-open；接著把目標行改成不再含過時值；再次 verify →
    resolved-in-source，open_count 隨之下降（唯讀重查，舊值消失才自動 close）。
    """
    repo = tmp_path / "repo"; repo.mkdir()
    note = repo / "note.md"
    note.write_text("line1\nstale value 133,604 B here\nline3\n", encoding="utf-8")
    _open(tmp_path, "fu-seq", "note.md", 2, "133,604 B")

    s1 = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW)
    assert s1["verified_open"] == 1
    assert fu.fold(tmp_path)["fu-seq"]["state"] == "verified-open"
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 1

    note.write_text("line1\nfresh value 133,372 B here\nline3\n", encoding="utf-8")
    s2 = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)},
                   now="2026-08-25T00:01:00Z")
    assert s2["resolved"] == 1
    assert fu.fold(tmp_path)["fu-seq"]["state"] == "resolved-in-source"
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 0


# --- T11 leftover minor: 端到端組合測試，只走 public functions ------------------------


def test_composed_extract_then_verify_open_then_edit_then_resolved(tmp_path):
    """用真實 knowledge note body（README-ARC.md:108／`133,604 B` 的過時值宣稱，跟
    `tests/test_followups_extract.py` 的 BODY fixture同一句）跑 `extract_all(apply=True)`
    真的開單 → `verify()` 先確認仍 open（source 裡值還在）→ 改掉 target 檔內容 →
    再 `verify()` 一次，確認自動轉成 resolved-in-source——全程只呼叫 public functions，
    不手動 `append_event` 塞 opened 事件。
    """
    body = ("README-ARC.md 記載的舊 FLASH 數字 `133,604 B` 已與 fresh build 的 `133,372 B` 不符，"
            "需要更新（`README-ARC.md:108`）。\n")
    k = tmp_path / "knowledge" / "ot-ti-mirror"; k.mkdir(parents=True)
    (k / "n--sl-e2e.md").write_text(
        "---\nslice_id: sl-e2e\nmemory_layer: knowledge\nproject: ot-ti-mirror\n---\n" + body,
        encoding="utf-8")

    s1 = fu.extract_all(tmp_path, apply=True, now=NOW)
    assert s1["opened"] == 1
    [fid] = list(fu.fold(tmp_path).keys())
    assert fu.fold(tmp_path)[fid]["state"] == "opened"

    repo = tmp_path / "repo"; repo.mkdir()
    readme = repo / "README-ARC.md"
    readme.write_text(
        "\n".join(f"line {i}" for i in range(1, 108)) + "\nFLASH usage: 133,604 B\n",
        encoding="utf-8")

    s2 = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW)
    assert s2["verified_open"] == 1
    assert fu.fold(tmp_path)[fid]["state"] == "verified-open"
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 1

    readme.write_text(
        "\n".join(f"line {i}" for i in range(1, 108)) + "\nFLASH usage: 133,372 B\n",
        encoding="utf-8")
    s3 = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)},
                   now="2026-08-25T00:01:00Z")
    assert s3["resolved"] == 1
    assert fu.fold(tmp_path)[fid]["state"] == "resolved-in-source"
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 0


# --- 全支線 review：`unverifiable` 是可重查狀態，不是終局 ---------------------------


def test_unverifiable_is_rechecked_and_recovers_when_root_appears(tmp_path):
    """`no-root`／`missing-file` 這類 unverifiable 只描述「這一輪查不到」，不是
    「這筆待辦不成立」。舊版把它排除在重查集合外，等於一次 dream run 撞到 roots
    還沒設定就讓該筆永久退場——root 補上之後再也不會被重查，靜默遺失。
    """
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "note.md").write_text("line1\nstale value 133,604 B here\nline3\n", encoding="utf-8")
    _open(tmp_path, "fu-nr", "note.md", 2, "133,604 B")

    s1 = fu.verify(tmp_path, roots_by_project={}, now=NOW)
    assert s1["unverifiable"] == 1
    st = fu.fold(tmp_path)
    assert st["fu-nr"]["state"] == "unverifiable" and st["fu-nr"]["detail"]["reason"] == "no-root"
    # 未解決就是未解決：unverifiable 必須計入 open_count（wakeup brief／KPI 的分子）。
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 1

    s2 = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)},
                   now="2026-08-25T00:01:00Z")
    assert s2 == {"checked": 1, "verified_open": 1, "resolved": 0, "unverifiable": 0}
    assert fu.fold(tmp_path)["fu-nr"]["state"] == "verified-open"


def test_unverifiable_recheck_can_resolve_in_source(tmp_path):
    """重查也可能直接關單：root 補上後過時值已經不在 → resolved-in-source。"""
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "note.md").write_text("line1\nfresh value 133,372 B here\nline3\n", encoding="utf-8")
    _open(tmp_path, "fu-res", "note.md", 2, "133,604 B")
    fu.verify(tmp_path, roots_by_project={}, now=NOW)
    s2 = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)},
                   now="2026-08-25T00:01:00Z")
    assert s2["resolved"] == 1
    assert fu.fold(tmp_path)["fu-res"]["state"] == "resolved-in-source"
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 0


def test_repeated_identical_unverifiable_reason_appends_nothing(tmp_path):
    """重查是每輪 dream 都會跑的；同一個原因每輪各記一筆會讓 append-only ledger
    以 dream 頻率無界成長，`fold` 的成本也跟著漲。結果與現況相同時不記事件。
    """
    repo = tmp_path / "repo"; repo.mkdir()
    roots = {"ot-ti-mirror": (str(repo),)}
    _open(tmp_path, "fu-dup", "nope.md", 1, "x")

    fu.verify(tmp_path, roots_by_project=roots, now=NOW)
    after_first = _ledger_lines(tmp_path)
    assert len(after_first) == 2                      # opened ＋ 第一次 unverifiable

    s2 = fu.verify(tmp_path, roots_by_project=roots, now="2026-08-25T00:01:00Z")
    assert _ledger_lines(tmp_path) == after_first     # 逐位元不變：完全沒 append
    # 仍然照實回報「這一輪查過、結果是 unverifiable」，只是沒有新事件。
    assert s2["checked"] == 1 and s2["unverifiable"] == 1
    assert s2["unverifiable_unchanged"] == 1
    assert fu.fold(tmp_path)["fu-dup"]["updated_at"] == NOW


def test_changed_unverifiable_reason_still_appends(tmp_path):
    """原因變了就是新資訊（no-root → missing-file），必須留下事件。"""
    repo = tmp_path / "repo"; repo.mkdir()
    _open(tmp_path, "fu-chg", "nope.md", 1, "x")
    fu.verify(tmp_path, roots_by_project={}, now=NOW)
    assert fu.fold(tmp_path)["fu-chg"]["detail"]["reason"] == "no-root"

    s2 = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)},
                   now="2026-08-25T00:01:00Z")
    assert s2["unverifiable"] == 1 and "unverifiable_unchanged" not in s2
    assert len(_ledger_lines(tmp_path)) == 3
    assert fu.fold(tmp_path)["fu-chg"]["detail"]["reason"] == "missing-file"


def test_verify_is_noop_when_followups_disabled(tmp_path):
    """spec：`followups.enabled` 關閉時 extract／verify／dream 階段 SHALL 為 no-op。
    先前只有 extract_all 有這道 gate，verify 照跑並照樣 append 事件。
    """
    repo = tmp_path / "repo"; repo.mkdir()
    _open(tmp_path, "fu-off", "nope.md", 1, "x")
    before = _ledger_lines(tmp_path)

    s = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW,
                  enabled=False)
    assert s["skipped"] == "followups.disabled"
    assert s["checked"] == 0
    assert _ledger_lines(tmp_path) == before


def test_cli_verify_honours_followups_disabled_flag(tmp_path, capsys):
    """CLI 這一層也要吃到 flag（gate 內建在 `followups.verify` 本身，比照 extract_all）。"""
    Path(os.environ["HIPPO_CONFIG_ROOT"], "config.yaml").write_text(
        "followups:\n  enabled: false\n", encoding="utf-8")
    repo = tmp_path / "repo"; repo.mkdir()
    _open(tmp_path, "fu-cli-off", "nope.md", 1, "x")
    before = _ledger_lines(tmp_path)

    assert cli.main(["followups", "verify", "--memory-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] == "followups.disabled"
    assert _ledger_lines(tmp_path) == before


def test_cli_verify_resolves_roots_from_registry_only_project(tmp_path, capsys, monkeypatch):
    """roots 解析要走 registry-aware 的 union 讀取：只登記在 generated registry
    （`project-hippo.yaml`）而沒進手寫 `projects.yaml` 的專案，先前一律拿不到 root，
    整批 follow-up 只會得到 `no-root`。
    """
    monkeypatch.setenv("PSC_CONFIG_ROOT", "")
    memory_root = tmp_path / "agents" / "memory"
    memory_root.mkdir(parents=True)
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "note.md").write_text("line1\nstale value 133,604 B here\nline3\n", encoding="utf-8")
    registry.record_discovery(slug="ot-ti-mirror", roots=[str(repo)],
                              registry_path=registry.default_registry_path(memory_root))
    _open(memory_root, "fu-reg", "note.md", 2, "133,604 B")

    assert cli.main(["followups", "verify", "--memory-root", str(memory_root)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "checked": 1, "verified_open": 1, "resolved": 0, "unverifiable": 0}


def test_fold_survives_non_utf8_ledger_bytes(tmp_path):
    """撕裂寫入／外部工具塞進非 UTF-8 bytes 時 `read_text` 丟的是 UnicodeDecodeError
    （ValueError 子類，不是 OSError）。`fold` 是每個讀取端的入口（open_count／list／
    verify／wakeup brief／KPI），漏接等於整條 follow-up 路徑被一個壞 byte 炸掉。
    """
    path = fu.ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"id": "fu-1", "event": "opened"}\n\xff\xfe\xfa\n')
    assert fu.fold(tmp_path) == {}
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 0
