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
