from __future__ import annotations

from pathlib import Path

from paulsha_hippo.upgrade import apply_upgrade, plan_upgrade, prepare_upgrade, rollback_upgrade


def test_upgrade_prepare_apply_rollback_is_hash_bound(tmp_path: Path):
    candidate = tmp_path / "candidate.whl"
    candidate.write_bytes(b"candidate")
    target_root = tmp_path / "release"
    target_root.mkdir()
    target = target_root / "hippo.whl"
    target.write_bytes(b"old")

    planned = plan_upgrade(candidate, target_root=target_root, profile_id="co-gem")
    prepared = prepare_upgrade(planned, transaction_root=tmp_path / "tx")
    manifest = Path(prepared["manifest"])

    applied = apply_upgrade(manifest, force=True)
    assert applied["status"] == "applied"
    assert target.read_bytes() == b"candidate"
    assert applied["service_verification"] == "pending"

    rolled_back = rollback_upgrade(manifest)
    assert rolled_back["status"] == "rolled-back"
    assert target.read_bytes() == b"old"


def test_upgrade_requires_force_and_detects_candidate_drift(tmp_path: Path):
    candidate = tmp_path / "candidate.whl"
    candidate.write_bytes(b"candidate")
    plan = plan_upgrade(candidate, target_root=tmp_path / "release")
    prepared = prepare_upgrade(plan, transaction_root=tmp_path / "tx")
    manifest = Path(prepared["manifest"])
    try:
        apply_upgrade(manifest)
    except ValueError as exc:
        assert "force" in str(exc)
    else:
        raise AssertionError("upgrade apply must require --force")

    Path(prepared["candidate_copy"]).write_bytes(b"drift")
    try:
        apply_upgrade(manifest, force=True)
    except ValueError as exc:
        assert "drifted" in str(exc)
    else:
        raise AssertionError("candidate drift must fail closed")
