from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_installed_cli_runs_outside_checkout(installed_hippo: tuple[Path, Path], isolated_env: dict[str, str]):
    executable, _sandbox = installed_hippo
    result = subprocess.run(
        [str(executable).replace("/python", "/hippo"), "--version", "--json"],
        cwd="/tmp",
        env=isolated_env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["version"] == "0.1.1"
    assert payload["build_commit"] != "unknown"
    assert isinstance(payload["source_dirty"], bool)
    assert payload["install_root"]
    assert payload["package_root"]


def test_installed_config_and_manifest_assets_are_packaged(installed_hippo: tuple[Path, Path], isolated_env: dict[str, str]):
    executable, _sandbox = installed_hippo
    code = (
        "from importlib.resources import files; "
        "root=files('paulsha_hippo'); "
        "assert (root/'atomizer'/'atomizer.yaml').is_file(); "
        "assert (root/'install-manifest.json').is_file()"
    )
    subprocess.run([str(executable), "-c", code], cwd="/tmp", env=isolated_env, check=True)
