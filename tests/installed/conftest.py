from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def installed_hippo(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[2]
    sandbox = tmp_path_factory.mktemp("installed-hippo")
    wheel_dir = sandbox / "dist"
    wheel_dir.mkdir()
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(root), "--no-deps", "--wheel-dir", str(wheel_dir)],
        cwd=str(root),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert wheels
    venv = sandbox / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, stdout=subprocess.PIPE, text=True)
    executable = venv / "bin" / "python"
    subprocess.run([str(executable), "-m", "pip", "install", "--no-deps", str(wheels[-1])], check=True, stdout=subprocess.PIPE, text=True)
    return executable, sandbox


@pytest.fixture
def isolated_env(installed_hippo: tuple[Path, Path], tmp_path: Path) -> dict[str, str]:
    _python, _sandbox = installed_hippo
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["HIPPO_CONFIG_ROOT"] = str(tmp_path / "config")
    env["HIPPO_MEMORY_ROOT"] = str(tmp_path / "memory")
    env["PYTHONPATH"] = ""
    return env
