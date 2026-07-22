from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_installed_install_all_isolated_and_idempotent(installed_hippo: tuple[Path, Path], isolated_env: dict[str, str], tmp_path: Path):
    executable, _sandbox = installed_hippo
    target = tmp_path / "owned-config"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": "1", "entries": [{"path": "owned.yaml", "content": "a: 1\n"}]}),
        encoding="utf-8",
    )
    command = [
        str(executable).replace("/python", "/hippo"), "install", "all", "--force",
        "--manifest", str(manifest), "--target-root", str(target),
    ]
    subprocess.run(command, cwd="/tmp", env=isolated_env, check=True, capture_output=True, text=True)
    second = subprocess.run(command, cwd="/tmp", env=isolated_env, check=True, capture_output=True, text=True)
    assert (target / "owned.yaml").read_text(encoding="utf-8") == "a: 1\n"
    assert json.loads(second.stdout)["status"] == "applied"
