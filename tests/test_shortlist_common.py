# tests/test_shortlist_common.py
import json
import multiprocessing
from pathlib import Path

import pytest

from paulsha_hippo.moc import search as S
from paulsha_hippo.hooks import _shortlist_common as SC


def _seed(mr: Path):
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={})


def test_shortlist_injects_and_records_offered(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sid1", cwd="/x", prompt="SerialWrap 執行")
    note = str(tmp_path / "knowledge" / "proj" / "a.md")
    assert note in out and "Read" in out
    # offered ledger
    led = (tmp_path / "runtime" / "ledger" / "offered.jsonl").read_text(encoding="utf-8")
    assert "sl-aaaaaaaaaaaaaaaa" in led and note in led
    # per-session map accumulates both directions
    m = json.loads((tmp_path / "runtime" / "wakeup" / "claude-code__sid1.offered.json").read_text())
    assert m["by_path"][note] == "sl-aaaaaaaaaaaaaaaa"
    assert m["by_id"]["sl-aaaaaaaaaaaaaaaa"] == note


def test_shortlist_skips_slash_command(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    assert SC.build_shortlist_and_record(tmp_path, "claude-code", "s", cwd="/x", prompt="/effort ultra") == ""


def test_shortlist_unknown_project_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "_unknown")
    assert SC.build_shortlist_and_record(tmp_path, "claude-code", "s", cwd="/x", prompt="anything") == ""


def test_shortlist_no_match_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    assert SC.build_shortlist_and_record(tmp_path, "claude-code", "s", cwd="/x", prompt="zzzznomatch") == ""


def test_shortlist_fails_closed_when_redaction_raises(tmp_path, monkeypatch):
    # If the boundary/redaction check is unavailable, NO un-redacted memory text is
    # injected and NOTHING is recorded as offered (fail-closed).
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    import paulsha_hippo.policy as pol

    def _boom(*a, **k):
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(pol, "check_boundary", _boom)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidX", cwd="/x", prompt="SerialWrap 執行")
    assert out == ""  # no shortlist injected
    assert not (tmp_path / "runtime" / "ledger" / "offered.jsonl").exists()
    assert not (tmp_path / "runtime" / "wakeup" / "claude-code__sidX.offered.json").exists()


def _seed_two(mr: Path):
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8")
    (k / "b.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-bbbbbbbbbbbbbbbb\nproject: proj\n"
        "title: SerialWrap 進階\ncaptured_at: '2026-06-29T00:01:00Z'\n---\nSerialWrap 執行注意事項\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={})


def _offered_events(mr: Path):
    path = mr / "runtime" / "ledger" / "offered.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_shortlist_session_dedup_next_best_then_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    monkeypatch.setattr(SC, "SHORTLIST_K", 1)
    _seed_two(tmp_path)

    out1 = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidD", cwd="/x", prompt="SerialWrap 執行")
    out2 = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidD", cwd="/x", prompt="SerialWrap 執行")
    out3 = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidD", cwd="/x", prompt="SerialWrap 執行")

    assert out1 != ""
    assert out2 != ""
    assert out3 == ""

    events = _offered_events(tmp_path)
    assert len(events) == 2
    ids1 = {item["sl_id"] for item in events[0]["offered"]}
    ids2 = {item["sl_id"] for item in events[1]["offered"]}
    assert ids1 != ids2
    assert ids1 | ids2 == {"sl-aaaaaaaaaaaaaaaa", "sl-bbbbbbbbbbbbbbbb"}


def test_shortlist_dedup_scoped_to_session(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    monkeypatch.setattr(SC, "SHORTLIST_K", 1)
    _seed_two(tmp_path)

    out1 = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidE", cwd="/x", prompt="SerialWrap 執行")
    out2 = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidF", cwd="/x", prompt="SerialWrap 執行")
    note = str(tmp_path / "knowledge" / "proj" / "b.md")
    expected = [{"sl_id": "sl-bbbbbbbbbbbbbbbb", "path": note}]

    assert out1 != ""
    assert out2 != ""
    events = _offered_events(tmp_path)
    assert [event["session_id"] for event in events] == ["sidE", "sidF"]
    assert events[0]["offered"] == expected
    assert events[1]["offered"] == expected

    sid_e_map = json.loads((tmp_path / "runtime" / "wakeup" / "claude-code__sidE.offered.json").read_text())
    sid_f_map = json.loads((tmp_path / "runtime" / "wakeup" / "claude-code__sidF.offered.json").read_text())
    expected_map = {"by_path": {note: "sl-bbbbbbbbbbbbbbbb"}, "by_id": {"sl-bbbbbbbbbbbbbbbb": note}}
    assert sid_e_map == expected_map
    assert sid_f_map == expected_map


def test_shortlist_dedup_fail_open_on_corrupt_map(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    monkeypatch.setattr(SC, "SHORTLIST_K", 1)
    _seed_two(tmp_path)

    wakeup = tmp_path / "runtime" / "wakeup"
    wakeup.mkdir(parents=True)
    (wakeup / "claude-code__sidG.offered.json").write_text("{broken", encoding="utf-8")

    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidG", cwd="/x", prompt="SerialWrap 執行")

    assert out != ""


def test_summary_skips_title_echo_first_line(tmp_path):
    p = tmp_path / "n.md"
    p.write_text(
        "---\ntitle: overview\n---\n# Overview\n\nUART2 pinmux 設錯會靜默失效。\n",
        encoding="utf-8",
    )
    assert SC._summary(str(p), "overview") == "UART2 pinmux 設錯會靜默失效。"


def test_summary_all_title_echo_returns_empty(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("---\ntitle: review-summary\n---\n# Review Summary\n", encoding="utf-8")
    assert SC._summary(str(p), "review-summary") == ""


def test_summary_first_line_kept_when_not_echo(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("---\ntitle: x\n---\n具體結論第一行。\n", encoding="utf-8")
    assert SC._summary(str(p), "x") == "具體結論第一行。"


def test_shortlist_appends_applied_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(
        tmp_path, "claude-code", "sidA", cwd="/x", prompt="SerialWrap 執行"
    )
    assert "usage mark-applied" in out
    assert "--session-id sidA" in out and "--tool claude-code" in out
    assert f"--memory-root {tmp_path}" in out


def test_shortlist_empty_result_has_no_applied_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(
        tmp_path, "claude-code", "s", cwd="/x", prompt="zzzznomatch"
    )
    assert out == ""


def _offered_writer(root: str, tool: str, session_id: str, barrier, slices) -> None:
    """multiprocessing worker：同步起跑後逐筆寫入自己的 slices。

    模擬 Copilot prompt hook / Claude prompt hook / 顯式 recall 對同一
    (tool, session_id) 重疊執行——每次呼叫都是一輪「讀 map→合併→替換」。
    """
    from paulsha_hippo.hooks import _shortlist_common as sc
    barrier.wait()
    for sid, path in slices:
        sc._record_offered(Path(root), tool, session_id, "proj", [(sid, path)])


def _run_concurrent_offered_writers(tmp_path: Path, session_id: str, n: int = 40):
    """兩個 writer 進程同 session 同步起跑，各寫 n 筆互斥的 slices。"""
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    slices_a = [(f"sl-aaaa{i:012d}", str(tmp_path / "knowledge" / "proj" / f"a{i}.md"))
                for i in range(n)]
    slices_b = [(f"sl-bbbb{i:012d}", str(tmp_path / "knowledge" / "proj" / f"b{i}.md"))
                for i in range(n)]
    procs = [
        ctx.Process(target=_offered_writer,
                    args=(str(tmp_path), "claude-code", session_id, barrier, s))
        for s in (slices_a, slices_b)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert all(p.exitcode == 0 for p in procs)
    return slices_a, slices_b


def test_record_offered_concurrent_writers_keep_both_slice_sets(tmp_path):
    # 迴歸：無鎖 read-modify-write 下並發更新互相覆蓋（丟 slice）、或 tmp 被
    # 對方搶先 replace 而整輪更新失敗——兩組 slices 必須全數保留於 map，
    # 後續 read 判定（by_path / by_id / _load_offered_ids）均 offered:true。
    slices_a, slices_b = _run_concurrent_offered_writers(tmp_path, "sidCC")
    mpath = tmp_path / "runtime" / "wakeup" / "claude-code__sidCC.offered.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    missing = [(sid, path) for sid, path in slices_a + slices_b
               if m["by_id"].get(sid) != path or m["by_path"].get(path) != sid]
    assert missing == []
    offered_ids = SC._load_offered_ids(tmp_path, "claude-code", "sidCC")
    assert {sid for sid, _ in slices_a + slices_b} <= offered_ids


@pytest.mark.parametrize("evil", [
    "../../outside",     # 相對 traversal
    "..",                # 純父目錄
    "a/b",               # POSIX 分隔符
    "a\\b",              # Windows 分隔符
    "/etc",              # 絕對路徑
    ".hidden",           # 前導 '.'（dotfile / '..' 家族）
    "a:b",               # 冒號（sanitize_id 同級拒絕）
    "",                  # 空值
])
def test_offered_map_path_rejects_non_path_safe_tool(tmp_path, evil):
    # 迴歸（#17 review [high]）：tool 未驗證即嵌入檔名，路徑分隔符/.. 可把
    # offered map 組出 runtime/wakeup 之外——一律 ValueError，不得產生路徑。
    with pytest.raises(ValueError):
        SC._offered_map_path(tmp_path, evil, "sid")


def test_offered_map_path_valid_tool_stays_under_wakeup(tmp_path):
    p = SC._offered_map_path(tmp_path, "claude-code", "sid/1")
    assert p.parent == tmp_path / "runtime" / "wakeup"
    assert p.name == "claude-code__sid__1.offered.json"


def test_shortlist_traversal_tool_fails_closed_writes_nothing(tmp_path, monkeypatch):
    # 迴歸（#17 review [high]）：tool="../../../outside" 過去會讓 _record_offered
    # 的原子 replace 把 map 寫到 memory root 之外（可覆寫任意檔）。現在整條
    # pipeline fail-closed：不注入 shortlist、不記 offered、root 外無任何落檔。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(
        tmp_path, "../../../outside", "sidT", cwd="/x", prompt="SerialWrap 執行")
    assert out == ""
    assert not (tmp_path / "runtime" / "ledger" / "offered.jsonl").exists()
    assert not (tmp_path / "runtime" / "wakeup").exists()
    # 逃逸落點（root 外）：map 本體與 lock/tmp 一律不得出現
    assert not (tmp_path.parent / "outside__sidT.offered.json").exists()
    assert list(tmp_path.parent.glob(".outside__sidT.offered.json*")) == []


def test_record_offered_concurrent_writers_leave_no_tmp_residue(tmp_path):
    # 迴歸：並發 writer 完成後 wakeup 目錄不得殘留任何 .tmp 暫存檔。
    _run_concurrent_offered_writers(tmp_path, "sidCT")
    wakeup = tmp_path / "runtime" / "wakeup"
    residue = sorted(p.name for p in wakeup.iterdir() if p.name.endswith(".tmp"))
    assert residue == []


def _pipeline_worker(root: str, tool: str, session_id: str, prompt: str,
                     barrier, queue) -> None:
    """multiprocessing worker：同步起跑後跑完整 build_shortlist_and_record，回報是否注入。

    模擬兩個 prompt-time 路徑（Claude prompt hook 與顯式 recall）對同一
    (tool, session_id) 同 prompt 併發跑完整管線——seen 去重與 claim 必須原子。
    """
    from paulsha_hippo.hooks import _shortlist_common as sc
    barrier.wait()
    out = sc.build_shortlist_and_record(Path(root), tool, session_id, cwd="/x", prompt=prompt)
    queue.put(1 if out else 0)


def test_build_shortlist_concurrent_same_session_claims_slice_once(tmp_path, monkeypatch):
    # 迴歸（#17 Codex gate [high]）：seen 讀取＋hits 篩選過去發生在任何鎖之外，
    # 只有 _record_offered 內的 map RMW 被 per-session flock 保護。兩個進程對同一
    # (tool, session_id) 同 prompt 併發跑完整 build_shortlist_and_record()：兩邊都
    # 讀到空 seen → 都回傳非空 shortlist、都向 offered.jsonl 追加同一 slice（重複
    # 曝光＋offered_count 膨脹）。修正後「重讀 seen → claim → ledger append → map
    # commit」整段併入同一把 per-session flock：恰一次非空回傳、offered.jsonl 對該
    # slice 僅一筆、後續 read 判定 offered:true。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)  # 單一命中 slice sl-aaaaaaaaaaaaaaaa

    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_pipeline_worker,
                    args=(str(tmp_path), "claude-code", "sidRACE", "SerialWrap 執行",
                          barrier, queue))
        for _ in range(2)
    ]
    for p in procs:
        p.start()
    results = sorted(queue.get(timeout=120) for _ in range(2))
    for p in procs:
        p.join(timeout=120)
    assert all(p.exitcode == 0 for p in procs)

    assert results == [0, 1]  # 恰一次非空注入；另一路 seen 已含該 slice → 去重成空

    events = _offered_events(tmp_path)
    assert len(events) == 1
    assert [item["sl_id"] for item in events[0]["offered"]] == ["sl-aaaaaaaaaaaaaaaa"]

    # 後續 read 判定：該 slice 一定 offered:true（map 已 commit）
    assert "sl-aaaaaaaaaaaaaaaa" in SC._load_offered_ids(tmp_path, "claude-code", "sidRACE")


def _boom_oserror(*a, **k):
    raise OSError("disk full")


def test_publish_offered_map_cache_failure_keeps_ledger_truth(tmp_path, monkeypatch):
    # 反轉為「先 ledger 後 map」後：map cache 更新失敗（磁碟滿／權限／IO）發生在 ledger 已
    # commit 之後。ledger 是單一真值＋commit point——此失敗不回滾、不 fail-closed（否則 agent
    # 收不到已 commit 的 offer，重現前輪 offered-but-undelivered 的指標膨脹）：offer 視為已發布
    # （block 照常回傳），ledger 保有事件，讀取端以 ledger 為準仍視該 slice 為 offered，
    # 下一輪 reconcile 由 ledger 重建 map。此失敗態等同硬中止落在「ledger 有、map 無」的安全側。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    monkeypatch.setattr(SC, "_commit_offered_map", _boom_oserror)

    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidMC", cwd="/x",
                                        prompt="SerialWrap 執行")
    assert out != ""  # ledger 已 commit → offer 已發布，block 照常送達（非 fail-closed）
    events = _offered_events(tmp_path)
    assert len(events) == 1
    assert [i["sl_id"] for i in events[0]["offered"]] == ["sl-aaaaaaaaaaaaaaaa"]
    # map cache 寫入失敗 → 檔案缺該 slice，但讀取端以 ledger 為準仍 offered:true（不永久遺漏）
    assert "sl-aaaaaaaaaaaaaaaa" in SC._load_offered_ids(tmp_path, "claude-code", "sidMC")


def test_publish_offered_ledger_failure_publishes_nothing(tmp_path, monkeypatch):
    # ledger append（commit point、發布第一步）失敗（disk full）：map 從未被觸及（不是回滾——
    # 反轉後 map 更新在 ledger 之後，ledger 一失敗就 raise、根本不到 map 步驟），外層回 ''，
    # 兩產物皆未發布、可乾淨重試，且不留 .tmp 殘留。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    monkeypatch.setattr(SC, "_append_offered_ledger", _boom_oserror)

    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidLA", cwd="/x",
                                        prompt="SerialWrap 執行")
    assert out == ""
    assert _offered_events(tmp_path) == []
    # map 從未寫入（ledger 先失敗）
    assert not (tmp_path / "runtime" / "wakeup" / "claude-code__sidLA.offered.json").exists()
    wakeup = tmp_path / "runtime" / "wakeup"
    if wakeup.exists():
        residue = [p.name for p in wakeup.iterdir() if p.name.endswith(".tmp")]
        assert residue == []


def test_publish_offered_ledger_failure_leaves_prior_map_untouched(tmp_path, monkeypatch):
    # 前態非空：先成功 offer A（map={A}、ledger=[A]），第二筆 B 於 ledger append 失敗——反轉後
    # B 的 append（commit point）失敗發生在 map 更新之前，故 map 從未因 B 改動（無需回滾），逐
    # byte 維持 {A}、ledger 仍只有 A（不會落入 map 有 B、ledger 無 B 的單邊態）。以 _record_offered
    # 直呼，精準控制 offered 清單。
    note_a = str(tmp_path / "knowledge" / "proj" / "a.md")
    note_b = str(tmp_path / "knowledge" / "proj" / "b.md")
    SC._record_offered(tmp_path, "claude-code", "sidRB", "proj",
                       [("sl-aaaaaaaaaaaaaaaa", note_a)])
    mpath = tmp_path / "runtime" / "wakeup" / "claude-code__sidRB.offered.json"
    before = mpath.read_text(encoding="utf-8")

    monkeypatch.setattr(SC, "_append_offered_ledger", _boom_oserror)
    SC._record_offered(tmp_path, "claude-code", "sidRB", "proj",
                       [("sl-bbbbbbbbbbbbbbbb", note_b)])  # _record_offered 內部吞例外

    assert mpath.read_text(encoding="utf-8") == before  # 逐 byte 不變（B 從未寫入）
    m = json.loads(mpath.read_text(encoding="utf-8"))
    assert m["by_id"] == {"sl-aaaaaaaaaaaaaaaa": note_a}
    assert "sl-bbbbbbbbbbbbbbbb" not in m["by_id"]
    events = _offered_events(tmp_path)
    assert len(events) == 1
    assert [i["sl_id"] for i in events[0]["offered"]] == ["sl-aaaaaaaaaaaaaaaa"]


def test_publish_offered_ledger_failure_then_retry_is_clean(tmp_path, monkeypatch):
    # 失敗的發布不留半發布狀態 → 相同輸入重試乾淨成功：恰一筆 offered。ledger 是 commit point
    # ——首呼 append 失敗（disk full）時尚無任何產物發布（map 未動）、外層 fail-closed 回 ''、
    # 未污染 seen／未重複記帳；次呼（磁碟恢復）乾淨成功。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    real_append = SC._append_offered_ledger
    calls = {"n": 0}

    def _flaky_append(root, tool, session_id, project, offered):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_append(root, tool, session_id, project, offered)

    monkeypatch.setattr(SC, "_append_offered_ledger", _flaky_append)

    # 第一次：ledger append 失敗 → '' 且無 ledger、無 map（commit point 未達）
    assert SC.build_shortlist_and_record(tmp_path, "claude-code", "sidRT", cwd="/x",
                                         prompt="SerialWrap 執行") == ""
    assert _offered_events(tmp_path) == []
    assert not (tmp_path / "runtime" / "wakeup" / "claude-code__sidRT.offered.json").exists()

    # 第二次（相同輸入、磁碟恢復）：乾淨成功、恰一筆 offered
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidRT", cwd="/x",
                                        prompt="SerialWrap 執行")
    assert out != ""
    events = _offered_events(tmp_path)
    assert len(events) == 1
    assert [i["sl_id"] for i in events[0]["offered"]] == ["sl-aaaaaaaaaaaaaaaa"]
    assert "sl-aaaaaaaaaaaaaaaa" in SC._load_offered_ids(tmp_path, "claude-code", "sidRT")


def _kill_before_map_worker(root: str, tool: str, session_id: str, note: str, sid_val: str) -> None:
    """fork child：ledger append（含 fsync）成功後、map 更新前以 os._exit(1) 模擬硬中止。

    發布順序為「先 ledger 後 map」，故 monkeypatch map 更新步驟（_commit_offered_map）為
    os._exit——child 死在 ledger 已落盤、map 尚未更新的窗口（正是本 finding 的硬中止點）。
    os._exit 不執行 except/finally，等同 SIGKILL/hook timeout 之不可捕捉終止。
    """
    import os as _os
    from paulsha_hippo.hooks import _shortlist_common as sc

    def _die(*a, **k):
        _os._exit(1)

    sc._commit_offered_map = _die
    sc._record_offered(Path(root), tool, session_id, "proj", [(sid_val, note)])


def test_kill_after_ledger_before_map_no_permanent_loss(tmp_path, monkeypatch):
    # 迴歸（#17 Codex high, conf 0.99）：_publish_offered 前輪「先 commit map 再 append
    # ledger」，SIGKILL / hook timeout / 主機中斷落在兩步之間 → map 已標記 slice 為 seen
    # 但 ledger 無 offered 事件 → 重啟後該 slice 永久被去重、不再送達（違反全有或全無），
    # 前輪只修可捕捉例外路徑。修正：反轉為「先 fsync ledger（單一真值＋commit point）後更新
    # map（可重建 cache）」，硬中止只會落在「ledger 有、map 無」＝安全側，reconcile 以 ledger
    # 為準補齊 map。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    monkeypatch.setattr(SC, "SHORTLIST_K", 1)
    _seed(tmp_path)  # 命中 slice sl-aaaaaaaaaaaaaaaa @ knowledge/proj/a.md
    note = str(tmp_path / "knowledge" / "proj" / "a.md")
    sid_val = "sl-aaaaaaaaaaaaaaaa"

    ctx = multiprocessing.get_context("fork")
    proc = ctx.Process(target=_kill_before_map_worker,
                       args=(str(tmp_path), "claude-code", "sidKILL", note, sid_val))
    proc.start()
    proc.join(timeout=120)
    # 確認確實走到「map 更新前」的中止點（os._exit(1)）——否則測不到目標窗口
    assert proc.exitcode == 1

    # 硬中止後的 durable 狀態：ledger 有事件（write 後 fsync 落盤，撐過 os._exit），map
    # 尚未更新（缺該 slice）——正是「ledger 有、map 無」的安全側（不是舊 bug 的相反單邊態）
    events = _offered_events(tmp_path)
    assert len(events) == 1
    assert events[0]["session_id"] == "sidKILL"
    assert [i["sl_id"] for i in events[0]["offered"]] == [sid_val]
    mpath = tmp_path / "runtime" / "wakeup" / "claude-code__sidKILL.offered.json"
    map_by_id = json.loads(mpath.read_text(encoding="utf-8")).get("by_id", {}) if mpath.exists() else {}
    assert sid_val not in map_by_id  # 中止點：map 尚未含該 slice

    # 「重啟」讀取端以 ledger 為準：該 slice 仍被視為 offered（不永久遺漏）
    assert sid_val in SC._load_offered_ids(tmp_path, "claude-code", "sidKILL")

    # 下一輪完整管線以 ledger 為準去重：不重複送達（out==''）、不重複 append ledger、也不永久
    # 遺漏（ledger 仍持有事件）；reconcile 已把該 slice 補回 map（post-tool 讀取端據此判定）
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidKILL", cwd="/x",
                                        prompt="SerialWrap 執行")
    assert out == ""  # 不重複去重／不重複送達
    events_after = _offered_events(tmp_path)
    assert len(events_after) == 1  # 不重複 append
    reconciled = json.loads(mpath.read_text(encoding="utf-8"))
    assert reconciled["by_id"].get(sid_val) == note  # reconcile 補回 map
    assert reconciled["by_path"].get(note) == sid_val


def test_reconcile_from_ledger_rebuilds_missing_map(tmp_path):
    # 直接構造硬中止後的 durable 狀態（「先 ledger 後 map」的中止窗口）：offered ledger 有
    # 事件、per-session map 檔尚不存在。斷言 (a) 讀取端以 ledger 為準視該 slice 為 offered
    # （不永久遺漏），(b) reconcile 把 ledger 有、map 無的 slice 補回 map（post-tool 讀取端
    # 據此拿到 ledger 可重建的真值），(c) reconcile 幂等。
    note = str(tmp_path / "knowledge" / "proj" / "a.md")
    sid_val = "sl-aaaaaaaaaaaaaaaa"
    SC._append_offered_ledger(tmp_path, "claude-code", "sidLO", "proj", [(sid_val, note)])
    mpath = tmp_path / "runtime" / "wakeup" / "claude-code__sidLO.offered.json"
    assert not mpath.exists()  # 中止窗口：map 尚未建立

    assert sid_val in SC._load_offered_ids(tmp_path, "claude-code", "sidLO")

    SC._reconcile_offered_map(tmp_path, "claude-code", "sidLO", mpath)
    m = json.loads(mpath.read_text(encoding="utf-8"))
    assert m["by_id"] == {sid_val: note}
    assert m["by_path"] == {note: sid_val}

    # reconcile 幂等：ledger 已全在 map 中 → 不重複寫、內容不變
    SC._reconcile_offered_map(tmp_path, "claude-code", "sidLO", mpath)
    assert json.loads(mpath.read_text(encoding="utf-8")) == m


def test_reconcile_map_write_failure_does_not_block_new_slice(tmp_path, monkeypatch):
    # 迴歸：恢復窗口「offered ledger 有事件（slice A）、per-session map 尚未反映」下，下一輪
    # build_shortlist_and_record 會先跑 _reconcile_offered_map 以 ledger 補齊 map。若補寫
    # （_commit_offered_map）因儲存層故障（磁碟滿／權限／IO——正是 _publish_offered 明文容忍的
    # 同一類故障）持續失敗，過去例外會從 reconcile 傳出、經 _session_lock 區塊被 build_shortlist_
    # and_record 最外層 except 攔成 fail-closed 回 ''——即使本輪要 claim 的是全新、從未 offer 過、
    # 確實命中的 slice B 也被吞掉。修正後 reconcile 的補寫降為 best-effort（比照 _publish_offered）：
    # 只 log_warn 不 raise，B 照常送達；ledger 為單一真值、_load_offered_ids 已聯集 ledger，去重不受影響。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed_two(tmp_path)  # a.md=sl-aaaa（預置為 offered）、b.md=sl-bbbb（本輪全新命中）
    note_a = str(tmp_path / "knowledge" / "proj" / "a.md")
    note_b = str(tmp_path / "knowledge" / "proj" / "b.md")

    # 恢復窗口：ledger 有 A，但 per-session map 檔尚不存在（硬中止／前輪 map 補寫失敗殘留態）
    SC._append_offered_ledger(tmp_path, "claude-code", "sidRW", "proj",
                              [("sl-aaaaaaaaaaaaaaaa", note_a)])
    mpath = tmp_path / "runtime" / "wakeup" / "claude-code__sidRW.offered.json"
    assert not mpath.exists()

    # 持續性補寫失敗：reconcile 與其下游 _publish_offered 的 _commit_offered_map 均會拋
    monkeypatch.setattr(SC, "_commit_offered_map", _boom_oserror)

    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidRW", cwd="/x",
                                        prompt="SerialWrap 執行")

    # reconcile 補寫失敗不再 fail-closed：全新命中 slice B 仍正常送達 shortlist
    assert out != ""
    assert note_b in out
    # A 已於 ledger → seen 命中去重、不重複送達；B 為本輪新 offer（ledger 為準，不依賴 map 補寫）
    seen = SC._load_offered_ids(tmp_path, "claude-code", "sidRW")
    assert "sl-aaaaaaaaaaaaaaaa" in seen and "sl-bbbbbbbbbbbbbbbb" in seen
    events = _offered_events(tmp_path)
    assert len(events) == 2  # 預置的 A + 本輪的 B（reconcile／publish 的 map 補寫失敗不影響 ledger）
    assert [i["sl_id"] for i in events[0]["offered"]] == ["sl-aaaaaaaaaaaaaaaa"]
    assert [i["sl_id"] for i in events[1]["offered"]] == ["sl-bbbbbbbbbbbbbbbb"]


def test_applied_hint_failure_before_commit_publishes_nothing(tmp_path, monkeypatch):
    # 迴歸：applied-hint 計算（_applied_hint → hippo_invocation → Path.exists() stat
    # hooks/.venv/bin/python）遇 PermissionError(EACCES)／NFS ESTALE/EIO 等（皆不在
    # Path.exists() 內部吞掉的 errno {ENOENT,ENOTDIR,EBADF,ELOOP} 之列）會原樣往外拋。此計算
    # 已移到不可逆 commit point（_publish_offered fsync）之前，故一旦拋出時尚無任何產物發布：
    # 外層 fail-closed 回 '' 為乾淨失敗，offered ledger／per-session map 皆未落檔、無 .tmp 殘留
    # （相同輸入可乾淨重試，slice 未被判 seen）。修正前此計算排在 publish 之後，例外被最外層
    # except 吞成 '' 但 slice 已記 offered → 後續呼叫永久去重、slice 再也不進 shortlist。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)

    def _boom_hint(*a, **k):
        raise PermissionError("stat venv python denied")

    monkeypatch.setattr(SC, "_applied_hint", _boom_hint)

    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidAH", cwd="/x",
                                        prompt="SerialWrap 執行")
    assert out == ""  # hint 失敗仍 fail-closed 回 ''
    # 關鍵差異（修正前此處 offered.jsonl 已含該 slice）：commit point 未達 → 兩產物皆未落檔
    assert _offered_events(tmp_path) == []
    assert not (tmp_path / "runtime" / "wakeup" / "claude-code__sidAH.offered.json").exists()
    wakeup = tmp_path / "runtime" / "wakeup"
    if wakeup.exists():
        assert [p.name for p in wakeup.iterdir() if p.name.endswith(".tmp")] == []


def test_applied_hint_failure_then_retry_delivers_slice_no_permanent_loss(tmp_path, monkeypatch):
    # 迴歸（核心不變量：既不永久遺漏也不重複送達）：首呼 applied-hint 計算拋例外（模擬 stat
    # hooks/.venv/bin/python 遇 EACCES/ESTALE）→ 回 '' 且無 ledger／map（commit point 之前失敗）；
    # 還原後次呼以相同 session/tool/prompt → slice 未被判 seen，照常送達 shortlist、恰一筆 offered。
    # 修正前：首呼在 publish 之後才拋 → offered.jsonl 已含該 slice、回 ''；次呼因 slice 已 seen
    # 仍回 '' → slice 永久不再出現在 shortlist（本 finding 已用重現腳本驗證的永久遺漏）。
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    real_hint = SC._applied_hint
    calls = {"n": 0}

    def _flaky_hint(root, tool, session_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("stat venv python denied")
        return real_hint(root, tool, session_id)

    monkeypatch.setattr(SC, "_applied_hint", _flaky_hint)

    # 首呼：hint 計算失敗（commit point 之前）→ '' 且無 ledger／map
    assert SC.build_shortlist_and_record(tmp_path, "claude-code", "sidAR", cwd="/x",
                                         prompt="SerialWrap 執行") == ""
    assert _offered_events(tmp_path) == []
    assert not (tmp_path / "runtime" / "wakeup" / "claude-code__sidAR.offered.json").exists()

    # 次呼（相同輸入、hint 恢復）：slice 未被判 seen → 照常送達、恰一筆 offered（不永久遺漏）
    note = str(tmp_path / "knowledge" / "proj" / "a.md")
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidAR", cwd="/x",
                                        prompt="SerialWrap 執行")
    assert out != "" and note in out
    assert "usage mark-applied" in out  # 成功路徑照常附上 applied-hint
    events = _offered_events(tmp_path)
    assert len(events) == 1
    assert [i["sl_id"] for i in events[0]["offered"]] == ["sl-aaaaaaaaaaaaaaaa"]
    assert "sl-aaaaaaaaaaaaaaaa" in SC._load_offered_ids(tmp_path, "claude-code", "sidAR")
