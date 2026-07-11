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
