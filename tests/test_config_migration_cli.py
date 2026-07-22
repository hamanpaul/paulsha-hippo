from __future__ import annotations

import json
from pathlib import Path

from paulsha_hippo import cli


def test_config_migration_cli_plan_apply_and_rollback(tmp_path: Path, capsys):
    canonical = tmp_path / "config.yaml"
    legacy = tmp_path / "atomizer.override.yaml"
    legacy.write_text("memory_root: /safe/memory\npromoter: identity\n", encoding="utf-8")
    plan = tmp_path / "plan.json"
    report = tmp_path / "report.json"

    assert cli.main(
        [
            "config",
            "migrate",
            "plan",
            "--canonical",
            str(canonical),
            "--legacy",
            str(legacy),
            "--out",
            str(plan),
        ]
    ) == 0
    assert json.loads(plan.read_text(encoding="utf-8"))["status"] == "ready"

    assert cli.main(
        ["config", "migrate", "apply", "--plan", str(plan), "--out", str(report)]
    ) == 0
    assert canonical.is_file()
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "applied"

    capsys.readouterr()
    assert cli.main(["config", "migrate", "rollback", "--report", str(report)]) == 0
    assert not canonical.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "rolled-back"


def test_config_migration_cli_blocks_credential_shape_without_copy(tmp_path: Path, capsys):
    canonical = tmp_path / "config.yaml"
    legacy = tmp_path / "atomizer.override.yaml"
    legacy.write_text("agent_exec:\n  api_key_env: PRIVATE_NAME\n", encoding="utf-8")
    plan = tmp_path / "plan.json"

    assert cli.main(
        [
            "config",
            "migrate",
            "plan",
            "--canonical",
            str(canonical),
            "--legacy",
            str(legacy),
            "--out",
            str(plan),
        ]
    ) == 0
    assert json.loads(plan.read_text(encoding="utf-8"))["reason"] == "operator-redaction-required"
    assert cli.main(["config", "migrate", "apply", "--plan", str(plan)]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE_NAME" not in output
    assert not canonical.exists()
