from __future__ import annotations

import json

import pytest

from paulsha_hippo.config_migration import (
    MigrationError,
    apply_migration,
    plan_migration,
    rollback_migration,
)


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_nonempty_provider_field_blocks_before_any_apply_or_backup(tmp_path):
    canonical = tmp_path / "canonical.yaml"
    legacy = tmp_path / "legacy.yaml"
    _write(canonical, "external_agents:\n  profiles: []\n")
    _write(legacy, "agent_exec:\n  api_key_env: SECRET_NAME\n")
    plan = plan_migration(canonical, legacy)
    assert plan.status == "blocked"
    assert plan.reason == "operator-redaction-required"
    assert plan.prohibited_fields == ("agent_exec.api_key_env",)
    assert "SECRET_NAME" not in json.dumps(plan.as_dict())
    with pytest.raises(MigrationError, match="operator-redaction-required"):
        apply_migration(plan)
    assert not (tmp_path / "canonical.yaml.bak").exists()


def test_conflict_requires_hash_bound_resolution_and_is_idempotent(tmp_path):
    canonical = tmp_path / "canonical.yaml"
    legacy = tmp_path / "legacy.yaml"
    _write(canonical, "schema_version: 1\nvalue: canonical\n")
    _write(legacy, "schema_version: 1\nvalue: legacy\n")
    plan = plan_migration(canonical, legacy)
    assert plan.status == "conflict"
    with pytest.raises(MigrationError, match="hash-bound-resolution"):
        apply_migration(plan)
    resolution = {
        "plan_hash": plan.plan_hash,
        "canonical_hash": plan.canonical_hash,
        "legacy_hash": plan.legacy_hash,
        "selected_source": "canonical",
    }
    result = apply_migration(plan, resolution=resolution)
    assert result["status"] == "applied"
    second = plan_migration(canonical, legacy)
    second_resolution = {
        "plan_hash": second.plan_hash,
        "canonical_hash": second.canonical_hash,
        "legacy_hash": second.legacy_hash,
        "selected_source": "canonical",
    }
    assert apply_migration(second, resolution=second_resolution)["status"] == "no-op"


def test_source_drift_rejects_reviewed_resolution(tmp_path):
    canonical = tmp_path / "canonical.yaml"
    legacy = tmp_path / "legacy.yaml"
    _write(canonical, "value: canonical\n")
    _write(legacy, "value: legacy\n")
    plan = plan_migration(canonical, legacy)
    resolution = {
        "plan_hash": plan.plan_hash,
        "canonical_hash": plan.canonical_hash,
        "legacy_hash": plan.legacy_hash,
        "selected_source": "legacy",
    }
    legacy.write_text("value: changed\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="source drift"):
        apply_migration(plan, resolution=resolution)


def test_mapping_plan_loader_does_not_mutate_reviewed_payload(tmp_path):
    canonical = tmp_path / "canonical.yaml"
    _write(canonical, "value: canonical\n")
    payload = plan_migration(canonical).as_dict()
    original = dict(payload)

    apply_migration(payload, dry_run=True)

    assert payload == original


def test_migration_retires_old_transport_and_rolls_back_hash_bound(tmp_path):
    import yaml

    canonical = tmp_path / "config.yaml"
    _write(
        canonical,
        "memory_root: /safe/memory\n"
        "distiller:\n  backend: openai-compatible\n"
        "agent_exec:\n  command: [claude, -p]\n",
    )
    plan = plan_migration(canonical)
    result = apply_migration(plan)
    migrated = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    assert "distiller" not in migrated
    assert "agent_exec" not in migrated
    assert migrated["memory_root"] == "/safe/memory"
    assert migrated["external_agents"]["profiles"]
    assert result["backup"]

    assert rollback_migration(result)["status"] == "rolled-back"
    restored = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    assert restored["distiller"]["backend"] == "openai-compatible"


def test_migration_rollback_blocks_concurrent_canonical_edit(tmp_path):
    canonical = tmp_path / "config.yaml"
    _write(canonical, "memory_root: /safe\n")
    result = apply_migration(plan_migration(canonical))
    canonical.write_text(canonical.read_text() + "operator_value: keep\n")
    with pytest.raises(MigrationError, match="drift blocks rollback"):
        rollback_migration(result)


def test_transaction_owned_migration_skips_persistent_side_backup(tmp_path):
    canonical = tmp_path / "config.yaml"
    _write(
        canonical,
        "memory_root: /safe/memory\n"
        "distiller:\n  backend: openai-compatible\n",
    )

    result = apply_migration(plan_migration(canonical), backup=False)

    assert result["status"] == "applied"
    assert result["backup"] is None
    assert result["external_snapshot_required"] is True
    assert not (tmp_path / ".hippo-migration").exists()
    with pytest.raises(MigrationError, match="external transaction snapshot required"):
        rollback_migration(result)
