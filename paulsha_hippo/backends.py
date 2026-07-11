"""Backend preset registry（spec §3.5 契約 7）。

每個 preset 宣告 name / argv template / required executable / doctor probe /
capabilities / available。argv presets 全部走既有 custom-argv 機制
（AgentExecClient：prompt 由 stdin 餵入、stdout 取回輸出），機制零新增。

argv 實測基線（2026-07-10）：
- codex：`codex exec ... -`——stdout 僅含 final message，log 走 stderr。
- copilot：stdin 為唯一 prompt 來源；帶非空 `-p` 時 stdin 注入不可靠（實測
  內容丟失），故不使用 `-p`。
- gemini：unavailable——headless 呼叫僅觀察到 rc=41（selectedType=vertex-ai
  而無 GOOGLE_CLOUD_PROJECT/GOOGLE_API_KEY），無任何成功 stdin→stdout
  round-trip。候選 argv `gemini -p "執行 stdin 提供的任務指示"`（`-p` 文字依
  `--help`「Appended to input on stdin (if any)」推得）未經實證，依 spec §8
  「不猜 argv」不入 template；升級前提見 docs/backend-matrix.md。
- antigravity：命令契約未確認（spec §2 非目標）→ available=False，
  選單顯示但不可選。

capabilities 詞彙：
- "argv-stdin"：CLI 子程序，prompt 走 stdin（AgentExecClient）
- "http"：openai-compatible HTTP API（HttpAgentClient）
- "user-defined"：argv 由使用者 config 提供

本模組 stdlib-only，且不得 import paulsha_hippo.ops（ops 反向 import 本模組）。
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 近似 systemd --user 服務的預設 PATH（非互動 shell；不含 nvm/~/.local 等
# 使用者 shell 追加段）。實際值可用 `systemctl --user show-environment` 查證。
SERVICE_EFFECTIVE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class BackendPreset:
    name: str
    argv_template: list[str]
    required_executable: str | None
    doctor_probe: list[str] | None
    capabilities: frozenset[str]
    available: bool = True


PRESETS: dict[str, BackendPreset] = {
    "claude-headless": BackendPreset(
        name="claude-headless",
        argv_template=["claude", "-p"],
        required_executable="claude",
        doctor_probe=["claude", "--version"],
        capabilities=frozenset({"argv-stdin"}),
    ),
    "codex-headless": BackendPreset(
        name="codex-headless",
        argv_template=["codex", "exec", "--skip-git-repo-check",
                       "--sandbox", "read-only", "--color", "never", "-"],
        required_executable="codex",
        doctor_probe=["codex", "--version"],
        capabilities=frozenset({"argv-stdin"}),
    ),
    "copilot-headless": BackendPreset(
        name="copilot-headless",
        argv_template=["copilot", "-s", "--no-color"],
        required_executable="copilot",
        doctor_probe=["copilot", "--version"],
        capabilities=frozenset({"argv-stdin"}),
    ),
    # unavailable：無成功 round-trip 實證（2026-07-10 僅 rc=41 觀察），依 spec
    # §8 不猜 argv——候選 argv 記錄於模組 docstring 與 docs/backend-matrix.md，
    # round-trip 實證後才填回 template、翻 available（同 PR 補 live smoke）。
    "gemini-headless": BackendPreset(
        name="gemini-headless",
        argv_template=[],
        required_executable="gemini",
        doctor_probe=None,
        capabilities=frozenset(),
        available=False,
    ),
    "antigravity-headless": BackendPreset(
        name="antigravity-headless",
        argv_template=[],
        required_executable="antigravity",
        doctor_probe=None,
        capabilities=frozenset(),
        available=False,
    ),
    "openai-compatible": BackendPreset(
        name="openai-compatible",
        argv_template=[],
        required_executable=None,
        doctor_probe=None,
        capabilities=frozenset({"http"}),
    ),
    "custom-argv": BackendPreset(
        name="custom-argv",
        argv_template=[],
        required_executable=None,
        doctor_probe=None,
        capabilities=frozenset({"argv-stdin", "user-defined"}),
    ),
}


@dataclass(frozen=True)
class ProbeResult:
    preset: str
    available: bool          # registry 宣告；False = 命令契約未確認、選單不可選
    executable: str | None   # 解析到的絕對路徑（service PATH 或互動 PATH）
    ok: bool | None          # probe 執行結果；None = 無 probe 可跑（宣告層判定）
    detail: str              # 人讀摘要（zh-tw）


def service_effective_env() -> dict[str, str]:
    """模擬 systemd --user service 的最小環境（PATH/HOME）。"""
    return {"PATH": SERVICE_EFFECTIVE_PATH, "HOME": str(Path.home())}


def probe_preset(preset: BackendPreset, *, env: dict[str, str] | None = None,
                 timeout: int = 30) -> ProbeResult:
    """以指定環境驗證單一 preset 可用性（doctor 用；只報告、不 gate）。

    env 預設 service_effective_env()：executable 先以 service PATH 解析，
    找不到再退互動 PATH（此時 detail 註記「不在 service PATH」）。probe 子程序
    一律以 env 執行——node-shebang 類 CLI 在 service PATH 缺 node 時會在此
    誠實暴露 rc!=0。
    """
    if not preset.available:
        return ProbeResult(preset.name, False, None, None,
                           "unavailable（命令契約未確認，選單不可選）")
    if preset.required_executable is None:
        return ProbeResult(preset.name, True, None, None,
                           "config 驅動（無本機執行檔需求）")
    probe_env = dict(env) if env is not None else service_effective_env()
    exe_service = shutil.which(preset.required_executable, path=probe_env.get("PATH"))
    exe_user = shutil.which(preset.required_executable)
    exe = exe_service or exe_user
    if exe is None:
        return ProbeResult(preset.name, True, None, False, "executable 未安裝")
    note = "" if exe_service else "（不在 service PATH；由 init 寫入絕對路徑）"
    if preset.doctor_probe is None:
        return ProbeResult(preset.name, True, exe, None, f"無 probe 定義{note}")
    argv = [exe] + list(preset.doctor_probe[1:])
    try:
        completed = subprocess.run(argv, capture_output=True, text=True,
                                   timeout=timeout, env=probe_env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ProbeResult(preset.name, True, exe, False, f"probe 失敗：{exc}"[:200])
    if completed.returncode != 0:
        stream = (completed.stderr or completed.stdout).strip().splitlines()
        head = stream[0] if stream else ""
        return ProbeResult(preset.name, True, exe, False,
                           f"probe rc={completed.returncode}：{head}"[:200] + note)
    stream = (completed.stdout or completed.stderr).strip().splitlines()
    head = stream[0] if stream else "ok"
    return ProbeResult(preset.name, True, exe, True, head[:120] + note)
