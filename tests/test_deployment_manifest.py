from __future__ import annotations

import json

import pytest

from paulsha_hippo import deployment


def _manifest(path, entries):
    path.write_text(json.dumps({"schema_version": "1", "entries": entries}), encoding="utf-8")


def test_force_dry_run_does_not_mutate_and_apply_is_idempotent(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    _manifest(manifest, [{"path": "owned.txt", "content": "v1\n"}])
    dry = deployment.install_all(manifest_path=manifest, target_root=target, force=True, dry_run=True)
    assert dry["status"] == "dry-run"
    assert not (target / "owned.txt").exists()
    deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    assert (target / "owned.txt").read_text() == "v1\n"
    assert (target / ".hippo-install-state.json").is_file()
    second = deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    assert second["status"] == "applied"
    assert (target / "owned.txt").read_text() == "v1\n"


def test_unowned_change_blocks_force_and_protected_paths_are_rejected(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    _manifest(manifest, [{"path": "owned.txt", "content": "v1\n"}])
    deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    (target / "owned.txt").write_text("user edit\n")
    with pytest.raises(deployment.DeploymentError, match="ownership conflict"):
        deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    _manifest(manifest, [{"path": "runtime/ledger/processing.jsonl", "content": "x"}])
    with pytest.raises(deployment.DeploymentError, match="protected"):
        deployment.plan_install(manifest, target_root=target)


@pytest.mark.parametrize(
    "path",
    [
        ".local/bin/copilot",
        "bin/external-agent",
        ".config/systemd/user/hippo.service",
        ".config/environment.d/agent.conf",
        ".config/openai/credentials.json",
    ],
)
def test_external_launchers_and_credential_stores_are_protected(tmp_path, path):
    manifest = tmp_path / "manifest.json"
    _manifest(manifest, [{"path": path, "content": "must not be touched\n"}])
    with pytest.raises(deployment.DeploymentError, match="protected"):
        deployment.plan_install(manifest, target_root=tmp_path / "target")


def test_shared_file_keeps_user_entry_and_rollback_restores_only_owned_keys(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    shared = target / "shared.json"
    shared.parent.mkdir()
    shared.write_text(json.dumps({"user": "keep", "hippo": "old"}), encoding="utf-8")
    _manifest(manifest, [{"path": "shared.json", "kind": "shared-json", "owned_entries": {"hippo": "new"}}])
    deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    assert json.loads(shared.read_text()) == {"user": "keep", "hippo": "new"}
    # A later manifest removes the owned key; the user entry remains.
    _manifest(manifest, [])
    result = deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    assert json.loads(shared.read_text()) == {"user": "keep"}
    assert not list((target / ".hippo-install-backups").glob("**/preimage/shared.json"))
    assert result["status"] == "applied"


def test_shared_rollback_blocks_a_concurrent_owned_key_edit(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    shared = target / "shared.json"
    shared.parent.mkdir()
    shared.write_text(json.dumps({"user": "keep", "hippo": "old"}), encoding="utf-8")
    _manifest(manifest, [{"path": "shared.json", "kind": "shared-json", "owned_entries": {"hippo": "new"}}])
    applied = deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    transaction = applied["transaction"]
    shared.write_text(json.dumps({"user": "keep", "hippo": "operator-edit"}), encoding="utf-8")

    rollback = deployment.rollback_install(transaction)

    assert rollback["status"] == "rollback-blocked"
    assert "shared.json:hippo" in rollback["conflicts"]
    assert json.loads(shared.read_text()) == {"user": "keep", "hippo": "operator-edit"}


def _runtime(target, calls, *, fail_phase=None, profile_fail=False, rollback_fail_phase=None, rollback_location=None):
    commands = {phase: (phase,) for phase in deployment.EXTERNAL_INSTALL_PHASES}
    rollback_commands = {phase: (phase,) for phase in deployment.ROLLBACK_PHASE_ORDER}

    def execute(argv, **kwargs):
        phase = kwargs["phase"]
        calls.append(("command", phase, tuple(argv), kwargs["env"], kwargs["cwd"]))
        if phase == fail_phase or phase == rollback_fail_phase:
            return {"ok": False, "status": "failed"}
        return {"ok": True}

    def doctor(context):
        calls.append(("doctor", context.phase, context.profile_id))
        return {"ok": True, "status": "passed"}

    def probe(context, profile_id):
        calls.append(("probe", context.phase, profile_id))
        if profile_fail:
            return {"ok": False, "status": "failed", "profile_id": profile_id}
        return {"ok": True, "status": "passed", "profile_id": profile_id}

    runner = deployment.AllowlistedCommandRunner(
        commands, executor=execute, target_root=target
    )
    rollback_runner = deployment.AllowlistedCommandRunner(
        rollback_commands, executor=execute, target_root=target
    )
    return deployment.InstallRuntime(
        commands=commands,
        rollback_commands=rollback_commands,
        command_runner=runner,
        rollback_runner=rollback_runner,
        doctor_static=doctor,
        profile_probe=probe,
        profile_id="claude",
        runner_location=None,
        rollback_runner_location=rollback_location,
        rollback_target_root=target,
    )


def test_runtime_transaction_records_fence_reload_probes_and_is_idempotent(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    tx = tmp_path / "transaction"
    _manifest(manifest, [{"path": "owned.txt", "content": "candidate\n"}])
    calls = []
    runtime = _runtime(target, calls)

    applied = deployment.install_all(
        manifest_path=manifest,
        target_root=target,
        force=True,
        transaction_root=tx,
        runtime=runtime,
    )

    assert applied["status"] == "applied"
    journal = json.loads((tx / "transaction.json").read_text(encoding="utf-8"))
    assert journal["state"] == "committed"
    assert all(journal["phases"][phase]["status"] == "passed" for phase in deployment.INSTALL_PHASE_ORDER)
    command_phases = [entry[1] for entry in calls if entry[0] == "command"]
    assert command_phases == list(deployment.EXTERNAL_INSTALL_PHASES)
    assert [entry[0] for entry in calls if entry[0] in {"doctor", "probe"}] == ["doctor", "probe"]
    assert all(entry[3]["PATH"] for entry in calls if entry[0] == "command")
    assert all("HOME" not in entry[3] for entry in calls if entry[0] == "command")

    call_count = len(calls)
    second = deployment.install_all(
        manifest_path=manifest,
        target_root=target,
        force=True,
        transaction_root=tx,
        runtime=runtime,
    )
    assert second["status"] == "applied"
    assert second["idempotent"] is True
    assert len(calls) == call_count + 2
    assert [entry[0] for entry in calls[-2:]] == ["doctor", "probe"]


def test_runtime_failure_rolls_back_filesystem_and_external_phases(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    tx = tmp_path / "transaction"
    _manifest(manifest, [{"path": "owned.txt", "content": "candidate\n"}])
    calls = []
    runtime = _runtime(target, calls, fail_phase="reinstall_service")

    with pytest.raises(deployment.DeploymentError, match="automatic rolled-back"):
        deployment.install_all(
            manifest_path=manifest,
            target_root=target,
            force=True,
            transaction_root=tx,
            runtime=runtime,
        )

    assert not (target / "owned.txt").exists()
    assert not (target / ".hippo-install-state.json").exists()
    journal = json.loads((tx / "transaction.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rolled-back"
    assert journal["filesystem_rollback"]["status"] == "rolled-back"
    rollback_calls = [entry[1] for entry in calls if entry[0] == "command" and entry[1].startswith("rollback")]
    assert "rollback_reinstall_service" in rollback_calls
    assert "rollback_stop_timer_service" in rollback_calls


def test_runtime_boundary_and_argv_validation_fail_before_mutation(tmp_path):
    target = tmp_path / "target"
    with pytest.raises(deployment.DeploymentError, match="shell wrapper"):
        deployment.AllowlistedCommandRunner(
            {"stop_timer_service": ("bash", "-c", "systemctl stop x")}
        )
    with pytest.raises(deployment.DeploymentError, match="credential-shaped"):
        deployment.AllowlistedCommandRunner(
            {"stop_timer_service": ("systemctl", "--api-key", "value")}
        )

    manifest = tmp_path / "manifest.json"
    tx = tmp_path / "transaction"
    _manifest(manifest, [{"path": "owned.txt", "content": "candidate\n"}])
    calls = []
    runtime = _runtime(target, calls, rollback_location=target / "rollback-runner")
    with pytest.raises(deployment.DeploymentError, match="independent"):
        deployment.install_all(
            manifest_path=manifest,
            target_root=target,
            force=True,
            transaction_root=tx,
            runtime=runtime,
        )
    assert not (target / "owned.txt").exists()


def test_shared_hash_drift_in_unowned_entry_is_preserved(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    shared = target / "shared.json"
    shared.parent.mkdir()
    shared.write_text(json.dumps({"user": "before", "hippo": "old"}), encoding="utf-8")
    _manifest(manifest, [{"path": "shared.json", "kind": "shared-json", "owned_entries": {"hippo": "new"}}])
    first = deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    shared.write_text(json.dumps({"user": "operator-edit", "hippo": "new"}), encoding="utf-8")
    second = deployment.install_all(manifest_path=manifest, target_root=target, force=True)

    assert second["idempotent"] is True
    assert json.loads(shared.read_text(encoding="utf-8")) == {"user": "operator-edit", "hippo": "new"}
    assert first["applied"][0]["kind"] == "shared-json"


def test_create_only_preserves_existing_user_config_and_tracks_no_ownership(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    config = target / "config.yaml"
    config.parent.mkdir()
    config.write_text("memory_root: /custom\nprofile: operator\n", encoding="utf-8")
    _manifest(manifest, [{"path": "config.yaml", "kind": "create-only", "content": "default: true\n"}])

    result = deployment.install_all(manifest_path=manifest, target_root=target, force=True)

    assert result["status"] == "applied"
    assert config.read_text(encoding="utf-8") == "memory_root: /custom\nprofile: operator\n"
    state = json.loads((target / ".hippo-install-state.json").read_text(encoding="utf-8"))
    assert state["entries"]["config.yaml"]["kind"] == "create-only"
    assert state["entries"]["config.yaml"]["created_by_install"] is False


def test_create_only_creates_missing_config_but_never_overwrites_later_edits(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    _manifest(manifest, [{"path": "config.yaml", "kind": "create-only", "content": "default: true\n"}])

    deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    config = target / "config.yaml"
    assert config.read_text(encoding="utf-8") == "default: true\n"
    config.write_text("operator: edited\n", encoding="utf-8")

    second = deployment.install_all(manifest_path=manifest, target_root=target, force=True)

    assert second["idempotent"] is True
    assert config.read_text(encoding="utf-8") == "operator: edited\n"


def test_retired_create_only_entry_forgets_preexisting_file_without_removing_it(tmp_path):
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target"
    config = target / "config.yaml"
    config.parent.mkdir()
    config.write_text("operator: keep\n", encoding="utf-8")
    _manifest(manifest, [{"path": "config.yaml", "kind": "create-only", "content": "default: true\n"}])
    deployment.install_all(manifest_path=manifest, target_root=target, force=True)
    _manifest(manifest, [])

    deployment.install_all(manifest_path=manifest, target_root=target, force=True)

    assert config.read_text(encoding="utf-8") == "operator: keep\n"
    state = json.loads((target / ".hippo-install-state.json").read_text(encoding="utf-8"))
    assert "config.yaml" not in state["entries"]
