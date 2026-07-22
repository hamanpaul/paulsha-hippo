from __future__ import annotations

import json
from pathlib import Path

import pytest

from paulsha_hippo.upgrade import (
    PHASE_ORDER,
    ROLLBACK_PHASE_ORDER,
    UpgradeError,
    apply_upgrade,
    plan_upgrade,
    prepare_upgrade,
    rollback_upgrade,
)


def _commands() -> tuple[dict[str, list[list[str]]], dict[str, list[list[str]]]]:
    phase_commands = {
        phase: [["hippo", "upgrade-test", phase]] for phase in PHASE_ORDER
    }
    rollback_commands = {
        phase: [["hippo", "upgrade-test", phase]] for phase in ROLLBACK_PHASE_ORDER
    }
    return phase_commands, rollback_commands


class RecordingRunner:
    def __init__(self, *, candidate_sha256: str, fail_phase: str | None = None):
        self.candidate_sha256 = candidate_sha256
        self.fail_phase = fail_phase
        self.failed = False
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def __call__(self, argv, *, phase, profile_id, timeout, env, cwd):
        self.calls.append((phase, tuple(argv)))
        assert timeout > 0
        assert "HOME" not in env
        assert "API_KEY" not in env
        assert env["HIPPO_UPGRADE_PHASE"] == phase
        if phase == self.fail_phase and not self.failed:
            self.failed = True
            return {"returncode": 17}
        if phase == "project_registry_producer_wiring":
            return {
                "status": "ok",
                "registry_producer_wired": True,
                "registry_contract_consumed": True,
                "registry_hash": "registry-contract-sha",
            }
        if phase == "effective_profile_verification":
            return {
                "status": "ok",
                "profile_id": profile_id,
                "artifact_sha256": self.candidate_sha256,
            }
        return {"returncode": 0}


def _prepared(tmp_path: Path):
    candidate = tmp_path / "candidate.whl"
    candidate.write_bytes(b"candidate")
    target_root = tmp_path / "release"
    target_root.mkdir()
    target = target_root / "hippo.whl"
    target.write_bytes(b"old")
    phase_commands, rollback_commands = _commands()
    planned = plan_upgrade(
        candidate,
        target_root=target_root,
        profile_id="co-gem",
        phase_commands=phase_commands,
        rollback_commands=rollback_commands,
    )
    prepared = prepare_upgrade(planned, transaction_root=tmp_path / "tx")
    return candidate, target, prepared, planned


def test_upgrade_is_ordered_hash_pinned_and_second_apply_is_idempotent(tmp_path: Path):
    _, target, prepared, planned = _prepared(tmp_path)
    manifest = Path(prepared["manifest"])
    before = target.read_bytes()

    dry_run = apply_upgrade(manifest, dry_run=True)
    assert dry_run["status"] == "dry-run"
    assert dry_run["mutation"] is False
    assert target.read_bytes() == before

    runner = RecordingRunner(candidate_sha256=planned["candidate_sha256"])
    applied = apply_upgrade(manifest, force=True, runner=runner)
    assert applied["status"] == "applied"
    assert target.read_bytes() == b"candidate"
    assert [phase for phase, _ in runner.calls] == list(PHASE_ORDER)

    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    assert evidence["state"] == "applied"
    assert evidence["write_ahead"] is True
    assert evidence["candidate_sha256"] == planned["candidate_sha256"]
    assert evidence["previous_sha256"] == planned["current_sha256"]
    assert all(evidence["evidence"]["phases"][phase]["status"] == "passed" for phase in PHASE_ORDER)
    assert evidence["evidence"]["phases"]["project_registry_producer_wiring"]["result"][
        "registry_contract_consumed"
    ] is True

    second_runner = RecordingRunner(candidate_sha256=planned["candidate_sha256"], fail_phase="stop_drain")
    second = apply_upgrade(manifest, force=True, runner=second_runner)
    assert second["status"] == "already-applied"
    assert second["idempotent"] is True
    assert second_runner.calls == []


def test_failed_service_phase_rolls_back_artifact_and_attempts_old_surfaces(tmp_path: Path):
    _, target, prepared, planned = _prepared(tmp_path)
    manifest = Path(prepared["manifest"])
    runner = RecordingRunner(
        candidate_sha256=planned["candidate_sha256"],
        fail_phase="service_reinstall",
    )

    with pytest.raises(UpgradeError, match="automatic rolled-back"):
        apply_upgrade(manifest, force=True, runner=runner)

    assert target.read_bytes() == b"old"
    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    assert evidence["state"] == "rolled-back"
    assert evidence["failure"]["phase"] == "service_reinstall"
    assert evidence["rollback"]["artifact"]["restored_sha256"] == planned["current_sha256"]
    assert evidence["evidence"]["rollback_phases"]["rollback_hook_restore"]["status"] == "passed"
    assert evidence["evidence"]["rollback_phases"]["rollback_service_restore"]["status"] == "passed"
    assert [phase for phase, _ in runner.calls] == [
        *PHASE_ORDER[:4],
        *ROLLBACK_PHASE_ORDER,
    ]

    retried = apply_upgrade(manifest, force=True, runner=runner)
    assert retried["status"] == "applied"
    assert target.read_bytes() == b"candidate"
    assert [phase for phase, _ in runner.calls[-len(PHASE_ORDER) :]] == list(PHASE_ORDER)


def test_candidate_drift_fails_closed_before_switch(tmp_path: Path):
    _, target, prepared, _ = _prepared(tmp_path)
    manifest = Path(prepared["manifest"])
    Path(prepared["candidate_copy"]).write_bytes(b"drift")

    with pytest.raises(UpgradeError, match="drifted"):
        apply_upgrade(manifest, force=True, runner=RecordingRunner(candidate_sha256="unused"))

    assert target.read_bytes() == b"old"
    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    assert evidence["state"] == "prepared"


def test_effective_profile_mismatch_rolls_back_and_fails_closed(tmp_path: Path):
    _, target, prepared, planned = _prepared(tmp_path)
    manifest = Path(prepared["manifest"])

    class MismatchRunner(RecordingRunner):
        def __call__(self, argv, *, phase, profile_id, timeout, env, cwd):
            result = super().__call__(argv, phase=phase, profile_id=profile_id, timeout=timeout, env=env, cwd=cwd)
            if phase == "effective_profile_verification":
                return {"status": "ok", "profile_id": "wrong-profile", "artifact_sha256": self.candidate_sha256}
            return result

    with pytest.raises(UpgradeError, match="automatic rolled-back"):
        apply_upgrade(
            manifest,
            force=True,
            runner=MismatchRunner(candidate_sha256=planned["candidate_sha256"]),
        )

    assert target.read_bytes() == b"old"
    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    assert evidence["state"] == "rolled-back"
    assert "profile" in evidence["failure"]["reason"]


def test_registry_consumer_attestation_is_required(tmp_path: Path):
    _, target, prepared, planned = _prepared(tmp_path)
    manifest = Path(prepared["manifest"])

    class MissingConsumerRunner(RecordingRunner):
        def __call__(self, argv, *, phase, profile_id, timeout, env, cwd):
            result = super().__call__(argv, phase=phase, profile_id=profile_id, timeout=timeout, env=env, cwd=cwd)
            if phase == "project_registry_producer_wiring":
                return {"status": "ok", "registry_producer_wired": True}
            return result

    with pytest.raises(UpgradeError, match="automatic rolled-back"):
        apply_upgrade(
            manifest,
            force=True,
            runner=MissingConsumerRunner(candidate_sha256=planned["candidate_sha256"]),
        )

    assert target.read_bytes() == b"old"
    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    assert evidence["state"] == "rolled-back"
    assert evidence["failure"]["phase"] == "project_registry_producer_wiring"


def test_manual_rollback_is_hash_bound_and_idempotent(tmp_path: Path):
    _, target, prepared, planned = _prepared(tmp_path)
    manifest = Path(prepared["manifest"])
    runner = RecordingRunner(candidate_sha256=planned["candidate_sha256"])
    apply_upgrade(manifest, force=True, runner=runner)

    rolled_back = rollback_upgrade(manifest, runner=runner)
    assert rolled_back["status"] == "rolled-back"
    assert target.read_bytes() == b"old"
    assert rollback_upgrade(manifest, runner=runner)["status"] == "already-rolled-back"


def test_unsafe_shell_or_protected_command_is_rejected(tmp_path: Path):
    candidate = tmp_path / "candidate.whl"
    candidate.write_bytes(b"candidate")
    target_root = tmp_path / "release"
    target_root.mkdir()

    with pytest.raises(UpgradeError, match="shell"):
        plan_upgrade(
            candidate,
            target_root=target_root,
            phase_commands={"stop_drain": [["bash", "-c", "systemctl stop hippo"]]},
        )
    with pytest.raises(UpgradeError, match="credential|protected"):
        plan_upgrade(
            candidate,
            target_root=target_root,
            phase_commands={"stop_drain": [["hippo", "--api-key", "value"]]},
        )


def test_apply_requires_complete_phase_plan_before_mutation(tmp_path: Path):
    candidate = tmp_path / "candidate.whl"
    candidate.write_bytes(b"candidate")
    target_root = tmp_path / "release"
    target_root.mkdir()
    target = target_root / "hippo.whl"
    target.write_bytes(b"old")
    prepared = prepare_upgrade(
        plan_upgrade(candidate, target_root=target_root),
        transaction_root=tmp_path / "tx",
    )

    with pytest.raises(UpgradeError, match="command plan is incomplete"):
        apply_upgrade(
            prepared["manifest"],
            force=True,
            runner=RecordingRunner(candidate_sha256=prepared["candidate_sha256"]),
        )
    assert target.read_bytes() == b"old"
