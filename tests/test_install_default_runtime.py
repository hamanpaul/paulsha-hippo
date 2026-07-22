from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paulsha_hippo import cli, deployment, install_runtime


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "paulsha_hippo"
MANIFEST = PACKAGE_ROOT / "install-manifest.json"
DEFAULT_PLAN = PACKAGE_ROOT / "install-runtime-plan.json"
REQUIRED_SURFACES = {
    "config",
    "hooks",
    "service",
    "timer",
    "registry-producer",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_package_manifest_and_default_plan_cover_all_owned_surfaces():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    plan = json.loads(DEFAULT_PLAN.read_text(encoding="utf-8"))

    assert DEFAULT_PLAN.is_file()
    assert set(manifest["required_surfaces"]) == REQUIRED_SURFACES
    assert {surface["id"] for surface in manifest["owned_surfaces"]} == REQUIRED_SURFACES
    assert manifest["entries"][0]["kind"] == "create-only"
    assert plan["review"]["manifest_sha256"] == _sha256(MANIFEST)
    assert set(plan["commands"]) == set(deployment.EXTERNAL_INSTALL_PHASES)
    assert set(plan["rollback_commands"]) == set(deployment.ROLLBACK_PHASE_ORDER)


def test_cli_install_uses_packaged_default_runtime_and_is_idempotent(tmp_path: Path, monkeypatch, capsys):
    target = tmp_path / "config"
    transaction = tmp_path / "transaction"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    phases: list[str] = []

    def fake_executor(argv, *, phase, **kwargs):
        phases.append(phase)
        return {"ok": True, "status": "passed", "surface": phase}

    monkeypatch.setattr(install_runtime, "package_runtime_executor", fake_executor)
    monkeypatch.setattr(
        install_runtime, "doctor_gate", lambda context: {"ok": True, "status": "passed"}
    )
    monkeypatch.setattr(
        install_runtime,
        "profile_gate",
        lambda context, profile_id: {
            "ok": True, "status": "passed", "profile_id": profile_id,
        },
    )

    command = [
        "install",
        "all",
        "--force",
        "--target-root",
        str(target),
        "--transaction-root",
        str(transaction),
    ]
    assert cli.main([*command, "--dry-run"]) == 0
    assert not target.exists()

    assert cli.main(command) == 0
    assert phases == list(deployment.EXTERNAL_INSTALL_PHASES)
    state = json.loads((target / ".hippo-install-state.json").read_text(encoding="utf-8"))
    assert set(state["owned_surfaces"]) == REQUIRED_SURFACES

    second = cli.main(command)
    assert second == 0
    output = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert output["status"] == "applied"
    assert output["idempotent"] is True


def test_package_live_runtime_rejects_noncanonical_apply_target(tmp_path: Path, monkeypatch):
    canonical = tmp_path / "canonical"
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(canonical))

    with pytest.raises(deployment.DeploymentError, match="canonical Hippo config root"):
        install_runtime.package_runtime_executor(
            ["@hippo-default-runtime@", "stop_timer_service"],
            phase="stop_timer_service",
            target_root=str(tmp_path / "other"),
            transaction_root=str(tmp_path / "transaction"),
        )


def test_package_runtime_stop_phase_snapshots_and_stops_live_units(tmp_path: Path, monkeypatch):
    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(tmp_path / "memory"))
    monkeypatch.setattr(install_runtime.ops, "_systemd_user_available", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(
        install_runtime,
        "_snapshot",
        lambda resolved: {"schema_version": "1", "units": {}},
    )
    monkeypatch.setattr(
        install_runtime,
        "_stop_units",
        lambda state: calls.append("stop"),
    )

    result = install_runtime.package_runtime_executor(
        ["@hippo-default-runtime@", "stop_timer_service"],
        phase="stop_timer_service",
        target_root=str(target),
        transaction_root=str(transaction),
    )

    assert result["status"] == "passed"
    assert calls == ["stop"]


def test_package_runtime_reinstall_hooks_canonicalizes_without_side_backup(tmp_path: Path, monkeypatch):
    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(tmp_path / "memory"))
    calls: list[object] = []
    monkeypatch.setattr(
        install_runtime.ops,
        "_fix_backend_config",
        lambda *, backup: calls.append(("fix", backup)) or (0, "ok"),
    )
    monkeypatch.setattr(
        install_runtime.ops,
        "run_install_hooks",
        lambda **kwargs: calls.append(("hooks", kwargs)) or 0,
    )
    monkeypatch.setattr(
        install_runtime,
        "_record_config_after",
        lambda resolved: calls.append(("record", resolved["config"])),
    )
    monkeypatch.setattr(
        install_runtime,
        "_migrate_canonical_config",
        lambda resolved: calls.append(("migrate", resolved["config"])),
    )

    result = install_runtime.package_runtime_executor(
        ["@hippo-default-runtime@", "reinstall_hooks"],
        phase="reinstall_hooks",
        target_root=str(target),
        transaction_root=str(transaction),
    )

    assert result["status"] == "passed"
    assert calls[0][0] == "migrate"
    assert calls[1] == ("fix", False)
    assert calls[2][0] == "record"
    assert calls[3][0] == "hooks"


def test_package_runtime_snapshot_and_rollback_restore_existing_config(tmp_path: Path, monkeypatch):
    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    config = target / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("operator: before\n", encoding="utf-8")
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(tmp_path / "memory"))
    monkeypatch.setattr(install_runtime, "_unit_state", lambda name: {"active": False, "enabled": False})

    resolved = install_runtime._runtime_paths(target, transaction)
    state = install_runtime._snapshot(resolved)
    config.write_text("runtime: changed\n", encoding="utf-8")
    install_runtime._record_config_after(resolved)
    state = install_runtime._load_snapshot(resolved)
    install_runtime._restore_config(resolved, state)

    assert config.read_text(encoding="utf-8") == "operator: before\n"


def test_package_runtime_snapshot_rollback_removes_new_config(tmp_path: Path, monkeypatch):
    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(tmp_path / "memory"))
    monkeypatch.setattr(install_runtime, "_unit_state", lambda name: {"active": False, "enabled": False})

    resolved = install_runtime._runtime_paths(target, transaction)
    state = install_runtime._snapshot(resolved)
    config = target / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("runtime: created\n", encoding="utf-8")
    install_runtime._record_config_after(resolved)
    state = install_runtime._load_snapshot(resolved)
    config.unlink()  # filesystem rollback removes the manifest-created file first
    install_runtime._restore_config(resolved, state)

    assert not config.exists()


def test_package_runtime_config_rollback_blocks_concurrent_operator_edit(tmp_path: Path, monkeypatch):
    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    config = target / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("operator: before\n", encoding="utf-8")
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(tmp_path / "memory"))
    monkeypatch.setattr(install_runtime, "_unit_state", lambda name: {"active": False, "enabled": False})

    resolved = install_runtime._runtime_paths(target, transaction)
    install_runtime._snapshot(resolved)
    config.write_text("runtime: normalized\n", encoding="utf-8")
    install_runtime._record_config_after(resolved)
    config.write_text("operator: concurrent-edit\n", encoding="utf-8")

    with pytest.raises(deployment.DeploymentError, match="changed after install mutation"):
        install_runtime._restore_config(resolved, install_runtime._load_snapshot(resolved))
    assert config.read_text(encoding="utf-8") == "operator: concurrent-edit\n"


def test_package_runtime_snapshot_rejects_config_directory(tmp_path: Path, monkeypatch):
    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    (target / "config.yaml").mkdir(parents=True)
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(tmp_path / "memory"))

    with pytest.raises(deployment.DeploymentError, match="regular file or missing"):
        install_runtime._snapshot(install_runtime._runtime_paths(target, transaction))


def test_package_runtime_migrates_installed_legacy_config_without_side_backup(tmp_path: Path, monkeypatch):
    import yaml

    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    config = target / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "memory_root: /operator/memory\n"
        "distiller:\n  backend: openai-compatible\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(tmp_path / "memory"))

    install_runtime._migrate_canonical_config(
        install_runtime._runtime_paths(target, transaction)
    )

    migrated = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert migrated["memory_root"] == "/operator/memory"
    assert "distiller" not in migrated
    assert migrated["external_agents"]["profiles"]
    assert not (target / ".hippo-migration").exists()


def test_package_runtime_migration_blocks_prohibited_provider_field_without_backup(tmp_path: Path, monkeypatch):
    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    config = target / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "distiller:\n  base_url: https://provider.invalid\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    monkeypatch.setenv("HIPPO_MEMORY_ROOT", str(tmp_path / "memory"))

    with pytest.raises(deployment.DeploymentError, match="operator-redaction-required: distiller.base_url"):
        install_runtime._migrate_canonical_config(
            install_runtime._runtime_paths(target, transaction)
        )
    assert "provider.invalid" in config.read_text(encoding="utf-8")
    assert not (target / ".hippo-migration").exists()


def test_package_runtime_gates_call_real_doctor_modes(tmp_path: Path, monkeypatch):
    target = tmp_path / "canonical"
    transaction = tmp_path / "transaction"
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(target))
    calls: list[tuple[bool, bool]] = []
    monkeypatch.setattr(
        install_runtime.ops,
        "run_doctor",
        lambda *, live_probe, probe_profiles: calls.append((live_probe, probe_profiles)) or 0,
    )
    context = deployment.InstallContext(
        manifest_path=MANIFEST,
        target_root=target,
        transaction_root=transaction,
        journal_path=transaction / "transaction.json",
        profile_id="package-default",
        phase="doctor_static",
    )

    assert install_runtime.doctor_gate(context)["ok"] is True
    assert install_runtime.profile_gate(context, "package-default")["ok"] is True
    assert calls == [(False, False), (False, True)]


def test_explicit_runtime_plan_requires_review_binding(tmp_path: Path):
    target = tmp_path / "target"
    plan = tmp_path / "runtime.json"
    commands = {
        phase: ["true"] for phase in deployment.EXTERNAL_INSTALL_PHASES
    }
    rollback = {phase: ["true"] for phase in deployment.ROLLBACK_PHASE_ORDER}
    plan.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "runtime_kind": "reviewed-override",
                "commands": commands,
                "rollback_commands": rollback,
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(
        [
            "install",
            "all",
            "--force",
            "--dry-run",
            "--manifest",
            str(MANIFEST),
            "--target-root",
            str(target),
            "--runtime-plan",
            str(plan),
        ]
    ) == 1
    assert not target.exists()


def test_manifest_missing_required_owned_surface_fails_closed(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "required_surfaces": ["config", "hooks"],
                "owned_surfaces": [
                    {"id": "config", "mode": "target-root"},
                    {"id": "hooks", "mode": "runtime"},
                ],
                "entries": [{"path": "config.yaml", "content": "schema_version: 1\n"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(deployment.DeploymentError, match="required owned surface"):
        deployment.plan_install(manifest, target_root=tmp_path / "target")
