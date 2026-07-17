import json
from hashlib import sha256
from pathlib import Path

import pytest

from paulsha_hippo import recovery
from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.atomizer import pipeline as atomizer_pipeline
from paulsha_hippo.ledger import processing


def _seed_source(root: Path, *, session_id: str = "s1", summary: str = "fixed outcome") -> Path:
    source = root / "archive" / "queue" / "2026-07" / f"claude__{session_id}.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps(
            {
                "tool": "claude",
                "session_id": session_id,
                "capture_scope": "session_end",
                "cwd": "/repo",
                "assistant_summary": summary,
                "user_prompts": ["repair this"],
                "ended_at": "2026-07-16T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return source


def _plan(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "paulsha_hippo.importer.title._default_runner", lambda text, command, timeout: "Recovered"
    )
    return recovery.create_plan(root, batch_size=5)


def _seed_live_transcript_source(root: Path) -> tuple[Path, Path]:
    transcript = root / "live-transcript.jsonl"
    transcript.write_text(
        '\n'.join(
            [
                json.dumps({"type": "user", "message": {"content": "repair this"}}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [{"type": "text", "text": "frozen outcome"}]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    source = root / "archive" / "queue" / "2026-07" / "claude__live.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps(
            {
                "tool": "claude",
                "session_id": "live",
                "capture_scope": "session_end",
                "cwd": "/repo",
                "transcript_path": str(transcript),
                "ended_at": "2026-07-16T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return source, transcript


def test_plan_accounts_all_raw_and_keeps_llm_replay_separate(tmp_path, monkeypatch):
    _seed_source(tmp_path, session_id="same", summary="older")
    second = tmp_path / "archive" / "queue" / "2026-07" / "claude__same__new.json"
    second.write_text(
        json.dumps(
            {
                "tool": "claude",
                "session_id": "same",
                "capture_scope": "watcher_final",
                "assistant_summary": "newer and more complete outcome",
                "user_prompts": ["repair this"],
                "ended_at": "2026-07-16T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["source_count"] == 2
    assert manifest["logical_session_count"] == 1
    assert manifest["winner_count"] == 1
    assert manifest["llm_replay"] == "not-planned"
    assert sum(entry.get("decision") == "importer-recover" for entry in manifest["entries"]) == 1
    assert sum(entry.get("decision") == "superseded-source" for entry in manifest["entries"]) == 1
    assert all(entry["expected_ledger_delta"]["historical_jsonl"] == 0 for entry in manifest["entries"])


def test_live_transcript_is_frozen_at_plan_and_may_continue_afterward(tmp_path, monkeypatch):
    source, transcript = _seed_live_transcript_source(tmp_path)
    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    winner = next(entry for entry in manifest["entries"] if entry.get("winner"))
    snapshot = Path(winner["transcript"]["snapshot_path"])

    assert snapshot.is_file()
    assert winner["transcript"]["sha256"] == sha256(snapshot.read_bytes()).hexdigest()
    assert winner["capture_id"] == sha256(source.read_bytes()).hexdigest()

    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "late outcome"}]},
                }
            )
            + "\n"
        )

    result = recovery.apply_plan(manifest_path)
    recovered = Path(winner["inbox_path"]).read_text(encoding="utf-8")
    assert result["committed"] == 1
    assert "frozen outcome" in recovered
    assert "late outcome" not in recovered
    assert str(source) in recovered


def test_apply_rejects_frozen_transcript_snapshot_drift(tmp_path, monkeypatch):
    _seed_live_transcript_source(tmp_path)
    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    winner = next(entry for entry in manifest["entries"] if entry.get("winner"))
    snapshot = Path(winner["transcript"]["snapshot_path"])
    snapshot.write_bytes(b"tampered\n")

    with pytest.raises(recovery.RecoveryError, match="transcript snapshot drift"):
        recovery.apply_plan(manifest_path)
    assert not list((tmp_path / "inbox").rglob("*.md"))


def test_recovery_apply_does_not_implicitly_replay_promoted_session(tmp_path, monkeypatch):
    _seed_source(tmp_path)
    processing.append_state(
        tmp_path,
        session_key="claude:s1",
        state="promoted",
        now="2026-07-16T00:00:00Z",
        config_hash="old",
        source_inbox_hash="0" * 64,
        accepted_slices=1,
    )
    manifest_path = _plan(tmp_path, monkeypatch)
    recovery.apply_plan(manifest_path)
    cfg, config_hash = atomizer_config.load_config(override_path=None)

    result = atomizer_pipeline.run(
        tmp_path,
        config=cfg,
        config_hash=config_hash,
        now="2026-07-17T00:00:00Z",
        dry_run=True,
    )

    assert result["summary"]["split_sessions"] == 0
    assert result["warnings"] == []
    assert processing.state_of(tmp_path, "claude:s1") == "promoted"
    recovered = next((tmp_path / "inbox").rglob("*.md")).read_text(encoding="utf-8")
    assert "atomization_replay: false" in recovered


def test_plan_separates_frozen_baseline_from_later_ingress_drift(tmp_path, monkeypatch):
    first = _seed_source(tmp_path, session_id="baseline")
    second = _seed_source(tmp_path, session_id="drift")
    # Stabilize chronological order independently of filesystem timestamp granularity.
    first.touch()
    second.touch()
    manifest_path = recovery.create_plan(tmp_path, batch_size=5, baseline_count=1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["source_count"] == 2
    assert manifest["baseline_source_count"] == 1
    assert manifest["ingress_drift_count"] == 1
    assert sorted(entry["source_set"] for entry in manifest["entries"]) == [
        "baseline",
        "ingress-drift",
    ]


def test_transaction_root_includes_code_pin_and_preserves_prior_journal(tmp_path, monkeypatch):
    _seed_source(tmp_path)
    code_hash = {"value": "a" * 64}
    monkeypatch.setattr(recovery, "_code_hash", lambda: code_hash["value"])

    first_manifest_path = recovery.create_plan(tmp_path, batch_size=5)
    first_manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
    first_journal = Path(first_manifest["transaction_root"]) / "journal.jsonl"
    first_journal.write_bytes(b'{"event":"existing"}\n')

    code_hash["value"] = "b" * 64
    second_manifest_path = recovery.create_plan(tmp_path, batch_size=5)
    second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))

    assert second_manifest["transaction_root"] != first_manifest["transaction_root"]
    assert second_manifest_path != first_manifest_path
    assert first_manifest_path.is_file()
    assert first_journal.read_bytes() == b'{"event":"existing"}\n'


@pytest.mark.parametrize("commit_point", ["begin", "preimage", "staged", "replace", "committed"])
def test_resume_after_each_commit_point_is_byte_equivalent(tmp_path, monkeypatch, commit_point):
    _seed_source(tmp_path)
    historical = tmp_path / "runtime" / "ledger" / "import.jsonl"
    historical.parent.mkdir(parents=True, exist_ok=True)
    historical.write_bytes(b'{"old":true}\n')
    before_ledger = historical.read_bytes()
    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    winner = next(entry for entry in manifest["entries"] if entry.get("winner"))

    with pytest.raises(recovery.RecoveryError, match="injected interruption"):
        recovery.apply_plan(manifest_path, _interrupt_after=commit_point)
    result = recovery.apply_plan(manifest_path, resume=True)

    target = Path(winner["inbox_path"])
    assert target.read_bytes() == Path(winner["planned_artifact"]).read_bytes()
    assert result["complete"] is True
    assert historical.read_bytes() == before_ledger


def test_rollback_restores_preimage_and_does_not_rewrite_jsonl(tmp_path, monkeypatch):
    _seed_source(tmp_path)
    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    winner = next(entry for entry in manifest["entries"] if entry.get("winner"))
    target = Path(winner["inbox_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"preimage\n")
    # Re-plan so the preimage hash is part of the immutable manifest.
    manifest_path = _plan(tmp_path, monkeypatch)
    ledger = tmp_path / "runtime" / "ledger" / "processing.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b'{"historical":true}\n')
    before = ledger.read_bytes()

    recovery.apply_plan(manifest_path)
    result = recovery.rollback_plan(manifest_path)

    assert result["rolled_back"] == 1
    assert target.read_bytes() == b"preimage\n"
    assert ledger.read_bytes() == before


def test_apply_rejects_source_drift_before_writes(tmp_path, monkeypatch):
    source = _seed_source(tmp_path)
    manifest_path = _plan(tmp_path, monkeypatch)
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(recovery.RecoveryError, match="source pin drift"):
        recovery.apply_plan(manifest_path)
    assert not list((tmp_path / "inbox").rglob("*.md"))


def test_apply_rejects_target_drift_before_overwrite(tmp_path, monkeypatch):
    _seed_source(tmp_path)
    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    winner = next(entry for entry in manifest["entries"] if entry.get("winner"))
    target = Path(winner["inbox_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"concurrent newer inbox\n")

    with pytest.raises(recovery.RecoveryError, match="target precondition drift"):
        recovery.apply_plan(manifest_path)

    assert target.read_bytes() == b"concurrent newer inbox\n"


def test_resume_after_first_begin_completes_entire_selected_batch(tmp_path, monkeypatch):
    for session_id in ("a", "b", "c"):
        _seed_source(tmp_path, session_id=session_id)
    manifest_path = recovery.create_plan(tmp_path, batch_size=3)

    with pytest.raises(recovery.RecoveryError, match="injected interruption"):
        recovery.apply_plan(manifest_path, _interrupt_after="begin")
    result = recovery.apply_plan(manifest_path, resume=True)

    assert result["committed"] == 3
    assert result["complete"] is True
    assert len(list((tmp_path / "inbox").rglob("*.md"))) == 3


def test_apply_after_rollback_replays_compensated_batch(tmp_path, monkeypatch):
    _seed_source(tmp_path)
    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    winner = next(entry for entry in manifest["entries"] if entry.get("winner"))
    target = Path(winner["inbox_path"])

    recovery.apply_plan(manifest_path)
    recovery.rollback_plan(manifest_path)
    assert not target.exists()

    result = recovery.apply_plan(manifest_path)

    assert result["committed"] == 1
    assert result["complete"] is True
    assert target.read_bytes() == Path(winner["planned_artifact"]).read_bytes()


def test_apply_rejects_manifest_target_escape(tmp_path, monkeypatch):
    _seed_source(tmp_path)
    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    winner = next(entry for entry in manifest["entries"] if entry.get("winner"))
    winner["inbox_path"] = str(tmp_path.parent / "outside.md")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(recovery.RecoveryError, match="target escapes inbox"):
        recovery.apply_plan(manifest_path)
    assert not (tmp_path.parent / "outside.md").exists()


def test_first_batch_seeds_one_of_each_named_canary_family(tmp_path, monkeypatch):
    summaries = (
        "paulsha-hippo Issue #34 atomization release",
        "health-integrator campaign recovery",
        "paulsha-labu PR #2 recovery",
        "paulsha-hippo Claude recovery",
        "homeclaw Claude recovery",
    )
    for index, summary in enumerate(summaries, 1):
        _seed_source(tmp_path, session_id=f"canary-{index}", summary=summary)

    manifest_path = _plan(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    winners = sorted(
        (entry for entry in manifest["entries"] if entry.get("winner")),
        key=lambda entry: entry["plan_order"],
    )

    assert [entry["canary"] for entry in winners[:5]] == [
        "hippo-issue-34",
        "health-campaign",
        "labu-pr-2",
        "hippo-claude",
        "homeclaw-claude",
    ]
