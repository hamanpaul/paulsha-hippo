from __future__ import annotations

import json
from pathlib import Path

from paulsha_hippo import cli, upgrade


def _commands() -> tuple[dict[str, list[list[str]]], dict[str, list[list[str]]]]:
    phases = {phase: [["hippo", "phase", phase]] for phase in upgrade.PHASE_ORDER}
    rollback = {
        phase: [["hippo", "phase", phase]]
        for phase in upgrade.ROLLBACK_PHASE_ORDER
    }
    return phases, rollback


def test_upgrade_plan_cli_accepts_reviewed_command_plan(tmp_path: Path):
    candidate = tmp_path / "candidate.whl"
    candidate.write_bytes(b"wheel")
    phase_commands, rollback_commands = _commands()
    commands = tmp_path / "commands.json"
    commands.write_text(
        json.dumps(
            {
                "phase_commands": phase_commands,
                "rollback_commands": rollback_commands,
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "plan.json"

    assert cli.main(
        [
            "upgrade", "plan", "--candidate", str(candidate),
            "--target-root", str(tmp_path / "artifacts"),
            "--command-plan", str(commands), "--out", str(out),
        ]
    ) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["phase_commands"] == phase_commands
    assert plan["rollback_commands"] == rollback_commands


def test_upgrade_plan_cli_rejects_unknown_command_plan_field(tmp_path: Path):
    candidate = tmp_path / "candidate.whl"
    candidate.write_bytes(b"wheel")
    commands = tmp_path / "commands.json"
    commands.write_text('{"credential_file":"never"}', encoding="utf-8")

    assert cli.main(
        [
            "upgrade", "plan", "--candidate", str(candidate),
            "--target-root", str(tmp_path / "artifacts"),
            "--command-plan", str(commands),
            "--out", str(tmp_path / "plan.json"),
        ]
    ) == 1
