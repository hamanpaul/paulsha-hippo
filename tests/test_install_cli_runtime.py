from __future__ import annotations

import json
import hashlib
from pathlib import Path

from paulsha_hippo import cli, deployment


def _runtime_plan(tmp_path: Path) -> Path:
    commands = {
        phase: ["systemctl", "--user", "status", phase]
        for phase in deployment.EXTERNAL_INSTALL_PHASES
    }
    rollback = {
        phase: ["systemctl", "--user", "status", phase]
        for phase in deployment.ROLLBACK_PHASE_ORDER
    }
    path = tmp_path / "runtime.json"
    payload = {
        "schema_version": "1",
        "runtime_kind": "reviewed-override",
        "review": {"status": "approved"},
        "commands": commands,
        "rollback_commands": rollback,
    }
    manifest = Path(__file__).resolve().parents[1] / "paulsha_hippo" / "install-manifest.json"
    payload["review"]["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    unsigned = dict(payload)
    unsigned.pop("review")
    canonical = (json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    payload["review"]["plan_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_install_dry_run_without_runtime_plan_uses_package_default(tmp_path: Path, capsys):
    target = tmp_path / "target"
    assert cli.main([
        "install", "all", "--force", "--dry-run", "--target-root", str(target)
    ]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["status"] == "dry-run"
    assert not target.exists()


def test_install_dry_run_validates_reviewed_runtime_plan(tmp_path: Path):
    target = tmp_path / "target"
    assert cli.main(
        [
            "install", "all", "--force", "--dry-run",
            "--target-root", str(target),
            "--runtime-plan", str(_runtime_plan(tmp_path)),
        ]
    ) == 0
    assert not target.exists()
