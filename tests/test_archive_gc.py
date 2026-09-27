"""#151：archive GC——以 processing／import ledger 為準，只回收已落成 knowledge 的 session。

合成 memory root 全在 tmp_path；不碰真實資料。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from paulsha_hippo import archive_gc, cli
from paulsha_hippo.dream.lock import acquire_dream_lock

NOW = "2026-09-20T00:00:00Z"
OLD_TS = "2026-08-01T00:00:00Z"
RECENT_TS = "2026-09-18T00:00:00Z"
OLD_EPOCH = datetime(2026, 7, 31, tzinfo=timezone.utc).timestamp()
RECENT_EPOCH = datetime(2026, 9, 19, tzinfo=timezone.utc).timestamp()
NOW_DT = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _write(path: Path, text: str, *, mtime: float = OLD_EPOCH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _doc(path: Path, agent: str, session: str, *, prov_path: str = "", mtime: float = OLD_EPOCH) -> Path:
    fm = [
        "---",
        "memory_layer: inbox",
        "project: demo",
        f"source_agent: {agent}",
        f"source_session: {session}",
        "provenance:",
        '  repo: "_unknown"',
        '  commit: ""',
        f"  path: {json.dumps(prov_path)}",
        "---",
        "body",
    ]
    return _write(path, "\n".join(fm) + "\n", mtime=mtime)


def _event(key: str, state: str, ts: str, **extra) -> dict:
    event = {"ts": ts, "session_key": key, "state": state, "atomizer_config_hash": "h"}
    if state == "parked":
        event["failure_category"] = "transient"
    event.update(extra)
    return event


class Store:
    """合成 memory root：各 session 的 archive 檔 + ledger + knowledge。"""

    def __init__(self, root: Path):
        self.root = root
        self.archive = root / "archive"
        self.events: list[dict] = []
        self.imports: list[dict] = []
        (root / "runtime" / "ledger").mkdir(parents=True)
        (root / "knowledge").mkdir(parents=True)
        (root / "inbox").mkdir(parents=True)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def session_doc(self, month: str, agent: str, session: str, *, name: str | None = None,
                    fm_session: str | None = None, mtime: float = OLD_EPOCH) -> Path:
        name = name or f"{agent}__{session}.md"
        return _doc(self.archive / "sessions" / month / name, agent,
                    fm_session if fm_session is not None else session, mtime=mtime)

    def fragment(self, month: str, agent: str, session: str, index: int, *, mtime: float = OLD_EPOCH) -> Path:
        return _doc(self.archive / "fragments" / month / f"{agent}__{session}__{index:03d}.md",
                    agent, session, mtime=mtime)

    def queue(self, month: str, name: str, logical_key: str | None, *, status: str = "written",
              mtime: float = OLD_EPOCH) -> Path:
        path = _write(self.archive / "queue" / month / name, json.dumps({"raw": name}), mtime=mtime)
        if logical_key is not None:
            self.imports.append({
                "status": status,
                "logical_session_key": logical_key,
                "idempotency_key": logical_key,
                "archive_path": str(path),
            })
        return path

    def flush(self) -> None:
        ledger = self.root / "runtime" / "ledger"
        (ledger / "processing.jsonl").write_text(
            "".join(json.dumps(e, sort_keys=True) + "\n" for e in self.events), encoding="utf-8")
        (ledger / "import.jsonl").write_text(
            "".join(json.dumps(e, sort_keys=True) + "\n" for e in self.imports), encoding="utf-8")


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "memory")
    m = "2026-07"
    files: dict[str, Path] = {}

    # A：promoted、早已落成——主要回收對象
    s.events += [_event("claude-code:sa", "split", "2026-07-30T00:00:00Z"),
                 _event("claude-code:sa", "promoted", OLD_TS)]
    files["a_doc"] = s.session_doc(m, "claude-code", "sa")
    files["a_frag0"] = s.fragment(m, "claude-code", "sa", 0)
    files["a_frag1"] = s.fragment(m, "claude-code", "sa", 1)
    files["a_q_written"] = s.queue(m, "claude-code__sa--written--aaaaaaaaaaaa.json", "claude-code:sa")
    files["a_q_final"] = s.queue(m, "claude-code__sa--updated--bbbbbbbbbbbb.json", "claude-code:sa",
                                 status="updated")
    # A 的 knowledge 以 provenance.path 釘住 final capture
    _doc(s.root / "knowledge" / "demo" / "slice-a.md", "claude-code", "sa",
         prov_path=str(files["a_q_final"]))
    # A 的晚到 capture（mtime 很新）→ 保留窗內
    files["a_q_recent"] = s.queue("2026-09", "claude-code__sa--stale-skip--eeeeeeeeeeee.json",
                                  "claude-code:sa", status="stale-skip", mtime=RECENT_EPOCH)
    # 檔名歸屬 A、frontmatter 卻寫別的 session → attribution-mismatch
    files["a_mismatch"] = s.session_doc(m, "claude-code", "sa", name="claude-code__sa--dddddddddddd.md",
                                        fm_session="someone-else")

    # B：promoted 但剛落成（保留窗內）
    s.events.append(_event("claude-code:sb", "promoted", RECENT_TS))
    files["b_doc"] = s.session_doc(m, "claude-code", "sb")

    # C：parked；D：split（pending）
    s.events.append(_event("codex:sc", "parked", OLD_TS))
    files["c_doc"] = s.session_doc(m, "codex", "sc")
    s.events.append(_event("codex:sd", "split", OLD_TS))
    files["d_doc"] = s.session_doc(m, "codex", "sd")

    # E：no-findings（預設不視為落成 knowledge）
    s.events.append(_event("copilot-cli:se", "no-findings", OLD_TS))
    files["e_doc"] = s.session_doc(m, "copilot-cli", "se")
    files["e_frag"] = s.fragment(m, "copilot-cli", "se", 0)

    # F：importer skip capture，從未進 atomize → 無 processing 紀錄
    files["f_q"] = s.queue(m, "claude-code__sf--empty-skip--ffffffffffff.json", "claude-code:sf",
                           status="empty-skip")

    # G：promoted，但 inbox 有新 capture 待重新蒸餾 → pending-inbox
    s.events.append(_event("claude-code:sg", "promoted", OLD_TS))
    files["g_doc"] = s.session_doc(m, "claude-code", "sg")
    _doc(s.root / "inbox" / "sessions" / "claude-code" / "2026-09-19" / "sg.md", "claude-code", "sg")

    # H：promoted，但 inbox/_slices 仍有殘留 fragment → pending-inbox
    s.events.append(_event("claude-code:sh", "promoted", OLD_TS))
    files["h_doc"] = s.session_doc(m, "claude-code", "sh")
    _doc(s.root / "inbox" / "_slices" / "demo" / "claude-code__sh__000.md", "claude-code", "sh")

    # P：promoted 且早已落成，但 archived session doc 被 knowledge provenance.path 引用
    #    （防線：任何被 provenance 引用的 archive 檔一律保留，不論子樹）
    s.events.append(_event("claude-code:sp", "promoted", OLD_TS))
    files["p_doc"] = s.session_doc(m, "claude-code", "sp")
    _doc(s.root / "knowledge" / "demo" / "slice-p.md", "claude-code", "sp",
         prov_path=str(files["p_doc"]))

    # 無法歸因：不在 import ledger 的 queue 檔、檔名無對應 session 的 archive doc
    files["orphan_q"] = s.queue(m, "mystery--written--cccccccccccc.json", None)
    files["orphan_doc"] = _doc(s.archive / "sessions" / m / "garbage.md", "x", "y")

    # symlink 假冒 archive 檔（指向 store 外）→ 不得刪、不得跟隨
    outside = _write(s.root.parent / "outside.md", "keep me")
    link = s.archive / "fragments" / m / "claude-code__sa__009.md"
    link.symlink_to(outside)
    files["symlink"] = link
    files["outside"] = outside

    s.flush()
    s.files = files
    return s


def _gc(store: Store, **kwargs):
    kwargs.setdefault("now", NOW_DT)
    return archive_gc.run_archive_gc(store.root, **kwargs)


def _expected_a(store: Store) -> set[str]:
    f = store.files
    return {store.rel(f[k]) for k in ("a_doc", "a_frag0", "a_frag1")}


def test_dry_run_lists_only_landed_unpinned_files_and_deletes_nothing(store):
    before = {p for p in store.root.rglob("*")}

    result = _gc(store)

    assert result["applied"] is False
    assert set(result["candidate_paths"]) == _expected_a(store)
    assert result["candidates"]["files"] == 3
    assert result["candidates"]["bytes"] == sum(
        (store.root / rel).stat().st_size for rel in _expected_a(store))
    assert {p for p in store.root.rglob("*")} == before
    assert "error" not in result and "blocked" not in result


def test_kept_reasons_cover_every_protection(store):
    result = _gc(store)
    reasons = {k: v["files"] for k, v in result["kept"]["by_reason"].items()}

    assert reasons == {
        "raw-queue-retained": 5,         # A×3 capture、F skip capture、orphan queue
        "provenance-pinned": 1,          # P 的 archived session doc
        "retention-window": 1,           # B（landed_at）
        "attribution-mismatch": 1,
        "not-landed:parked": 1,
        "not-landed:split": 1,
        "not-landed:no-findings": 2,
        "pending-inbox": 2,              # G, H
        "unattributable": 1,             # garbage doc
        "not-regular-file": 1,           # symlink
    }
    assert result["scanned"]["files"] == result["candidates"]["files"] + result["kept"]["files"]


def test_retention_zero_releases_recent_landed_files(store):
    result = _gc(store, retention_days=0)
    f = store.files

    assert set(result["candidate_paths"]) == _expected_a(store) | {store.rel(f["b_doc"])}


def test_include_no_findings_is_opt_in(store):
    result = _gc(store, include_no_findings=True)
    f = store.files

    assert set(result["candidate_paths"]) == _expected_a(store) | {
        store.rel(f["e_doc"]), store.rel(f["e_frag"])}
    assert result["landed_states"] == ["no-findings", "promoted"]


def test_apply_deletes_exactly_the_candidates_and_is_idempotent(store):
    expected = _expected_a(store)
    all_before = {store.rel(p) for p in store.root.rglob("*") if p.is_file() or p.is_symlink()}

    result = _gc(store, apply=True)

    assert result["applied"] is True
    assert result["deleted"]["files"] == 3
    assert result["failed"] == []
    all_after = {store.rel(p) for p in store.root.rglob("*") if p.is_file() or p.is_symlink()}
    assert all_before - all_after == expected
    assert store.files["outside"].read_text(encoding="utf-8") == "keep me"
    assert store.files["symlink"].is_symlink()

    again = _gc(store, apply=True)
    assert again["candidates"]["files"] == 0
    assert again["deleted"]["files"] == 0
    assert {store.rel(p) for p in store.root.rglob("*") if p.is_file() or p.is_symlink()} == all_after


def test_apply_is_blocked_while_dream_holds_the_lock(store):
    handle = acquire_dream_lock(store.root)
    assert handle is not None
    try:
        result = _gc(store, apply=True)
    finally:
        handle.close()

    assert result["applied"] is False
    assert "dream" in result["blocked"]
    assert all((store.root / rel).exists() for rel in _expected_a(store))


def test_malformed_processing_ledger_fails_closed(store):
    ledger = store.root / "runtime" / "ledger" / "processing.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    result = _gc(store, apply=True)

    assert result["applied"] is False
    assert "processing ledger" in result["error"]
    assert all((store.root / rel).exists() for rel in _expected_a(store))


def test_symlinked_subtree_is_not_followed_and_blocks_apply(store, tmp_path):
    real = tmp_path / "elsewhere-fragments"
    (store.archive / "fragments").rename(real)
    (store.archive / "fragments").symlink_to(real, target_is_directory=True)

    dry = _gc(store)
    assert dry["unsafe_dirs"] == ["archive/fragments"]
    assert not any(p.startswith("archive/fragments/") for p in dry["candidate_paths"])

    result = _gc(store, apply=True)
    assert result["applied"] is False
    assert "symlink" in result["blocked"]
    assert all(p.exists() for p in real.rglob("*.md"))


def test_delete_skips_files_swapped_after_planning(store, tmp_path):
    """規劃後檔案被換掉（symlink／新 inode）→ 不刪、列入 failed。"""
    plan = archive_gc._plan(store.root, now=NOW_DT, retention_days=7.0,
                            landed_states=frozenset({"promoted"}))
    by_rel = {c.entry.rel: c for c in plan["candidates"]}
    swapped_link = store.files["a_frag0"]
    swapped_file = store.files["a_frag1"]
    target = _write(tmp_path / "victim.md", "must survive")
    swapped_link.unlink()
    swapped_link.symlink_to(target)
    replacement = _write(swapped_file.with_name("incoming.tmp"), "new capture with a new inode")
    os.replace(replacement, swapped_file)  # 原 inode 仍存在時建立 → 保證不同 inode

    outcome = archive_gc._delete(store.root, list(by_rel.values()))

    assert sorted(outcome["failed"]) == sorted([store.rel(swapped_link), store.rel(swapped_file)])
    assert target.read_text(encoding="utf-8") == "must survive"
    assert swapped_link.is_symlink() and swapped_file.is_file()
    assert {e.rel for e in outcome["deleted"]} == {store.rel(store.files["a_doc"])}


def test_list_out_receives_the_deletion_list(store, tmp_path):
    out = tmp_path / "gc-list.tsv"

    result = _gc(store, list_out=out)

    assert result["list_out"] == str(out)
    assert "candidate_paths" not in result
    rows = [line.split("\t") for line in out.read_text(encoding="utf-8").splitlines()]
    assert {row[0] for row in rows} == _expected_a(store)
    assert all(row[2] == "claude-code:sa" for row in rows)


def test_cli_dry_run_then_apply(store, capsys):
    base = ["archive", "gc", "--memory-root", str(store.root), "--now", NOW]

    assert cli.main(base) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["applied"] is False
    assert set(dry["candidate_paths"]) == _expected_a(store)
    assert all((store.root / rel).exists() for rel in _expected_a(store))

    assert cli.main(base + ["--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["deleted"]["files"] == 3
    assert not any((store.root / rel).exists() for rel in _expected_a(store))


def test_cli_rejects_negative_retention(store, capsys):
    rc = cli.main(["archive", "gc", "--memory-root", str(store.root), "--retention-days", "-1"])
    assert rc == 2


def test_cli_returns_nonzero_when_blocked(store, capsys):
    handle = acquire_dream_lock(store.root)
    try:
        rc = cli.main(["archive", "gc", "--memory-root", str(store.root), "--now", NOW, "--apply"])
    finally:
        handle.close()
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["blocked"]


def test_missing_archive_is_a_clean_noop(tmp_path):
    root = tmp_path / "memory"
    (root / "runtime" / "ledger").mkdir(parents=True)

    result = archive_gc.run_archive_gc(root, now=NOW_DT, apply=True)

    assert result["scanned"]["files"] == 0
    assert result["deleted"]["files"] == 0
    assert "error" not in result and "blocked" not in result


def test_queue_payloads_are_never_candidates_even_when_landed(store):
    """archive/queue 是 importer 的 frozen raw 來源（recovery／backfill 直接讀它），一律保留。"""
    result = _gc(store, retention_days=0, include_no_findings=True)

    assert not any(p.startswith("archive/queue/") for p in result["candidate_paths"])
    assert result["candidates"]["by_subtree"]["queue"] == {"files": 0, "bytes": 0}
    assert result["kept"]["by_reason"]["raw-queue-retained"]["files"] == 5


# ------------------------------------------------ recovery／backfill 回歸（#160 審查）


def _raw_capture(path: Path, session_id: str, *, scope: str, summary: str) -> Path:
    return _write(path, json.dumps({
        "tool": "claude",
        "session_id": session_id,
        "capture_scope": scope,
        "cwd": "/repo",
        "assistant_summary": summary,
        "user_prompts": ["repair this"],
        "ended_at": "2026-07-16T00:00:00Z",
    }))


@pytest.fixture()
def landed_raw_store(tmp_path, monkeypatch):
    """一個已 promoted 的 session：兩份真實可解析的 raw capture（只有 final 被 knowledge 釘住）
    ＋ 已 archive 的 session doc 與 fragment；import ledger 也記錄了兩份 capture 的歸屬。"""
    monkeypatch.setattr(
        "paulsha_hippo.importer.title._default_runner", lambda text, command, timeout: "Recovered")
    s = Store(tmp_path / "memory")
    m = "2026-07"
    s.events.append(_event("claude:sr", "promoted", OLD_TS))
    first = _raw_capture(s.archive / "queue" / m / "claude__sr--written--aaaaaaaaaaaa.json", "sr",
                         scope="pre_compact", summary="earlier capture")
    final = _raw_capture(s.archive / "queue" / m / "claude__sr--updated--bbbbbbbbbbbb.json", "sr",
                         scope="session_end", summary="final capture")
    for path, status in ((first, "written"), (final, "updated")):
        s.imports.append({"status": status, "logical_session_key": "claude:sr",
                          "idempotency_key": "claude:sr", "archive_path": str(path)})
    _doc(s.root / "knowledge" / "demo" / "slice-r.md", "claude", "sr", prov_path=str(final))
    s.files = {
        "doc": s.session_doc(m, "claude", "sr"),
        "frag": s.fragment(m, "claude", "sr", 0),
        "first": first,
        "final": final,
    }
    s.flush()
    return s


def test_existing_recovery_transaction_still_applies_and_rolls_back_after_gc(landed_raw_store):
    from paulsha_hippo import recovery

    s = landed_raw_store
    manifest = recovery.create_plan(s.root, batch_size=5)   # GC 前就存在的 recovery 交易
    planned = json.loads(manifest.read_text(encoding="utf-8"))
    assert planned["source_count"] == 2

    result = _gc(s, apply=True)
    assert result["applied"] is True
    assert result["deleted"]["files"] == 2                  # 只刪 session doc 與 fragment
    assert s.files["first"].is_file() and s.files["final"].is_file()

    applied = recovery.apply_plan(manifest)                 # _verify_pins 不得出現 source pin drift
    assert applied
    rolled = recovery.rollback_plan(manifest)
    assert rolled["rolled_back"] == 1
    replanned = recovery.create_plan(s.root, batch_size=5)  # 新規劃仍涵蓋全部 raw capture
    assert json.loads(replanned.read_text(encoding="utf-8"))["source_count"] == 2
    repinned = recovery.create_plan(s.root, batch_size=5, source_manifest_path=manifest)
    assert json.loads(repinned.read_text(encoding="utf-8"))["source_count"] == 2


def test_backfill_still_reextracts_every_raw_capture_after_gc(landed_raw_store):
    from paulsha_hippo.importer import backfill

    s = landed_raw_store
    before = backfill.run(s.root, dry_run=True)
    assert before["count"] == 2 and not any("error" in item for item in before["items"])

    _gc(s, apply=True)

    after = backfill.run(s.root, dry_run=True)
    assert after["count"] == 2
    assert not any("error" in item for item in after["items"])


# ------------------------------------------------------ --list-out 錯誤契約（#160 審查）


def test_unwritable_list_out_is_reported_and_blocks_apply(store, tmp_path):
    blocker = _write(tmp_path / "not-a-dir", "file")
    bad = blocker / "gc-list.tsv"                       # 父層是檔案 → NotADirectoryError

    dry = _gc(store, list_out=bad)
    assert "list-out" in dry["error"]
    assert dry["applied"] is False

    result = _gc(store, apply=True, list_out=bad)
    assert "list-out" in result["error"]
    assert result["applied"] is False
    assert result["deleted"]["files"] == 0
    assert all((store.root / rel).exists() for rel in _expected_a(store))


def test_cli_list_out_failure_keeps_json_and_exit_code_contract(store, tmp_path, capsys):
    blocker = _write(tmp_path / "not-a-dir", "file")

    rc = cli.main(["archive", "gc", "--memory-root", str(store.root), "--now", NOW,
                   "--apply", "--list-out", str(blocker / "gc-list.tsv")])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert "list-out" in payload["error"]
    assert all((store.root / rel).exists() for rel in _expected_a(store))
