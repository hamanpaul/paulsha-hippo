"""`hippo knowledge link-supersedes` backfill（fix 2b，issue #136）。

auto tier（同 family ＋ canonical title/alias 完全相等、雙向唯一）自動連結；
其餘同主題候選走 review tier 出報表，人看過後再 `--accept`。
"""
from __future__ import annotations

import json
from pathlib import Path

from paulsha_hippo import cli
from paulsha_hippo import supersedes_link as sl
from paulsha_hippo.janitor import record_source, rules
from paulsha_hippo.janitor.config import JanitorConfig
from paulsha_hippo.ledger import lifecycle, relations
from paulsha_hippo.moc import frontmatter_io as fio

NOW = "2026-08-25T00:00:00Z"


def _note(mr: Path, sid: str, project: str, title: str, at, checksum: str,
          tags=(), aliases=(), related=(), distilled_from=None):
    """寫一則 knowledge note。``at=None`` 代表整個 captured_at 欄位缺席。"""
    p = mr / "knowledge" / project / f"n--{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tags_s = "".join(f"  - {t}\n" for t in tags)
    al = "".join(f"  - \"{a}\"\n" for a in aliases)
    rel = "".join(f"  - \"{r}\"\n" for r in related)
    captured = f"captured_at: \"{at}\"\n" if at is not None else ""
    distilled = f"distilled_from: \"{distilled_from}\"\n" if distilled_from is not None else ""
    p.write_text(
        f"---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: {project}\n"
        f"title: \"{title}\"\n{captured}{distilled}checksum: {checksum}\n"
        f"supersedes: []\ntags:\n{tags_s}aliases:\n{al}related:\n{rel}---\nb\n",
        encoding="utf-8",
    )
    return p


def test_scan_splits_auto_and_review(tmp_path):
    _note(tmp_path, "sl-old", "p", "CC2674P10 Flash 配置", "2026-08-10T00:00:00Z", "c1")
    _note(tmp_path, "sl-new", "p", "cc2674p10 flash 配置", "2026-08-20T00:00:00Z", "c2")
    _note(tmp_path, "sl-a", "p", "dual profile build isolation contract",
          "2026-08-01T00:00:00Z", "c3", tags=("build", "dual"))
    _note(tmp_path, "sl-b", "p", "dual build profile isolation gate",
          "2026-08-02T00:00:00Z", "c4", tags=("build", "dual"))
    out = sl.scan(tmp_path)
    assert [(x["new"], x["old"], x["reason"]) for x in out["auto"]] == [("sl-new", "sl-old", "exact-title")]
    assert {(x["new"], x["old"]) for x in out["review"]} == {("sl-b", "sl-a")}
    assert out["review"][0]["reason"] == "jaccard:0.67"


def test_alias_match_is_auto_tier(tmp_path):
    _note(tmp_path, "sl-al-old", "p", "Flash Layout 契約", "2026-08-01T00:00:00Z", "c1")
    _note(tmp_path, "sl-al-new", "p", "雙目標 Flash 佈局", "2026-08-05T00:00:00Z", "c2",
          aliases=("Flash Layout 契約",))
    out = sl.scan(tmp_path)
    assert [(x["new"], x["old"], x["reason"]) for x in out["auto"]] == [("sl-al-new", "sl-al-old", "alias")]


def test_multi_candidates_go_to_review_not_auto(tmp_path):
    _note(tmp_path, "sl-1", "p", "T", "2026-08-01T00:00:00Z", "c1")
    _note(tmp_path, "sl-2", "p", "T", "2026-08-02T00:00:00Z", "c2")
    _note(tmp_path, "sl-3", "p", "T", "2026-08-03T00:00:00Z", "c3")
    out = sl.scan(tmp_path)
    assert out["auto"] == [] and len(out["review"]) >= 1
    # 沒有自連、沒有回頭邊（舊 -> 新）：captured_at 嚴格較新才成對。
    assert all(x["new"] != x["old"] for x in out["review"])
    assert {(x["new"], x["old"]) for x in out["review"]} == {
        ("sl-2", "sl-1"), ("sl-3", "sl-1"), ("sl-3", "sl-2")}


def test_same_project_only_without_families(tmp_path):
    _note(tmp_path, "sl-x", "proj-a", "T", "2026-08-01T00:00:00Z", "c1")
    _note(tmp_path, "sl-y", "proj-b", "T", "2026-08-02T00:00:00Z", "c2")
    assert sl.scan(tmp_path) == {"auto": [], "review": []}
    out = sl.scan(tmp_path, families=(("proj-a", "proj-b"),))
    assert [(x["new"], x["old"], x["reason"]) for x in out["auto"]] == [("sl-y", "sl-x", "exact-title")]


def test_decayed_and_identical_checksum_are_not_candidates(tmp_path):
    _note(tmp_path, "sl-dead", "p", "T", "2026-08-01T00:00:00Z", "c1")
    _note(tmp_path, "sl-live", "p", "T", "2026-08-02T00:00:00Z", "c2")
    _note(tmp_path, "sl-same-old", "p", "U", "2026-08-01T00:00:00Z", "c9")
    _note(tmp_path, "sl-same-new", "p", "U", "2026-08-02T00:00:00Z", "c9")
    lifecycle.append_event(
        path=tmp_path / "runtime" / "ledger" / "lifecycle.jsonl", record_id="sl-dead",
        event_type="decayed", source="janitor", reason="superseded", actor="hippo", ts=NOW,
    )
    assert sl.scan(tmp_path) == {"auto": [], "review": []}


def test_decayed_note_is_not_a_superseder_either(tmp_path):
    """decayed 過濾必須雙側：已退場的較新 note 不該吃掉還活著的舊 note。"""
    _note(tmp_path, "sl-dk-old", "p", "T", "2026-08-01T00:00:00Z", "c1")
    _note(tmp_path, "sl-dk-new", "p", "T", "2026-08-02T00:00:00Z", "c2")
    lifecycle.append_event(
        path=tmp_path / "runtime" / "ledger" / "lifecycle.jsonl", record_id="sl-dk-new",
        event_type="decayed", source="janitor", reason="superseded", actor="hippo", ts=NOW,
    )
    assert sl.scan(tmp_path) == {"auto": [], "review": []}


def test_tied_captured_at_same_session_siblings_are_skipped(tmp_path):
    """同時間戳 ＋ 同 distilled_from ＝ 同一場 session 蒸出的兄弟，publish 當下已判過。"""
    _note(tmp_path, "sl-sib-a", "p", "T", "2026-08-01T00:00:00Z", "c1", distilled_from="claude:s1")
    _note(tmp_path, "sl-sib-b", "p", "T", "2026-08-01T00:00:00Z", "c2", distilled_from="claude:s1")
    assert sl.scan(tmp_path) == {"auto": [], "review": []}


def test_tied_captured_at_across_sessions_goes_to_review(tmp_path):
    """同時間戳但不同 session：真的是兩個版本，方向卻無法從時間判定 -> review。"""
    _note(tmp_path, "sl-tie-a", "p", "T", "2026-08-01T00:00:00Z", "c1", distilled_from="claude:s1")
    _note(tmp_path, "sl-tie-b", "p", "T", "2026-08-01T00:00:00Z", "c2", distilled_from="claude:s2")
    out = sl.scan(tmp_path)
    # 永不進 auto；方向以 slice_id 字典序決定（大者當新），同一份輸入永遠同一個方向。
    assert out["auto"] == []
    assert [(x["new"], x["old"], x["reason"]) for x in out["review"]] == [
        ("sl-tie-b", "sl-tie-a", "equal-captured_at")]


def test_missing_or_unparseable_captured_at_never_pairs(tmp_path):
    """缺值/無法解析的 captured_at 被壓到時間地板；地板上的並列不是「同時」。"""
    _note(tmp_path, "sl-nd-none", "p", "T", None, "c1", distilled_from="claude:s1")
    _note(tmp_path, "sl-nd-bad", "p", "T", "_unknown", "c2", distilled_from="claude:s2")
    _note(tmp_path, "sl-nd-dated", "p", "T", "2026-08-01T00:00:00Z", "c3", distilled_from="claude:s3")
    assert sl.scan(tmp_path) == {"auto": [], "review": []}


def test_corrupt_lifecycle_ledger_warns_once(tmp_path, capsys):
    """壞掉的 lifecycle ledger 不該靜悄悄地變成「沒有任何 decayed note」。"""
    _note(tmp_path, "sl-w-old", "p", "T", "2026-08-01T00:00:00Z", "c1")
    _note(tmp_path, "sl-w-new", "p", "T", "2026-08-02T00:00:00Z", "c2")
    ledger = tmp_path / "runtime" / "ledger" / "lifecycle.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{not json}\n", encoding="utf-8")
    out = sl.scan(tmp_path)
    assert [(x["new"], x["old"]) for x in out["auto"]] == [("sl-w-new", "sl-w-old")]
    err = capsys.readouterr().err
    assert err.count("warning: link-supersedes:") == 1
    assert "lifecycle" in err


def test_apply_writes_supersedes_edge_and_is_idempotent(tmp_path):
    _note(tmp_path, "sl-old", "p", "T", "2026-08-10T00:00:00Z", "c1")
    new = _note(tmp_path, "sl-new", "p", "T", "2026-08-20T00:00:00Z", "c2")
    n1 = sl.apply_pairs(tmp_path, sl.scan(tmp_path)["auto"], now=NOW)
    assert n1 == 1 and fio.read(new.read_text(encoding="utf-8"))[0]["supersedes"] == ["sl-old"]
    edges = [e for e in relations.read_edges(tmp_path) if e["type"] == "supersedes"]
    assert len(edges) == 1
    assert edges[0]["from"] == "slice:sl-new" and edges[0]["to"] == "slice:sl-old"
    assert sl.scan(tmp_path)["auto"] == []
    assert sl.apply_pairs(tmp_path, [{"new": "sl-new", "old": "sl-old"}], now=NOW) == 0
    assert len([e for e in relations.read_edges(tmp_path) if e["type"] == "supersedes"]) == 1


def test_apply_appends_to_existing_supersedes(tmp_path):
    """既有 supersedes 的順序原樣保留，新前身接在最後（不做字典序重排）。"""
    _note(tmp_path, "sl-p1", "p", "T", "2026-08-01T00:00:00Z", "c1")
    new = _note(tmp_path, "sl-p2", "p", "T", "2026-08-02T00:00:00Z", "c2")
    fio.update(new, {"supersedes": ["sl-zz-earlier", "sl-aa-earlier"]})
    assert sl.apply_pairs(tmp_path, [{"new": "sl-p2", "old": "sl-p1"}], now=NOW) == 1
    assert fio.read(new.read_text(encoding="utf-8"))[0]["supersedes"] == [
        "sl-zz-earlier", "sl-aa-earlier", "sl-p1"]


def test_apply_never_self_links_or_targets_missing_slice(tmp_path):
    _note(tmp_path, "sl-solo", "p", "T", "2026-08-01T00:00:00Z", "c1")
    assert sl.apply_pairs(tmp_path, [{"new": "sl-solo", "old": "sl-solo"},
                                     {"new": "sl-solo", "old": "sl-ghost"}], now=NOW) == 0
    assert relations.read_edges(tmp_path) == []


def test_apply_refuses_pair_that_would_close_a_cycle(tmp_path):
    # 手改過的報表可能夾帶回頭邊；scan 產出的配對本身是嚴格 recency 偏序、天生無環。
    old = _note(tmp_path, "sl-cy-old", "p", "T", "2026-08-01T00:00:00Z", "c1")
    _note(tmp_path, "sl-cy-new", "p", "T", "2026-08-02T00:00:00Z", "c2")
    assert sl.apply_pairs(tmp_path, [{"new": "sl-cy-new", "old": "sl-cy-old"}], now=NOW) == 1
    assert sl.apply_pairs(tmp_path, [{"new": "sl-cy-old", "old": "sl-cy-new"}], now=NOW) == 0
    assert fio.read(old.read_text(encoding="utf-8"))[0]["supersedes"] == []


def test_flash_layout_pair_needs_family(tmp_path):
    _note(tmp_path, "sl-0817", "ot-ti-mirror", "ot-ti-mirror Flash Layout and Image Artifacts",
          "2026-08-17T03:32:41Z", "c1", tags=("flash-layout",))
    _note(tmp_path, "sl-0821", "MCU-Octopus", "雙目標 Flash Layout",
          "2026-08-21T10:27:49Z", "c2", tags=("flash-layout", "cc2755"))
    assert sl.scan(tmp_path) == {"auto": [], "review": []}
    out = sl.scan(tmp_path, families=(("MCU-Octopus", "ot-ti-mirror"),))
    assert out["auto"] == []
    # 同 family、tags 交集 1（flash-layout）、標題 token 交集 2（flash、layout）→ review tier「tags+title」
    assert [(x["new"], x["old"], x["reason"]) for x in out["review"]] == [("sl-0821", "sl-0817", "tags+title")]


def test_shared_tags_tier(tmp_path):
    _note(tmp_path, "sl-t1", "p", "alpha", "2026-08-01T00:00:00Z", "c1", tags=("cc2674", "nvs"))
    _note(tmp_path, "sl-t2", "p", "beta", "2026-08-02T00:00:00Z", "c2", tags=("cc2674", "nvs"))
    out = sl.scan(tmp_path)
    assert [(x["new"], x["old"], x["reason"]) for x in out["review"]] == [("sl-t2", "sl-t1", "tags:2")]


def test_mutual_related_tier_resolves_real_wikilinks(tmp_path):
    """`related` 實際寫的是 `[[<title-slug>--<slice_id>]]`（`moc/linker.py`），不是裸 slice_id。

    兩種形狀都要認得：linker 產出的 wikilink（用 `--<slice_id>` 尾綴解析）與
    手寫的裸 slice_id。認不得就等於整個 related tier 從來沒有生效過。
    """
    _note(tmp_path, "sl-r1", "p", "alpha", "2026-08-01T00:00:00Z", "c1",
          related=("[[beta-title--sl-r2]]",))
    _note(tmp_path, "sl-r2", "p", "beta", "2026-08-02T00:00:00Z", "c2", related=("sl-r1",))
    out = sl.scan(tmp_path)
    assert [(x["new"], x["old"], x["reason"]) for x in out["review"]] == [("sl-r2", "sl-r1", "related")]


def test_report_round_trip_accept(tmp_path):
    _note(tmp_path, "sl-a", "p", "dual profile build isolation contract", "2026-08-01T00:00:00Z", "c3")
    new = _note(tmp_path, "sl-b", "p", "dual build profile isolation gate", "2026-08-02T00:00:00Z", "c4")
    report = sl.write_report(tmp_path, sl.scan(tmp_path)["review"], now=NOW)
    lines = report.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and '"accept": false' in lines[0]
    markdown = report.with_suffix(".md").read_text(encoding="utf-8")
    for token in ("sl-b", "sl-a", "jaccard:0.67", "dual build profile isolation gate",
                  "2026-08-02T00:00:00Z", "| p / p |"):
        assert token in markdown
    report.write_text(lines[0].replace('"accept": false', '"accept": true') + "\n", encoding="utf-8")
    assert sl.apply_accepted(tmp_path, report, now=NOW) == 1
    assert fio.read(new.read_text(encoding="utf-8"))[0]["supersedes"] == ["sl-a"]
    # 再跑一次同一份報表 → no-op。
    assert sl.apply_accepted(tmp_path, report, now=NOW) == 0


def test_applied_pair_decays_old_note_via_janitor(tmp_path):
    _note(tmp_path, "sl-j-old", "p", "T", "2026-08-10T00:00:00Z", "c1")
    _note(tmp_path, "sl-j-new", "p", "T", "2026-08-20T00:00:00Z", "c2")
    assert sl.apply_pairs(tmp_path, sl.scan(tmp_path)["auto"], now=NOW) == 1
    records, _ = record_source.iter_records(tmp_path / "knowledge")
    config = JanitorConfig(schema_version="1", default_decay_age_days=90, by_artifact_kind={},
                           check_provenance_path=False, check_provenance_commit=False,
                           decay_superseded=True)
    events = rules.plan_scan(records, {}, {}, config, NOW, "h", source_path_exists=lambda rec: True)
    decayed = [e for e in events if e["record_id"] == "sl-j-old"]
    assert len(decayed) == 1 and decayed[0]["reason"] == "superseded"
    assert decayed[0]["detail"]["superseded_by"] == "sl-j-new"


def test_cli_dry_run_writes_no_note_and_apply_then_dry_run_reports_zero(tmp_path, capsys):
    old = _note(tmp_path, "sl-c-old", "p", "T", "2026-08-10T00:00:00Z", "c1")
    new = _note(tmp_path, "sl-c-new", "p", "T", "2026-08-20T00:00:00Z", "c2")
    before = (old.read_bytes(), new.read_bytes())

    assert cli.main(["knowledge", "link-supersedes", "--memory-root", str(tmp_path),
                     "--dry-run", "--now", NOW]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["auto"] == 1 and payload["review"] == 0
    # 沒有 review 候選就沒有報表：乾淨 root 上的 dry-run 什麼都不寫。
    assert payload["report"] is None
    assert not (tmp_path / "runtime").exists()
    assert (old.read_bytes(), new.read_bytes()) == before
    assert relations.read_edges(tmp_path) == []

    assert cli.main(["knowledge", "link-supersedes", "--memory-root", str(tmp_path),
                     "--apply", "--tier", "auto", "--now", NOW]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] == 1
    assert fio.read(new.read_text(encoding="utf-8"))[0]["supersedes"] == ["sl-c-old"]

    assert cli.main(["knowledge", "link-supersedes", "--memory-root", str(tmp_path),
                     "--dry-run", "--now", NOW]) == 0
    assert json.loads(capsys.readouterr().out)["auto"] == 0

    assert cli.main(["knowledge", "link-supersedes", "--memory-root", str(tmp_path),
                     "--apply", "--tier", "auto", "--now", NOW]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == 0


def test_cli_accept_report_and_missing_report_exits_one(tmp_path, capsys):
    _note(tmp_path, "sl-d-a", "p", "dual profile build isolation contract", "2026-08-01T00:00:00Z", "c3")
    new = _note(tmp_path, "sl-d-b", "p", "dual build profile isolation gate", "2026-08-02T00:00:00Z", "c4")
    assert cli.main(["knowledge", "link-supersedes", "--memory-root", str(tmp_path),
                     "--dry-run", "--now", NOW]) == 0
    report = Path(json.loads(capsys.readouterr().out)["report"])
    line = report.read_text(encoding="utf-8").splitlines()[0]
    report.write_text(line.replace('"accept": false', '"accept": true') + "\n", encoding="utf-8")

    assert cli.main(["knowledge", "link-supersedes", "--memory-root", str(tmp_path),
                     "--accept", str(report), "--now", NOW]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == 1
    assert fio.read(new.read_text(encoding="utf-8"))[0]["supersedes"] == ["sl-d-a"]

    assert cli.main(["knowledge", "link-supersedes", "--memory-root", str(tmp_path),
                     "--accept", str(tmp_path / "nope.jsonl"), "--now", NOW]) == 1
