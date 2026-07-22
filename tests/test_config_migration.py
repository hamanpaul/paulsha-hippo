from __future__ import annotations

import json

import pytest

from paulsha_hippo.config_migration import MigrationError, apply_migration, plan_migration


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
    # The legacy file remains inspectable; selecting canonical is still a
    # semantic no-op and does not copy/retire user-owned legacy data.
    assert apply_migration(second, resolution=resolution)["status"] == "applied"


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
