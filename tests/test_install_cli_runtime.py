from __future__ import annotations

import json
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
    path.write_text(
        json.dumps({"commands": commands, "rollback_commands": rollback}),
        encoding="utf-8",
    )
    return path


def test_install_apply_without_runtime_plan_fails_before_mutation(tmp_path: Path):
    target = tmp_path / "target"
    assert cli.main(["install", "all", "--force", "--target-root", str(target)]) == 1
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
