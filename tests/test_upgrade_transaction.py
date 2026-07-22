from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from paulsha_hippo import upgrade as upgrade_tx
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


def _prepared_for_default_runner(tmp_path: Path):
    candidate = tmp_path / "candidate.whl"
    candidate.write_bytes(b"candidate")
    target_root = tmp_path / "release"
    target_root.mkdir()
    target = target_root / "hippo.whl"
    target.write_bytes(b"old")
    candidate_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
    runner_script = tmp_path / "phase_runner.py"
    runner_script.write_text(
        "import json\n"
        "import os\n"
        "phase = os.environ['HIPPO_UPGRADE_PHASE']\n"
        f"candidate_sha256 = {candidate_sha256!r}\n"
        "if phase == 'project_registry_producer_wiring':\n"
        "    result = {'status': 'ok', 'registry_producer_wired': True, 'registry_contract_consumed': True, 'registry_hash': 'registry-contract-sha'}\n"
        "elif phase == 'effective_profile_verification':\n"
        "    result = {'status': 'ok', 'profile_id': os.environ['HIPPO_PROFILE_ID'], 'artifact_sha256': candidate_sha256}\n"
        "else:\n"
        "    result = {'status': 'ok'}\n"
        "print(json.dumps(result))\n",
        encoding="utf-8",
    )
    argv = [sys.executable, str(runner_script)]
    phase_commands = {phase: [argv] for phase in PHASE_ORDER}
    rollback_commands = {phase: [argv] for phase in ROLLBACK_PHASE_ORDER}
    planned = plan_upgrade(
        candidate,
        target_root=target_root,
        profile_id="co-gem",
        phase_commands=phase_commands,
        rollback_commands=rollback_commands,
        allowed_executables=[sys.executable],
    )
    prepared = prepare_upgrade(planned, transaction_root=tmp_path / "tx")
    return candidate, target, prepared, planned, argv


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


def test_default_runner_consumes_json_attestation_and_keeps_process_boundary(tmp_path: Path, monkeypatch):
    script = tmp_path / "runner.py"
    script.write_text(
        "import json\n"
        "import os\n"
        "print(json.dumps({'status': 'ok', 'profile_id': os.environ['HIPPO_PROFILE_ID']}))\n",
        encoding="utf-8",
    )
    calls: dict[str, object] = {}
    real_popen = upgrade_tx.subprocess.Popen

    def recording_popen(*args, **kwargs):
        calls.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(upgrade_tx.subprocess, "Popen", recording_popen)
    env = {"PATH": "/usr/bin", "LC_ALL": "C", "HIPPO_UPGRADE_PHASE": "doctor", "HIPPO_PROFILE_ID": "co-gem"}
    result = upgrade_tx._default_runner(
        [sys.executable, str(script)],
        phase="doctor",
        profile_id="co-gem",
        timeout=5.0,
        env=env,
        cwd=str(tmp_path),
    )

    assert result == {"profile_id": "co-gem", "returncode": 0, "status": "ok"}
    assert calls["shell"] is False
    assert calls["env"] == env
    assert calls["stdin"] is subprocess.DEVNULL
    assert calls["stdout"] is subprocess.PIPE
    assert calls["stderr"] is subprocess.DEVNULL


def test_default_runner_attestation_is_used_by_real_apply(tmp_path: Path):
    _, target, prepared, planned, _ = _prepared_for_default_runner(tmp_path)

    result = apply_upgrade(prepared["manifest"], force=True)

    assert result["status"] == "applied"
    assert target.read_bytes() == b"candidate"
    evidence = json.loads(Path(prepared["manifest"]).read_text(encoding="utf-8"))
    producer = evidence["evidence"]["phases"]["project_registry_producer_wiring"]["result"]
    profile = evidence["evidence"]["phases"]["effective_profile_verification"]["result"]
    assert producer["registry_producer_wired"] is True
    assert producer["registry_contract_consumed"] is True
    assert profile["profile_id"] == planned["profile_id"]
    assert profile["artifact_sha256"] == planned["candidate_sha256"]


def _direct_runner_script(tmp_path: Path, source: str) -> list[str]:
    script = tmp_path / "direct_runner.py"
    script.write_text(source, encoding="utf-8")
    return [sys.executable, str(script)]


def _runner_env(tmp_path: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "LC_ALL": "C",
        "HIPPO_UPGRADE_PHASE": "doctor",
        "HIPPO_PROFILE_ID": "co-gem",
    }


def test_default_runner_rejects_invalid_json_without_persisting_raw_output(tmp_path: Path):
    argv = _direct_runner_script(tmp_path, "print('raw output that must not be journaled')\n")

    with pytest.raises(UpgradeError, match="valid JSON") as exc_info:
        upgrade_tx._default_runner(
            argv,
            phase="doctor",
            profile_id="co-gem",
            timeout=5.0,
            env=_runner_env(tmp_path),
            cwd=str(tmp_path),
        )

    assert getattr(exc_info.value, "returncode") == 0
    assert "raw output" not in str(exc_info.value)


def test_default_runner_rejects_oversize_and_secret_stdout(tmp_path: Path):
    oversized = _direct_runner_script(
        tmp_path,
        "print('x' * 20000)\n",
    )
    with pytest.raises(UpgradeError, match="bounded JSON limit") as oversize_error:
        upgrade_tx._default_runner(
            oversized,
            phase="doctor",
            profile_id="co-gem",
            timeout=5.0,
            env=_runner_env(tmp_path),
            cwd=str(tmp_path),
        )
    assert "x" * 100 not in str(oversize_error.value)

    secret = _direct_runner_script(
        tmp_path,
        "import json\nprint(json.dumps({'status': 'ok', 'api_key': 'do-not-persist'}))\n",
    )
    with pytest.raises(UpgradeError, match="protected data") as secret_error:
        upgrade_tx._default_runner(
            secret,
            phase="doctor",
            profile_id="co-gem",
            timeout=5.0,
            env=_runner_env(tmp_path),
            cwd=str(tmp_path),
        )
    assert "do-not-persist" not in str(secret_error.value)


def test_default_runner_preserves_nonzero_returncode_with_safe_json_failure(tmp_path: Path):
    argv = _direct_runner_script(
        tmp_path,
        "import json\nimport sys\nprint(json.dumps({'status': 'failed'}))\nsys.exit(23)\n",
    )

    result = upgrade_tx._default_runner(
        argv,
        phase="doctor",
        profile_id="co-gem",
        timeout=5.0,
        env=_runner_env(tmp_path),
        cwd=str(tmp_path),
    )

    assert result == {"returncode": 23, "status": "failed"}


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
