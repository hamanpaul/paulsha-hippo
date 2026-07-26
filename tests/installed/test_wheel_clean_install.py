from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_installed_usage_funnel_contract_and_coverage(
    installed_hippo: tuple[Path, Path], isolated_env: dict[str, str]
):
    executable, _sandbox = installed_hippo
    hippo = str(executable).replace("/python", "/hippo")
    memory_root = Path(isolated_env["HIPPO_MEMORY_ROOT"])
    ledger = memory_root / "runtime" / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "offered.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-07-26T01:00:00+00:00",
                        "session_id": "s-c1",
                        "tool": "claude-code",
                        "offered": ["sl-a", "sl-b"],
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-07-26T01:00:00+00:00",
                        "session_id": "s-cx",
                        "tool": "codex",
                        "offered": ["sl-c"],
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-07-26T01:00:00+00:00",
                        "session_id": "s-cop",
                        "tool": "copilot-cli",
                        "offered": [{"sl_id": "sl-d", "path": "/tmp/slice.md"}],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (ledger / "memory_usage.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-07-26T01:00:10+00:00",
                        "session_id": "s-c1",
                        "tool": "claude-code",
                        "sl_id": "sl-a",
                        "source": "read",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-07-26T01:00:11+00:00",
                        "session_id": "s-c1",
                        "tool": "claude-code",
                        "sl_id": "sl-b",
                        "source": "read",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-07-26T01:00:20+00:00",
                        "session_id": "s-cx",
                        "tool": "codex",
                        "sl_id": "sl-offtopic",
                        "source": "read",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-07-26T01:00:30+00:00",
                        "session_id": "s-cx",
                        "tool": "codex",
                        "kind": "applied",
                        "slice_id": "sl-c",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            hippo,
            "usage",
            "funnel",
            "--memory-root",
            str(memory_root),
            "--json",
        ],
        cwd="/tmp",
        env=isolated_env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["selected_mode"] == "without-noise"
    assert payload["summary"] == {
        "sessions_offered": 3,
        "sessions_with_read": 1,
        "read_through_rate": 33.33,
        "sessions_with_applied": 1,
        "applied_rate": 33.33,
        "unique_slice_coverage": {"offered": 4, "read": 2, "read_rate": 50.0},
    }
    assert payload["by_tool"]["claude-code"]["unique_slice_coverage"] == {
        "offered": 2,
        "read": 2,
        "read_rate": 100.0,
    }
    assert payload["by_tool"]["claude-code"]["sessions_with_applied"] == 0
    assert payload["by_tool"]["codex"]["unique_slice_coverage"] == {
        "offered": 1,
        "read": 0,
        "read_rate": 0.0,
    }
    assert payload["by_tool"]["codex"]["sessions_with_read"] == 0
    assert payload["by_tool"]["codex"]["sessions_with_applied"] == 1
    assert payload["by_tool"]["copilot-cli"]["unique_slice_coverage"] == {
        "offered": 1,
        "read": 0,
        "read_rate": 0.0,
    }
    assert payload["by_tool"]["copilot-cli"]["applied_rate"] == 0.0


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
        "assert (root/'install-manifest.json').is_file(); "
        "assert (root/'install-runtime-plan.json').is_file()"
    )
    subprocess.run([str(executable), "-c", code], cwd="/tmp", env=isolated_env, check=True)
