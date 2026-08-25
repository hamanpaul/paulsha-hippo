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


def _note(mr: Path, sid: str, project: str, title: str, at: str, checksum: str,
          tags=(), aliases=(), related=()):
    p = mr / "knowledge" / project / f"n--{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tags_s = "".join(f"  - {t}\n" for t in tags)
    al = "".join(f"  - \"{a}\"\n" for a in aliases)
    rel = "".join(f"  - \"{r}\"\n" for r in related)
    p.write_text(
        f"---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: {project}\n"
        f"title: \"{title}\"\ncaptured_at: \"{at}\"\nchecksum: {checksum}\n"
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
    _note(tmp_path, "sl-p1", "p", "T", "2026-08-01T00:00:00Z", "c1")
    new = _note(tmp_path, "sl-p2", "p", "T", "2026-08-02T00:00:00Z", "c2")
    fio.update(new, {"supersedes": ["sl-earlier"]})
    assert sl.apply_pairs(tmp_path, [{"new": "sl-p2", "old": "sl-p1"}], now=NOW) == 1
    assert fio.read(new.read_text(encoding="utf-8"))[0]["supersedes"] == ["sl-earlier", "sl-p1"]


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


def test_mutual_related_tier(tmp_path):
    _note(tmp_path, "sl-r1", "p", "alpha", "2026-08-01T00:00:00Z", "c1", related=("sl-r2",))
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
    assert Path(payload["report"]).exists()
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
