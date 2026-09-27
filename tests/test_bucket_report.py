"""#117：既有 knowledge bucket 合併的 dry-run impact report（`hippo knowledge bucket-report`）。

只驗報告內容與「零寫入」；實際搬檔屬 P2，不在本指令範圍。全部使用合成資料。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from paulsha_hippo import cli
from paulsha_hippo.atomizer.config import project_directory_key
from paulsha_hippo.importer.config import ProjectConfig
from paulsha_hippo.importer.registry import render_registry


def _git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def _note(
    memory_root: Path,
    *,
    project: str,
    slice_id: str,
    repo: str = "_unknown",
    session: str | None = None,
    payload: dict | None = None,
    layer: str = "knowledge",
    bucket: str | None = None,
) -> Path:
    session = session or f"sess-{slice_id}"
    payload_path = memory_root / "archive" / "queue" / "2026-09" / f"codex__{session}--written--x.json"
    if payload is not None:
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
    directory = memory_root / "knowledge" / (bucket or project_directory_key(project))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"note--{slice_id}.md"
    path.write_text(
        "---\n"
        f"memory_layer: {layer}\n"
        f"project: {json.dumps(project)}\n"
        f"slice_id: {slice_id}\n"
        f"distilled_from: \"codex:{session}\"\n"
        f"source_session: {session}\n"
        "provenance:\n"
        f"  repo: {repo}\n"
        f"  path: {payload_path}\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    return path


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("PSC_CONFIG_ROOT", raising=False)
    fake_tmp = tmp_path / "fake-system-tmp"
    fake_tmp.mkdir()
    monkeypatch.setenv("HIPPO_EPHEMERAL_ROOTS", str(fake_tmp))
    base = tmp_path / "agents"
    memory_root = base / "memory"
    registry = base / "config" / "paulsha" / "project-hippo.yaml"
    legacy = base / "config" / "projects.yaml"
    registry.parent.mkdir(parents=True)

    repo = tmp_path / "work" / "widget"
    repo.mkdir(parents=True)
    _git("init", "-q", str(repo))
    _git("-C", str(repo), "remote", "add", "origin", "git@github.com:acme/widget.git")
    registry.write_text(
        render_registry((ProjectConfig(slug="widget", roots=(str(repo.resolve()),)),)),
        encoding="utf-8",
    )
    legacy.write_text(
        "projects:\n  legacy-proj:\n    remotes:\n      - github.com/acme/legacy\n", encoding="utf-8"
    )

    # 已在正確 bucket：registered slug
    _note(memory_root, project="widget", slice_id="sl-w1", repo="github.com/acme/widget")
    _note(memory_root, project="widget", slice_id="sl-w2", repo="github.com/acme/widget")
    _note(memory_root, project="widget", slice_id="sl-ep", layer="episodic")
    # worktree 碎裂出的 raw remote bucket：補登 remotes 後應併入 widget
    for index in range(3):
        _note(memory_root, project="github.com/acme/widget", slice_id=f"sl-r{index}", repo="github.com/acme/widget")
    # 未登記 repo 的 raw remote bucket：維持原樣
    _note(memory_root, project="github.com/acme/other", slice_id="sl-o1", repo="github.com/acme/other")
    # 暫存 sandbox 的目錄名假 project
    for index in range(2):
        _note(
            memory_root,
            project="checkout",
            slice_id=f"sl-c{index}",
            payload={"cwd": str(fake_tmp / "cortex-planning-abc" / "checkout")},
        )
    # _unknown 成因
    _note(memory_root, project="_unknown", slice_id="sl-u1", session="coerced-1", payload={"cwd": "/w/x"})
    _note(memory_root, project="_unknown", slice_id="sl-u2", repo="github.com/acme/other", payload={"cwd": "/w/y"})
    _note(memory_root, project="_unknown", slice_id="sl-u3", payload={"tool": "codex"})
    _note(memory_root, project="_unknown", slice_id="sl-u4")
    _note(memory_root, project="_unknown", slice_id="sl-u5", payload={"cwd": str(tmp_path / "work" / "plain")})
    _note(memory_root, project="_unknown", slice_id="sl-u6", payload={"cwd": str(fake_tmp / "sbx")})

    ledger = memory_root / "runtime" / "ledger" / "import.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"idempotency_key": "codex:coerced-1", "project": "github.com/acme/widget"}) + "\n"
        + "{torn line\n",
        encoding="utf-8",
    )
    (memory_root / "knowledge" / "widget-moc.md").write_text("---\nmemory_layer: moc\n---\n", encoding="utf-8")
    return {"memory_root": memory_root, "registry": registry, "legacy": legacy, "base": base}


def _report(world, *extra: str, capsys) -> dict:
    code = cli.main(
        [
            "knowledge",
            "bucket-report",
            "--memory-root",
            str(world["memory_root"]),
            "--registry",
            str(world["registry"]),
            "--projects",
            str(world["legacy"]),
            *extra,
        ]
    )
    assert code == 0
    return json.loads(capsys.readouterr().out)


def _bucket(report: dict, name: str) -> dict:
    return next(bucket for bucket in report["buckets"] if bucket["bucket"] == name)


def test_report_is_read_only(world, capsys):
    before = _snapshot(world["base"])

    _report(world, capsys=capsys)

    assert _snapshot(world["base"]) == before


def test_raw_remote_bucket_merges_into_backfilled_slug_with_rekey_command(world, capsys):
    report = _report(world, capsys=capsys)

    assert report["dry_run"] is True
    assert report["planned_remote_backfill"] == [{"slug": "widget", "remotes": ["github.com/acme/widget"]}]
    raw = _bucket(report, project_directory_key("github.com/acme/widget"))
    assert raw["kind"] == "raw-remote"
    assert raw["notes"] == 3
    assert raw["move"] == {"widget": 3}
    proposal = next(p for p in report["merge_proposals"] if p["from_project"] == "github.com/acme/widget")
    assert proposal["to_slug"] == "widget"
    assert proposal["to_bucket"] == "widget"
    assert proposal["notes"] == 3
    assert proposal["rekey_executable"] is True
    assert proposal["command"] == (
        f"hippo knowledge rekey --memory-root {world['memory_root']} "
        "--from github.com/acme/widget --to widget --dry-run"
    )


def test_registered_and_unregistered_buckets_stay(world, capsys):
    report = _report(world, capsys=capsys)

    widget = _bucket(report, "widget")
    assert widget["kind"] == "registered"
    assert (widget["notes"], widget["stay"], widget["move"], widget["non_knowledge"]) == (2, 2, {}, 1)
    other = _bucket(report, project_directory_key("github.com/acme/other"))
    assert (other["kind"], other["stay"], other["move"]) == ("raw-remote", 1, {})


def test_ephemeral_dirname_bucket_is_unresolved_not_merged(world, capsys):
    report = _report(world, capsys=capsys)

    checkout = _bucket(report, "checkout")
    assert checkout["kind"] == "dirname-fallback"
    assert checkout["move"] == {}
    assert checkout["unresolved"] == {"ephemeral-cwd": 2}
    assert not [p for p in report["merge_proposals"] if p["from_project"] == "checkout"]


def test_unknown_bucket_cause_classification(world, capsys):
    report = _report(world, capsys=capsys)

    unknown = _bucket(report, "_unknown")
    assert unknown["kind"] == "unknown"
    assert unknown["move"] == {"widget": 1, "github.com/acme/other": 1}
    assert report["unknown_causes"] == {
        "atomizer-coerced": 1,
        "provenance-only": 1,
        "no-cwd": 1,
        "payload-missing": 1,
        "no-remote-evidence": 1,
        "ephemeral-cwd": 1,
    }
    assert report["unknown_recoverable"] == {"notes": 2, "total": 6}
    for proposal in report["merge_proposals"]:
        if proposal["from_project"] == "_unknown":
            # rekey 以 project key 全量搬移；_unknown 的去向分散，不能用 rekey 執行
            assert proposal["rekey_executable"] is False
            assert proposal["command"] is None


def test_without_backfill_overlay_raw_remote_bucket_stays(world, capsys):
    report = _report(world, "--no-backfill-overlay", capsys=capsys)

    assert report["planned_remote_backfill"] == []
    raw = _bucket(report, project_directory_key("github.com/acme/widget"))
    assert raw["move"] == {}
    assert raw["stay"] == 3


def test_misplaced_note_with_correct_frontmatter_is_reported_as_relocation(world, capsys):
    # 審查 #161-1：frontmatter project 已正確、檔案卻仍在舊 bucket（例如搬移中斷）時，
    # 只比 frontmatter 會把它算成 stay 而漏報；必須同時比對實際所在目錄。
    raw_bucket = project_directory_key("github.com/acme/widget")
    _note(world["memory_root"], project="widget", slice_id="sl-mis1", repo="github.com/acme/widget", bucket=raw_bucket)
    _note(world["memory_root"], project="widget", slice_id="sl-mis2", bucket="checkout")

    report = _report(world, capsys=capsys)

    raw = _bucket(report, raw_bucket)
    assert (raw["notes"], raw["stay"], raw["move"], raw["relocate"]) == (4, 0, {"widget": 3}, {"widget": 1})
    checkout = _bucket(report, "checkout")
    assert (checkout["stay"], checkout["relocate"], checkout["unresolved"]) == (0, {"widget": 1}, {"ephemeral-cwd": 2})
    assert report["totals"]["relocate"] == 2
    assert sorted((r["from_bucket"], r["project"], r["to_bucket"], r["notes"]) for r in report["relocations"]) == [
        ("checkout", "widget", "widget", 1),
        (raw_bucket, "widget", "widget", 1),
    ]
    for relocation in report["relocations"]:
        # rekey 以 frontmatter project 選取且拒絕 --from == --to，無法處理純目錄錯置
        assert relocation["rekey_executable"] is False
        assert relocation["command"] is None
    assert not [p for p in report["merge_proposals"] if p["from_project"] == "widget"]


def test_bucket_counts_partition_every_knowledge_note(world, capsys):
    _note(world["memory_root"], project="widget", slice_id="sl-mis3", bucket=project_directory_key("github.com/acme/widget"))

    report = _report(world, capsys=capsys)

    for bucket in report["buckets"]:
        counted = bucket["stay"] + sum(bucket["move"].values()) + sum(bucket["relocate"].values()) + sum(
            bucket["unresolved"].values()
        )
        assert counted == bucket["notes"], bucket["bucket"]
    totals = report["totals"]
    assert totals["stay"] + totals["move"] + totals["relocate"] + totals["unresolved"] == totals["notes"]


def test_legacy_sanitized_directory_is_accepted_as_correct_location(world, capsys):
    # atomizer 讀取端同時接受 project_directory_key 與 legacy sanitize_project_component 目錄
    _note(world["memory_root"], project="github.com/acme/other", slice_id="sl-leg1", bucket="github.com__acme__other")

    report = _report(world, capsys=capsys)

    legacy = _bucket(report, "github.com__acme__other")
    assert (legacy["stay"], legacy["relocate"]) == (1, {})
