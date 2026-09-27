"""issue #157 現象 1 根因：copilot CLI 1.0.88 拒收以 ``-`` 開頭的 ``-p`` 值。

atomize prompt 一律以 skill 的 YAML frontmatter（``---``）開頭。copilot CLI
1.0.88 的參數解析器把 ``-p "---..."`` 的值當成旗標，回
``error: Invalid command format. It looks like your prompt was not quoted…``
並 exit 1（以無憑證的拋棄式 HOME 實測：``-p 'hello'`` 進到認證階段，
``-p $'---\\n…'`` 立即被拒；``--prompt=$'---\\n…'`` 則正常進到認證階段）。
所以 cg 對每一個蒸餾 prompt 都 1.9 秒必敗，與模型或內容無關。

這裡用假的 copilot 執行檔記錄 launcher 實際傳入的 argv，驗證 prompt 以單一
``--prompt=<prompt>`` token 綁定，不再經過會被誤判為旗標的 ``-p <value>``。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_CORE = (
    Path(__file__).resolve().parents[1]
    / "contrib" / "local-harness" / "launchers" / "hippo-copilot-headless-core"
)

_FAKE_COPILOT = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_COPILOT_ARGV_LOG"], "w", encoding="utf-8") as handle:
    json.dump(sys.argv[1:], handle)
args = sys.argv[1:]
if "-p" in args:
    value = args[args.index("-p") + 1] if args.index("-p") + 1 < len(args) else ""
    if value.startswith("-"):
        sys.stderr.write("error: Invalid command format.\\n")
        sys.exit(1)
sys.stdout.write('{"schema_version":1,"disposition":"no_findings","reason":"x","findings":[]}')
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="launcher is a bash script")
def test_launcher_binds_hyphen_leading_prompt_as_single_prompt_token(tmp_path):
    home = tmp_path / "home"
    fake_bin = home / ".nvm" / "versions" / "node" / "v0" / "bin"
    fake_bin.mkdir(parents=True)
    copilot = fake_bin / "copilot"
    copilot.write_text(_FAKE_COPILOT, encoding="utf-8")
    copilot.chmod(0o755)
    env_file = tmp_path / "launcher.env"
    env_file.write_text("COPILOT_PROVIDER_API_KEY=placeholder-for-test\n", encoding="utf-8")
    argv_log = tmp_path / "argv.json"
    prompt = "---\nname: atomize-knowledge-slice\n---\n合成測試 prompt\n"

    completed = subprocess.run(
        ["bash", str(_CORE), "--model", "default", "--effort", "high", "--headless", "--stdin"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "TMPDIR": str(tmp_path),
            "HIPPO_COPILOT_ENV_FILE": str(env_file),
            "FAKE_COPILOT_ARGV_LOG": str(argv_log),
        },
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["disposition"] == "no_findings"
    argv = json.loads(argv_log.read_text(encoding="utf-8"))
    assert "-p" not in argv
    assert f"--prompt={prompt.rstrip(chr(10))}" in argv or f"--prompt={prompt}" in argv
