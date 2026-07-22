from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_installed_install_all_dry_run_and_apply_gate(installed_hippo: tuple[Path, Path], isolated_env: dict[str, str], tmp_path: Path):
    executable, _sandbox = installed_hippo
    target = tmp_path / "owned-config"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": "1", "entries": [{"path": "owned.yaml", "content": "a: 1\n"}]}),
        encoding="utf-8",
    )
    base_command = [
        str(executable).replace("/python", "/hippo"), "install", "all", "--force",
        "--manifest", str(manifest), "--target-root", str(target),
    ]
    dry_run = subprocess.run(
        [*base_command, "--dry-run"], cwd="/tmp", env=isolated_env,
        check=True, capture_output=True, text=True,
    )
    assert json.loads(dry_run.stdout)["status"] == "dry-run"
    assert not target.exists()

    blocked = subprocess.run(
        base_command, cwd="/tmp", env=isolated_env,
        check=False, capture_output=True, text=True,
    )
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["status"] == "blocked"
    assert not target.exists()
