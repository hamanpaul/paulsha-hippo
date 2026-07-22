# PR-D Backend 矩陣（#10）Implementation Plan

> **歷史文件／已取代：** 本計畫中的 `atomizer.override.yaml`、`agent_exec`、
> `--agent-command`、`openai-compatible` 與 repo 內 provider/API-key 管理已由
> `openspec/changes/issue-34-atomization-release/` 的 accepted 設計取代。
> 現行 runtime 只讀 Hippo canonical `config.yaml`，並透過宣告式 external CLI
> agent profiles 路由；本文件僅保留歷史脈絡，不可作為操作或實作指引。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把蒸餾 backend 從三個硬編碼檔位升級為宣告式 preset registry：codex/copilot headless 依本機實測 argv 接線、gemini（僅 rc=41 auth 失敗觀察、無 round-trip 實證）與 antigravity 誠實標 unavailable；`init --backend` 選單化、`doctor` per-preset probe、mock 情境矩陣＋真蒸餾 smoke＋openai-compatible integration smoke＋supervise 無 systemd E2E 全鏈補齊，#10 以 `Refs` 收斂——gemini 固定缺項由收口批次拆新 issue 後關單。

**Architecture:** 新增 `paulsha_hippo/backends.py` 作為 preset 單一真源（跨批次契約 7），`ops.py` 的 `_BACKENDS`／`run_init`／`run_doctor` 改為 registry 驅動；三個已驗 argv preset（claude/codex/copilot）全部是既有 custom-argv 機制（`AgentExecClient`：prompt 走 stdin、stdout 取回輸出）的宣告式包裝，機制零新增；gemini/antigravity 標 unavailable（無成功 round-trip 實證，§8 不猜 argv）。驗證分四層：registry/probe 單元測試 → mock 情境矩陣（散文包 JSON／截斷／non-zero／timeout）→ 真蒸餾 smoke（env gate ×3 available preset ＋ openai-compatible 真端點）→ `dream supervise --once` 無 systemd E2E。

**Tech Stack:** Python 3.10+ stdlib（dataclasses／shutil／subprocess／argparse／fcntl）＋ unittest/pytest；PyYAML 僅測試沿用既有 dev 依賴。runtime 零新依賴。

## Global Constraints

以下逐字抄自 spec（`docs/superpowers/specs/2026-07-10-all-issues-resolution-design.md`），每個 Task 隱含全部適用：

- §3.3.5／§3.5.2：「stdlib-only、零新依賴」「全部是 `custom-argv` 機制的 preset 包裝，機制零新增」。
- §7：「分支一律 `feature/<issue>-<slug>`；禁 commit main。」→ 本批分支：`feature/10-backend-matrix`。
- §7：「每 code PR：changelog.d 碎片（repo 現行慣例）、PR checklist 全勾、`Closes #N`（R-17）、zh-tw（語言規範）、`policy_check` 零 failure。」
- §7：「`tier: shareable`（R-21）：所有新增文件（含本 spec、capability matrix、快照留言）不得含個人絕對路徑、機敏標記。」→ 本 plan 與所有新增檔案一律用 `~`／repo 相對路徑表示法。
- §7：「R-18/R-22：behavior 變更同步 README／docs 引用（`hippo recall`、`--backend` 選單、doctor 新輸出）。」→ 本批負責 `--backend` 選單與 doctor 新輸出的 docs 同步。
- §7：「測試新增全部進 CI 覆蓋（R-19；`tests.yml` 已自動跑 pytest）。」→ env-gated 測試在 CI 無 credential 時為 skip（仍被收集執行）。
- §2 非目標：「不 bump `VERSION`」「antigravity preset 不實作（標 unavailable，命令契約確認後另補）」。
- §1：「所有驗收一律以執行時實測為準，不得寫死歷史數字。」
- §8 風險表：「真蒸餾 smoke 受各 CLI 認證/配額影響 → smoke 標記可重試；CLI 不可用時該 preset 標 skip 並回報，不擋批次但影響 #10 關單條件（§3.5）」「gemini/copilot headless 介面與預期不符 → preset 以實測為準；接不上就 registry 標 unavailable + 回報，不猜 argv」。
- §6 拓撲：「`D` await C（C、D 都動 `ops.py`，明確序列化，不再並行）」→ **本 plan 動工前置：PR-C 已 merge 進 main、worktree 由最新 main 切出**。
- commit message 一律 zh-tw conventional-commit。
- plan 內所有 `Modify` 行號區間以 2026-07-10 main 快照為準；PR-A／PR-C 已 merge 會使行號偏移，**實作時以內容錨點（引用的原始碼片段）定位，不盲信行號**。

## 跨批次共享介面契約（本批消費／提供；偏離即 bug）

**本批提供（契約 7）**——新檔 `paulsha_hippo/backends.py`：

```python
@dataclass(frozen=True)
class BackendPreset:
    name: str
    argv_template: list[str]
    required_executable: str | None
    doctor_probe: list[str] | None
    capabilities: frozenset[str]
    available: bool = True

PRESETS: dict[str, BackendPreset]
```

且 `paulsha_hippo/ops.py` 的 `_BACKENDS` 改由 registry 導出：`_BACKENDS = tuple(PRESETS)`（保持向後相容）。

**本批消費**：
- 契約 2（PR-A）：`ops.resolve_backend_argv(argv: list[str]) -> list[str]`——argv[0] 以 `shutil.which` 絕對路徑化，找不到 raise `BackendUnavailableError`（`ValueError` 子類，同在 `ops.py`）。
- 契約 1（PR-A）：processing 狀態機 `VALID_STATES = {"split","promoted","skipped","parked"}`；`parked` 事件 extra 欄位 `failure_category`（`"backend_unavailable"|"transient"|"invalid_output"`）、`attempts:int`、`cache_key:str`、`error:str`（截斷 ≤500 字元、去敏）。
- PR-A §3.1.3：毒快取淘汰後失敗證據落 `<memory_root>/runtime/queue/_failed/`。
- 契約 3（PR-A）：global dream lock 固定路徑 `<memory_root>/runtime/locks/dream.lock`（supervise E2E 在 tmp root 下自然隔離；本批不另實作）。
- 契約 5：CLI 子命令一律走 `cli.py` 的 `memory_subparsers.add_parser` 既有模式。

## 本機實測基線（2026-07-10，本 plan 撰寫時實測；smoke 設計依據）

| CLI | 安裝 | headless round-trip 實測 | 備註 |
|---|---|---|---|
| `claude` | ✓（原生執行檔） | ✓ `claude -p`（v0.1.0 既有已驗檔位） | `--version` rc=0 |
| `codex` | ✓（node script） | ✓ `codex exec --skip-git-repo-check --sandbox read-only --color never -`：stdin 進 prompt、**stdout 僅含 final message**（header/進度/token 統計全走 stderr）、rc=0 | `--version` rc=0 |
| `copilot` | ✓（bash wrapper） | ✓ `copilot -s --no-color`：**stdin 為唯一 prompt 來源**、stdout 僅回覆本文、rc=0。⚠ 帶非空 `-p` 時 stdin 注入不可靠（多次實測內容丟失、agent 徘徊找 stdin 至 timeout），故 preset **不用 `-p`** | `--version` rc=0 |
| `gemini` | ✓（node script） | ✗ rc=41：本機認證選了 vertex-ai 但無對應 env（`GOOGLE_CLOUD_PROJECT`/`GOOGLE_API_KEY`）——**無任何成功 stdin→stdout round-trip**。候選 argv 僅由 `--help` 文字推得（`-p`「Appended to input on stdin (if any)」），未經實證 | `--version` rc=0；依 §8 風險表「不猜 argv」→ registry 標 unavailable（與 antigravity 同級），不入 smoke 矩陣；升級前提見 `docs/backend-matrix.md` |
| `antigravity` | ✗ 不存在 | — | registry 標 unavailable |

另：`codex`/`gemini` 為 `#!/usr/bin/env node` script——即使 argv[0] 絕對路徑化，systemd service PATH 無 node 目錄時仍會啟動失敗；`claude` 為原生執行檔無此問題。doctor 的 service-effective probe 必須能暴露這類故障（Task 4）。

## File Structure（本批全部檔案與職責）

- Create `paulsha_hippo/backends.py`——preset registry（契約 7）＋ `ProbeResult`／`probe_preset()`／`service_effective_env()`。單一職責：宣告與探測，不 import `ops`（`ops` 反向 import 本模組）。
- Modify `paulsha_hippo/ops.py`——`_BACKENDS` 導出、`run_init` registry 驅動＋argv 絕對路徑化、`run_doctor` preset 矩陣。
- Modify `paulsha_hippo/cli.py`——`init --backend` choices 選單化；`dream supervise` 增 `--once/--max-load/--promoter/--agent-command`。
- Create `tests/test_backends.py`、`tests/test_atomizer_backend_matrix.py`、`tests/test_dream_supervise_e2e.py`、`tests/test_openai_smoke_integration.py`。
- Create `tests/fixtures/atomizer/{prose,truncated,failing,hanging}-agent.py`（mock backends）。
- Modify `tests/test_ops.py`（init/doctor/supervise 測試）、`tests/test_atomizer_llm_live.py`（真蒸餾 smoke 矩陣）。
- Create `docs/backend-matrix.md`；Modify `README.md`＋`CHANGELOG.md`（`[Unreleased]`，R-09 gate）；Create `changelog.d/feature-10-backend-matrix.md`。

---

### Task 1: `paulsha_hippo/backends.py` preset registry ＋ probe

**Files:**
- Create: `paulsha_hippo/backends.py`
- Test: `tests/test_backends.py`

**Interfaces:**
- Consumes:（無——stdlib only；本模組禁止 import `paulsha_hippo.ops`，避免循環）
- Produces:
  - `BackendPreset(name: str, argv_template: list[str], required_executable: str | None, doctor_probe: list[str] | None, capabilities: frozenset[str], available: bool = True)`（frozen dataclass，契約 7 逐字）
  - `PRESETS: dict[str, BackendPreset]`（定義序：claude-headless → codex-headless → copilot-headless → gemini-headless → antigravity-headless → openai-compatible → custom-argv）
  - `SERVICE_EFFECTIVE_PATH: str`
  - `service_effective_env() -> dict[str, str]`
  - `ProbeResult(preset: str, available: bool, executable: str | None, ok: bool | None, detail: str)`（frozen dataclass）
  - `probe_preset(preset: BackendPreset, *, env: dict[str, str] | None = None, timeout: int = 30) -> ProbeResult`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_backends.py`，完整內容：

```python
"""Backend preset registry（PR-D 契約 7）單元測試。"""
from __future__ import annotations

import os
import stat
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import backends


class RegistryContractTests(unittest.TestCase):
    def test_presets_expose_contract_names(self):
        for expected in (
            "claude-headless", "codex-headless", "copilot-headless",
            "gemini-headless", "antigravity-headless",
            "openai-compatible", "custom-argv",
        ):
            self.assertIn(expected, backends.PRESETS)

    def test_default_preset_is_first_and_claude(self):
        self.assertEqual(next(iter(backends.PRESETS)), "claude-headless")

    def test_preset_dataclass_is_frozen(self):
        preset = backends.PRESETS["claude-headless"]
        with self.assertRaises(FrozenInstanceError):
            preset.name = "x"

    def test_names_match_keys(self):
        for key, preset in backends.PRESETS.items():
            self.assertEqual(key, preset.name)

    def test_unverified_presets_marked_unavailable(self):
        """gemini：僅 rc=41（auth 未備）觀察、無成功 round-trip 實證——依 spec
        §8「不猜 argv」標 unavailable；antigravity：命令契約未確認（spec §2）。"""
        for name in ("gemini-headless", "antigravity-headless"):
            with self.subTest(preset=name):
                preset = backends.PRESETS[name]
                self.assertFalse(preset.available)
                self.assertEqual(preset.argv_template, [])

    def test_argv_presets_use_stdin_mechanism(self):
        for name in ("claude-headless", "codex-headless", "copilot-headless"):
            with self.subTest(preset=name):
                preset = backends.PRESETS[name]
                self.assertTrue(preset.available)
                self.assertIn("argv-stdin", preset.capabilities)
                self.assertTrue(preset.argv_template)
                self.assertEqual(preset.argv_template[0], preset.required_executable)
                self.assertEqual(
                    preset.doctor_probe, [preset.required_executable, "--version"])

    def test_verified_argv_templates(self):
        """2026-07-10 本機實測定案的 argv（見 plan「本機實測基線」）。"""
        self.assertEqual(backends.PRESETS["claude-headless"].argv_template,
                         ["claude", "-p"])
        self.assertEqual(
            backends.PRESETS["codex-headless"].argv_template,
            ["codex", "exec", "--skip-git-repo-check",
             "--sandbox", "read-only", "--color", "never", "-"])
        # copilot：實測帶非空 -p 時 stdin 注入不可靠——stdin 必須是唯一 prompt 來源
        self.assertEqual(backends.PRESETS["copilot-headless"].argv_template,
                         ["copilot", "-s", "--no-color"])
        # gemini-headless：候選 argv 未經 round-trip 實證，不入 registry
        # template（unavailable；升級前提見 docs/backend-matrix.md）。

    def test_http_and_custom_presets_have_no_argv_template(self):
        for name in ("openai-compatible", "custom-argv"):
            with self.subTest(preset=name):
                preset = backends.PRESETS[name]
                self.assertEqual(preset.argv_template, [])
                self.assertIsNone(preset.required_executable)
                self.assertIsNone(preset.doctor_probe)
        self.assertIn("http", backends.PRESETS["openai-compatible"].capabilities)
        self.assertIn("user-defined", backends.PRESETS["custom-argv"].capabilities)


class ProbeTests(unittest.TestCase):
    def _make_bin(self, dir_path: Path, name: str, script: str) -> Path:
        path = dir_path / name
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def test_service_effective_env_shape(self):
        env = backends.service_effective_env()
        self.assertEqual(env["PATH"], backends.SERVICE_EFFECTIVE_PATH)
        self.assertNotIn("nvm", env["PATH"])
        self.assertTrue(env["HOME"])

    def test_probe_ok_with_stub_executable(self):
        with TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._make_bin(bin_dir, "codex", "#!/bin/sh\necho fake-codex 9.9\nexit 0\n")
            result = backends.probe_preset(
                backends.PRESETS["codex-headless"],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp})
        self.assertTrue(result.ok)
        self.assertEqual(result.executable, str(bin_dir / "codex"))
        self.assertIn("fake-codex 9.9", result.detail)

    def test_probe_failure_reports_rc_and_stderr(self):
        # rc=41 情境取材自 gemini 實測；gemini-headless 已標 unavailable（probe
        # 宣告層短路），故以 available 的 copilot-headless stub 驗證 rc/stderr 回報。
        with TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._make_bin(bin_dir, "copilot", "#!/bin/sh\necho auth broken >&2\nexit 41\n")
            result = backends.probe_preset(
                backends.PRESETS["copilot-headless"],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp})
        self.assertFalse(result.ok)
        self.assertIn("rc=41", result.detail)
        self.assertIn("auth broken", result.detail)

    def test_probe_missing_executable(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"PATH": tmp}):
                result = backends.probe_preset(
                    backends.PRESETS["copilot-headless"],
                    env={"PATH": tmp, "HOME": tmp})
        self.assertFalse(result.ok)
        self.assertIsNone(result.executable)
        self.assertIn("未安裝", result.detail)

    def test_probe_notes_user_only_path_visibility(self):
        with TemporaryDirectory() as tmp:
            user_bin = Path(tmp) / "user-bin"
            user_bin.mkdir()
            self._make_bin(user_bin, "claude", "#!/bin/sh\necho 9.9 fake\nexit 0\n")
            service_bin = Path(tmp) / "svc-bin"
            service_bin.mkdir()
            with mock.patch.dict(os.environ, {"PATH": f"{user_bin}:/usr/bin:/bin"}):
                result = backends.probe_preset(
                    backends.PRESETS["claude-headless"],
                    env={"PATH": f"{service_bin}:/usr/bin:/bin", "HOME": tmp})
        self.assertTrue(result.ok)
        self.assertIn("不在 service PATH", result.detail)

    def test_probe_unavailable_and_config_driven_short_circuit(self):
        for name in ("gemini-headless", "antigravity-headless"):
            with self.subTest(preset=name):
                result = backends.probe_preset(backends.PRESETS[name])
                self.assertFalse(result.available)
                self.assertIsNone(result.ok)
                self.assertIn("unavailable", result.detail)
        openai = backends.probe_preset(backends.PRESETS["openai-compatible"])
        self.assertTrue(openai.available)
        self.assertIsNone(openai.ok)
        self.assertIn("config 驅動", openai.detail)

    def test_probe_timeout_is_failure_not_crash(self):
        with TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            self._make_bin(bin_dir, "codex", "#!/bin/sh\nsleep 30\n")
            result = backends.probe_preset(
                backends.PRESETS["codex-headless"],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": tmp},
                timeout=1)
        self.assertFalse(result.ok)
        self.assertIn("probe 失敗", result.detail)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 FAIL**

執行：`python3 -m pytest tests/test_backends.py -v`
預期：collection error——`ModuleNotFoundError: No module named 'paulsha_hippo.backends'`

- [ ] **Step 3: 最小實作**

建立 `paulsha_hippo/backends.py`，完整內容：

```python
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
```

- [ ] **Step 4: 跑測試確認 PASS**

執行：`python3 -m pytest tests/test_backends.py -v`
預期：全部 PASS（15 tests）。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/backends.py tests/test_backends.py
git commit -m "feat(backends): preset registry（契約 7）＋ service-effective probe——codex/copilot 實測 argv；gemini（無 round-trip 實證）/antigravity 標 unavailable"
```

---

### Task 2: `_BACKENDS` registry 導出 ＋ `init --backend` 選單化（antigravity 不可選）

**Files:**
- Modify: `paulsha_hippo/ops.py:6-20`（import 區與 `_BACKENDS`）、`paulsha_hippo/ops.py:33-38`（`run_init` 驗證頭）
- Modify: `paulsha_hippo/cli.py:48-56`（init subparser）
- Test: `tests/test_ops.py`（新增 class）

**Interfaces:**
- Consumes: `backends.PRESETS`（Task 1）
- Produces: `ops._BACKENDS == tuple(backends.PRESETS)`（契約 7 向後相容面）；CLI `hippo init --backend` choices＝registry 全 keys（含 gemini-headless／antigravity-headless，執行期以 rc 2 拒絕）；help 文字對 unavailable preset 標「（尚不可用）」

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_ops.py` 檔尾（`InstallHooksResolverTests` class 之後）加入：

```python
class BackendRegistryWiringTests(unittest.TestCase):
    def test_backends_tuple_derived_from_registry(self):
        from paulsha_hippo import backends
        self.assertEqual(ops._BACKENDS, tuple(backends.PRESETS))

    def test_init_rejects_unknown_backend(self):
        rc = ops.run_init(memory_root=None, backend="definitely-not-a-backend",
                          base_url=None, api_key_env=None, model=None, assume_yes=True)
        self.assertEqual(rc, 2)

    def test_init_rejects_unavailable_presets(self):
        for name in ("gemini-headless", "antigravity-headless"):
            with self.subTest(backend=name):
                rc = ops.run_init(memory_root=None, backend=name, base_url=None,
                                  api_key_env=None, model=None, assume_yes=True)
                self.assertEqual(rc, 2)


class InitBackendChoicesTests(unittest.TestCase):
    def test_parser_accepts_all_registry_presets(self):
        from paulsha_hippo import backends
        from paulsha_hippo.cli import _build_parser
        parser = _build_parser()
        for name in backends.PRESETS:
            with self.subTest(backend=name):
                args = parser.parse_args(["init", "--backend", name])
                self.assertEqual(args.backend, name)

    def test_parser_rejects_non_registry_backend(self):
        from paulsha_hippo.cli import _build_parser
        with self.assertRaises(SystemExit):
            _build_parser().parse_args(["init", "--backend", "definitely-not-a-backend"])
```

- [ ] **Step 2: 跑測試確認 FAIL**

執行：`python3 -m pytest tests/test_ops.py::BackendRegistryWiringTests tests/test_ops.py::InitBackendChoicesTests -v`
預期：`test_backends_tuple_derived_from_registry` FAIL（`AssertionError: ('claude-headless', 'openai-compatible', 'custom-argv') != (...7 keys...)`）；`test_parser_accepts_all_registry_presets` FAIL（`SystemExit`——codex-headless 不在 choices）；`test_init_rejects_unavailable_presets` FAIL（gemini/antigravity 走 `not in _BACKENDS` 路徑雖回 2，但 PR-A/PR-C 後行為以本 Task 改寫為準——若此測試意外 PASS 亦繼續，實作後全綠即可）。

- [ ] **Step 3: 改 `ops.py`**

（a）import 區——把

```python
from paulsha_hippo import paths
```

改為

```python
from paulsha_hippo import backends, paths
```

（b）把

```python
_BACKENDS = ("claude-headless", "openai-compatible", "custom-argv")
```

改為

```python
# 契約 7：_BACKENDS 改由 preset registry 導出（向後相容）。
_BACKENDS = tuple(backends.PRESETS)
```

（c）`run_init` 開頭——把

```python
    if backend not in _BACKENDS:
        print(f"init: 不支援的 backend: {backend}（可選 {', '.join(_BACKENDS)}）", file=sys.stderr)
        return 2
```

改為

```python
    preset = backends.PRESETS.get(backend)
    if preset is None:
        print(f"init: 不支援的 backend: {backend}（可選 {', '.join(_BACKENDS)}）", file=sys.stderr)
        return 2
    if not preset.available:
        print(f"init: backend {backend} 尚不可用（命令契約未確認，見 issue #10）", file=sys.stderr)
        return 2
```

- [ ] **Step 4: 改 `cli.py` init subparser**

把（`_build_parser` 內）

```python
    init_p = memory_subparsers.add_parser("init", help="初始化 config 與蒸餾 backend")
    init_p.add_argument("--memory-root")
    init_p.add_argument("--backend", default="claude-headless",
                        choices=["claude-headless", "openai-compatible", "custom-argv"])
```

改為

```python
    from paulsha_hippo import backends as hippo_backends

    init_p = memory_subparsers.add_parser("init", help="初始化 config 與蒸餾 backend")
    init_p.add_argument("--memory-root")
    _backend_help = "蒸餾 backend preset：" + "、".join(
        name + ("（尚不可用）" if not preset.available else "")
        for name, preset in hippo_backends.PRESETS.items()
    )
    init_p.add_argument("--backend", default="claude-headless",
                        choices=list(hippo_backends.PRESETS), help=_backend_help)
```

- [ ] **Step 5: 跑測試確認 PASS**

執行：`python3 -m pytest tests/test_ops.py -v`
預期：新增 5 tests 全 PASS；既有 tests 不回歸（若 PR-A 的 init 測試因 `_BACKENDS` 訊息文字斷言失敗，僅更新該斷言文字，不改行為）。

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/ops.py paulsha_hippo/cli.py tests/test_ops.py
git commit -m "feat(ops): _BACKENDS 改由 registry 導出；init --backend 選單化——gemini/antigravity 顯示但不可選"
```

---

### Task 3: `run_init` registry 驅動 override 寫入（argv[0] 絕對路徑化，fail-closed）

**Files:**
- Modify: `paulsha_hippo/ops.py:33-89`（`run_init` 全函式）＋ import 區補 `import json`
- Test: `tests/test_ops.py`（改寫 `InitTests.test_init_claude_headless_writes_config_and_override`、新增 3 tests）

**Interfaces:**
- Consumes: `ops.resolve_backend_argv(argv: list[str]) -> list[str]`、`ops.BackendUnavailableError`（PR-A 契約 2，同模組內直呼）；`backends.PRESETS[name].argv_template`（Task 1）
- Produces: `hippo init --backend <argv-preset>` 寫出 `atomizer.override.yaml`：`agent_exec.command` = preset argv template 且 `command[0]` 為絕對路徑（YAML 值以 JSON 字串引號輸出）；解析失敗 rc 2 且**不落任何檔案**（config.yaml 也不寫）

- [ ] **Step 1: 寫失敗測試**

`tests/test_ops.py`——改寫既有 `test_init_claude_headless_writes_config_and_override`（不論 PR-A 是否已改過此測試，一律以下版為準），並在 `InitTests` 內新增 3 個測試：

```python
    def test_init_claude_headless_writes_config_and_override(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops, "resolve_backend_argv",
                                   side_effect=lambda argv: ["/fake/abs/claude"] + list(argv[1:])):
                rc = ops.run_init(
                    memory_root=None, backend="claude-headless",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 0)
            cfg = Path(tmp) / "hippo-cfg" / "config.yaml"
            self.assertIn("backend: claude-headless", cfg.read_text(encoding="utf-8"))
            import yaml
            override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
            data = yaml.safe_load(override.read_text(encoding="utf-8"))
            self.assertEqual(str(data["schema_version"]), "1")
            self.assertEqual(data["agent_exec"]["command"], ["/fake/abs/claude", "-p"])

    def test_init_each_argv_preset_writes_registry_template(self):
        from paulsha_hippo import backends
        for name in ("claude-headless", "codex-headless", "copilot-headless"):
            with self.subTest(backend=name), TemporaryDirectory() as tmp:
                env = {
                    "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                    "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                    "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
                }
                template = backends.PRESETS[name].argv_template
                with mock.patch.dict("os.environ", env), \
                     mock.patch.object(ops, "resolve_backend_argv",
                                       side_effect=lambda argv: ["/fake/abs/" + argv[0]] + list(argv[1:])):
                    rc = ops.run_init(memory_root=None, backend=name, base_url=None,
                                      api_key_env=None, model=None, assume_yes=True)
                self.assertEqual(rc, 0)
                import yaml
                data = yaml.safe_load(
                    (Path(tmp) / "legacy" / ".config" / "paulshaclaw"
                     / "atomizer.override.yaml").read_text(encoding="utf-8"))
                self.assertEqual(
                    data["agent_exec"]["command"],
                    ["/fake/abs/" + template[0]] + list(template[1:]))

    def test_init_argv_preset_includes_model_when_given(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops, "resolve_backend_argv",
                                   side_effect=lambda argv: ["/fake/abs/codex"] + list(argv[1:])):
                rc = ops.run_init(memory_root=None, backend="codex-headless",
                                  base_url=None, api_key_env=None,
                                  model="gpt-5.4", assume_yes=True)
            self.assertEqual(rc, 0)
            import yaml
            data = yaml.safe_load(
                (Path(tmp) / "legacy" / ".config" / "paulshaclaw"
                 / "atomizer.override.yaml").read_text(encoding="utf-8"))
            self.assertEqual(data["agent_exec"]["model"], "gpt-5.4")

    def test_init_argv_preset_backend_unavailable_fails_closed(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops, "resolve_backend_argv",
                                   side_effect=ops.BackendUnavailableError("codex not found")):
                rc = ops.run_init(memory_root=None, backend="codex-headless",
                                  base_url=None, api_key_env=None, model=None, assume_yes=True)
            self.assertEqual(rc, 2)
            self.assertFalse((Path(tmp) / "hippo-cfg" / "config.yaml").exists())
            self.assertFalse((Path(tmp) / "legacy" / ".config" / "paulshaclaw"
                              / "atomizer.override.yaml").exists())
```

- [ ] **Step 2: 跑測試確認 FAIL**

執行：`python3 -m pytest tests/test_ops.py::InitTests -v`
預期：`test_init_each_argv_preset_writes_registry_template` FAIL（codex-headless 走 custom-argv else 分支，無 override 檔——`FileNotFoundError`）；`test_init_argv_preset_backend_unavailable_fails_closed` FAIL（config.yaml 已被寫出）。

- [ ] **Step 3: 改寫 `run_init`**

（a）`ops.py` import 區補一行（放在 `import os` 後）：

```python
import json
```

（b）`run_init` 整個函式改為（Task 2 的驗證頭保留在最前）：

```python
def run_init(*, memory_root: str | None, backend: str, base_url: str | None,
             api_key_env: str | None, model: str | None, assume_yes: bool) -> int:
    """產生 ~/.config/paulsha-hippo/config.yaml 與 atomizer override（backend preset）。

    preset 來自 backends.PRESETS（契約 7）；argv preset 寫入前經
    resolve_backend_argv（PR-A 契約 2）把 argv[0] 絕對路徑化。
    先解析、後寫檔：任何 fail-closed（rc 2）都不得留下半套 config。
    """
    preset = backends.PRESETS.get(backend)
    if preset is None:
        print(f"init: 不支援的 backend: {backend}（可選 {', '.join(_BACKENDS)}）", file=sys.stderr)
        return 2
    if not preset.available:
        print(f"init: backend {backend} 尚不可用（命令契約未確認，見 issue #10）", file=sys.stderr)
        return 2

    override_body: str | None
    if backend == "openai-compatible":
        if not base_url:
            print("init: openai-compatible 需要 --base-url", file=sys.stderr)
            return 2
        override_body = (
            "schema_version: \"1\"\n"
            "agent_exec:\n"
            "  backend: openai-compatible\n"
            f"  base_url: {base_url}\n"
            + (f"  api_key_env: {api_key_env}\n" if api_key_env else "")
            + (f"  model: {model}\n" if model else "")
        )
    elif preset.argv_template:
        try:
            argv = resolve_backend_argv(list(preset.argv_template))
        except BackendUnavailableError as exc:
            print(f"init: backend 不可用：{exc}", file=sys.stderr)
            return 2
        command_lines = "".join(
            f"    - {json.dumps(item, ensure_ascii=False)}\n" for item in argv
        )
        override_body = (
            "schema_version: \"1\"\n"
            "agent_exec:\n"
            "  command:\n"
            f"{command_lines}"
            + (f"  model: {model}\n" if model else "")
        )
    else:  # custom-argv：不動 override（沿 atomizer.yaml 或既有 override）
        override_body = None

    root = memory_root or str(paths.memory_root())
    cfg_dir = paths.hippo_config_root()
    cfg = cfg_dir / "config.yaml"
    wrote_cfg = _write_if_absent(
        cfg,
        (
            f"memory_root: {root}\n"
            "distiller:\n"
            f"  backend: {backend}\n"
            + (f"  base_url: {base_url}\n" if base_url else "")
            + (f"  api_key_env: {api_key_env}\n" if api_key_env else "")
            + (f"  model: {model}\n" if model else "")
        ),
        force=assume_yes and not cfg.exists(),
    )

    override = paths.config_path("atomizer.override.yaml")
    wrote_override = False
    if override_body is not None:
        wrote_override = _write_if_absent(override, override_body)

    print(f"memory_root: {root}")
    print(f"distiller backend: {backend}")
    print(f"config: {cfg}{'（既存，未覆寫）' if not wrote_cfg and cfg.exists() else ''}")
    if override_body is not None:
        print(f"atomizer override: {override}{'（既存，未覆寫）' if not wrote_override else ''}")
    print("下一步：hippo install hooks && hippo install service --enable")
    return 0
```

注意：這個版本**取代**（泛化）PR-A 在 claude-headless 分支做的絕對路徑化——語義相同、範圍擴到全部 argv presets。YAML 值用 `json.dumps` 引號輸出（雙引號 scalar 為合法 YAML），路徑含空白也安全。

- [ ] **Step 4: 跑測試確認 PASS**

執行：`python3 -m pytest tests/test_ops.py -v`
預期：全 PASS。若 PR-A 既有 init 測試以「未加引號的 YAML 行」做 substring 斷言而失敗，把該斷言改為 `yaml.safe_load` 解析後比對 `agent_exec.command`（語義不變），不回退 quoting。

- [ ] **Step 5: 全套回歸**

執行：`python3 -m pytest tests/ -q`
預期：0 failed（skip 允許）。

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/ops.py tests/test_ops.py
git commit -m "feat(init): registry 驅動 override 寫入——argv[0] 經 resolve_backend_argv 絕對路徑化、fail-closed 不落半套 config"
```

---

### Task 4: `hippo doctor` per-preset probe 報告

**Files:**
- Modify: `paulsha_hippo/ops.py:94-121`（`run_doctor`）
- Test: `tests/test_ops.py`（`DoctorTests` 新增 1 test＋import 補充）

**Interfaces:**
- Consumes: `backends.probe_preset()`、`backends.PRESETS`、`backends.ProbeResult`（Task 1）
- Produces: doctor stdout 新段（只報告，不改 exit code 語義）：
  - 標頭行 `- backend presets（service-effective probe）:`
  - 每 preset 一行：`  - <name>: ✓ <abs-exe>（<detail>）` ／ `  - <name>: ✗ <detail>` ／ `  - <name>: - <detail>`（ok=None 宣告層）

- [ ] **Step 1: 寫失敗測試**

`tests/test_ops.py`——import 區補：

```python
import io
from contextlib import redirect_stdout
```

`DoctorTests` class 內新增：

```python
    def test_doctor_reports_backend_preset_matrix(self):
        from paulsha_hippo import backends

        def fake_probe(preset, *, env=None, timeout=30):
            if preset.name == "claude-headless":
                return backends.ProbeResult(preset.name, True, "/abs/claude", True,
                                            "2.1.206 (Claude Code)")
            if preset.name == "codex-headless":
                return backends.ProbeResult(preset.name, True, "/abs/codex", False,
                                            "probe rc=127：node not found")
            if not preset.available:
                return backends.ProbeResult(preset.name, False, None, None,
                                            "unavailable（命令契約未確認，選單不可選）")
            if preset.required_executable is not None:
                return backends.ProbeResult(preset.name, True, None, False,
                                            "executable 未安裝")
            return backends.ProbeResult(preset.name, True, None, None,
                                        "config 驅動（無本機執行檔需求）")

        env = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a"}
        buf = io.StringIO()
        # 比照本 class 既有測試的 _PROBE_OK 手法：patch 掉 PR-A 的
        # _probe_backend_service_effective，隔離 configured-backend probe 的環境依賴
        # （否則 CI 無 backend → probe FAIL → rc=1，下方 rc==0 斷言必紅）。
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(ops, "_probe_backend_service_effective",
                               return_value=("- distiller backend：✓ mocked", False)), \
             mock.patch.object(ops.backends, "probe_preset", side_effect=fake_probe), \
             redirect_stdout(buf):
            rc = ops.run_doctor()
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("- backend presets（service-effective probe）:", out)
        self.assertIn("  - claude-headless: ✓ /abs/claude（2.1.206 (Claude Code)）", out)
        self.assertIn("  - codex-headless: ✗ probe rc=127：node not found", out)
        self.assertIn("  - copilot-headless: ✗ executable 未安裝", out)
        self.assertIn("  - gemini-headless: ✗ unavailable（命令契約未確認，選單不可選）", out)
        self.assertIn("  - antigravity-headless: ✗ unavailable（命令契約未確認，選單不可選）", out)
        self.assertIn("  - openai-compatible: - config 驅動（無本機執行檔需求）", out)
```

- [ ] **Step 2: 跑測試確認 FAIL**

執行：`python3 -m pytest tests/test_ops.py::DoctorTests -v`
預期：`test_doctor_reports_backend_preset_matrix` FAIL（`AssertionError: '- backend presets（service-effective probe）:' not found in ...`）。
注意：測試內對 `_probe_backend_service_effective` 的 patch 是為隔離 PR-A configured-backend probe 的環境依賴——該 probe 真實呼叫 `atomizer_config.load_config()` 並在 service-effective PATH 上 `shutil.which` 解析 configured backend，CI（無 backend、fallback PATH）必 probe FAIL → `failed=True` → `rc=1`；patch 掉後 `rc==0` 斷言才只反映本 task 的功能缺口，FAIL 原因收斂為「preset 矩陣段落尚未存在」。

- [ ] **Step 3: 改 `run_doctor`**

把 `run_doctor` 內

```python
    agent = shutil.which("claude")
    print(f"- claude CLI：{'✓ ' + agent if agent else '未找到（claude-headless 檔位需要）'}")
    return 1 if failed else 0
```

改為

```python
    # backend preset 矩陣（spec §3.5.4）：service-effective 環境逐 preset probe。
    # 只報告、不影響 doctor exit code——configured backend 的 gate 檢查屬 PR-A。
    print("- backend presets（service-effective probe）:")
    for preset in backends.PRESETS.values():
        result = backends.probe_preset(preset)
        if not result.available:
            print(f"  - {result.preset}: ✗ {result.detail}")
        elif result.ok is None:
            print(f"  - {result.preset}: - {result.detail}")
        elif result.ok:
            print(f"  - {result.preset}: ✓ {result.executable}（{result.detail}）")
        else:
            print(f"  - {result.preset}: ✗ {result.detail}")
    return 1 if failed else 0
```

內容錨點注意：若 PR-A 已把「`agent = shutil.which("claude")` 兩行」改寫為 configured-backend probe 段，**保留 PR-A 該段**、把 preset 矩陣加在其後、`return` 前；若兩行仍在則由本段取代。

- [ ] **Step 4: 跑測試確認 PASS**

執行：`python3 -m pytest tests/test_ops.py::DoctorTests -v`
預期：3 tests 全 PASS（既有 2＋新 1）。

- [ ] **Step 5: 本機實跑一次留檔（驗收素材，非測試）**

執行：`python3 -m paulsha_hippo.cli doctor`
預期（2026-07-10 本機）：`claude-headless` ✓（原生執行檔，service PATH 外但 probe 可跑，detail 帶「不在 service PATH」註記）；`codex-headless` ✗（node-shebang 在 service PATH 無 node → probe rc!=0）或 ✓ 視主機 node 安裝而定；`copilot-headless` 視 `~/.local/bin` 是否在 service PATH；`gemini-headless`／`antigravity-headless` ✗ unavailable（宣告層短路，不執行 probe）。**輸出如實貼進 PR body 佐證「doctor 對每個 preset 給出正確可用性判定」。**

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/ops.py tests/test_ops.py
git commit -m "feat(doctor): backend preset 矩陣 probe——service-effective 環境逐 preset 判定（只報告不 gate）"
```

---

### Task 5: mock 情境矩陣 ×4（散文包 JSON／截斷／non-zero／timeout）

**Files:**
- Create: `tests/fixtures/atomizer/prose-agent.py`
- Create: `tests/fixtures/atomizer/truncated-agent.py`
- Create: `tests/fixtures/atomizer/failing-agent.py`
- Create: `tests/fixtures/atomizer/hanging-agent.py`
- Test: `tests/test_atomizer_backend_matrix.py`

**Interfaces:**
- Consumes: `cli.main(["atomize", ...])` 既有介面；契約 1 的 `parked` 事件欄位（`failure_category`／`attempts`／`cache_key`／`error`）；PR-A §3.1.3 `runtime/queue/_failed/` 證據目錄；`processing.state_of()`／`processing.fold_events()`
- Produces: 四種 backend 輸出情境的回歸防線（spec §3.5.5 的「後四種以 mock backend 注入」；「純 JSON」情境由既有 `tests/test_atomizer_e2e.py` fake-agent E2E 與 Task 7 真蒸餾 smoke 覆蓋）

設計說明：mock 走 `agent_exec.command` 注入（與 preset 同一 custom-argv 機制），情境與分類的對映——散文包 JSON → `llm_output.parse` 應能抽出陣列（**成功**路徑）；截斷 → `invalid_output`（快取淘汰＋重試，超限 `parked`）；non-zero／timeout → `transient`（單輪斷言留 `split`、快取不落地；不 loop 到 parked——避免耦合 PR-A 退避節奏）。「executable 不存在 → 立即 parked(backend_unavailable)」是 PR-A 自己的 E2E（§3.1 驗收），本批不重複。

- [ ] **Step 1: 建 4 個 mock backend fixtures**

`tests/fixtures/atomizer/prose-agent.py`：

```python
#!/usr/bin/env python3
"""散文包 JSON mock backend：模擬 codex 類 CLI 在 JSON 前後夾敘事文字。"""
from __future__ import annotations

import sys

sys.stdin.read()
print("好的，以下是本次蒸餾結果，總共一個 slice：")
print("```json")
print(
    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw",'
    '"tags":["t1"],"body":"alpha distilled","source_fragment_indices":[0],'
    '"relations":[{"type":"mentions","entity":"MTK"}]}]'
)
print("```")
print("以上輸出已完成，如需調整請告知。")
```

`tests/fixtures/atomizer/truncated-agent.py`：

```python
#!/usr/bin/env python3
"""截斷輸出 mock backend：模擬 max-token 截斷——JSON 陣列中途斷裂。"""
from __future__ import annotations

import sys

sys.stdin.read()
print('[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw",'
      '"tags":["t1"],"body":"alp', end="")
```

`tests/fixtures/atomizer/failing-agent.py`：

```python
#!/usr/bin/env python3
"""non-zero exit mock backend：模擬 CLI 認證失敗／配額耗盡類故障。"""
from __future__ import annotations

import sys

sys.stdin.read()
print("fatal: authentication required", file=sys.stderr)
sys.exit(3)
```

`tests/fixtures/atomizer/hanging-agent.py`：

```python
#!/usr/bin/env python3
"""timeout mock backend：讀完 stdin 後長眠，觸發 agent_exec timeout。"""
from __future__ import annotations

import sys
import time

sys.stdin.read()
time.sleep(30)
```

- [ ] **Step 2: 寫失敗測試**

建立 `tests/test_atomizer_backend_matrix.py`，完整內容：

```python
"""PR-D spec §3.5.5 mock 情境矩陣：散文包 JSON／截斷／non-zero exit／timeout。

四種輸出情境經 custom-argv 機制（與 preset 同構）注入 atomize E2E：
- 散文包 JSON → llm_output.parse 抽出 JSON 陣列，session promoted
- 截斷輸出   → invalid_output：毒快取即時淘汰＋重試，超限 parked（契約 1）
- non-zero    → transient：本輪留 split、快取不落地
- timeout     → transient：本輪留 split、快取不落地
「純 JSON」happy path 由 tests/test_atomizer_e2e.py（fake-agent）與
tests/test_atomizer_llm_live.py（真蒸餾 smoke）覆蓋。
"""
from __future__ import annotations

import io
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli as memory_cli
from paulsha_hippo.ledger import processing

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "atomizer"
RAW_FIXTURE = FIXTURES / "raw" / "s1.md"
SESSION_KEY = "claude:sess-e2e"


def _seed(root: Path) -> None:
    raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RAW_FIXTURE, raw)


def _write_override(root: Path, agent_script: str, *, timeout_seconds: int = 30) -> Path:
    projects = root / "projects.yaml"
    projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
    override = root / "atomizer.override.yaml"
    override.write_text(
        "\n".join(
            (
                f'known_projects_file: "{projects}"',
                "agent_exec:",
                "  command:",
                f"    - {sys.executable}",
                f"    - {FIXTURES / agent_script}",
                f"  timeout_seconds: {timeout_seconds}",
                "  model: mock-backend",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return override


def _atomize(root: Path, override: Path, now: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = memory_cli.main([
            "atomize", "--memory-root", str(root), "--now", now,
            "--promoter", "llm", "--override", str(override),
        ])
    return rc, buf.getvalue()


def _cache_json_files(root: Path) -> list[Path]:
    cache = root / "runtime" / "cache" / "atomize"
    return sorted(cache.glob("*.json")) if cache.exists() else []


class ProseWrappedJsonTests(unittest.TestCase):
    def test_prose_wrapped_json_promotes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            override = _write_override(root, "prose-agent.py")
            rc, out = _atomize(root, override, "2026-07-10T00:00:00Z")
            self.assertEqual(rc, 0)
            self.assertEqual(processing.state_of(root, SESSION_KEY), "promoted")
            slices = sorted((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertGreaterEqual(len(slices), 1)


class TruncatedOutputTests(unittest.TestCase):
    def test_truncated_output_evicts_cache_and_parks_after_budget(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            override = _write_override(root, "truncated-agent.py")

            rc, out = _atomize(root, override, "2026-07-10T00:00:00Z")
            self.assertEqual(rc, 0)
            state = processing.state_of(root, SESSION_KEY)
            self.assertIn(state, {"split", "parked"})
            self.assertIn("llm promote failed", out)
            # 毒快取即時淘汰（spec §3.1.1 invalid output：先淘汰快取再重試）
            self.assertEqual(_cache_json_files(root), [])

            for round_no in range(1, 9):
                if state == "parked":
                    break
                rc, out = _atomize(root, override, f"2026-07-10T{round_no:02d}:00:00Z")
                self.assertEqual(rc, 0)
                state = processing.state_of(root, SESSION_KEY)
            self.assertEqual(state, "parked")

            event = processing.fold_events(root)[SESSION_KEY]
            self.assertEqual(event["failure_category"], "invalid_output")
            self.assertGreaterEqual(int(event["attempts"]), 1)
            self.assertTrue(event.get("cache_key"))
            self.assertTrue(event.get("error"))
            self.assertLessEqual(len(str(event["error"])), 500)
            # 超限即淘汰（spec §3.1.9 測試反轉）＋證據落 _failed/
            self.assertEqual(_cache_json_files(root), [])
            failed_dir = root / "runtime" / "queue" / "_failed"
            self.assertTrue(failed_dir.is_dir() and any(failed_dir.iterdir()))
            # split fragments 保留供 requeue（spec §3.1.8）
            self.assertTrue(list((root / "inbox" / "_slices").rglob("*.md")))

            # parked 不再吃 atomize 預算（spec §3.1.2）
            attempts_before = int(event["attempts"])
            rc, _ = _atomize(root, override, "2026-07-10T12:00:00Z")
            self.assertEqual(rc, 0)
            event_after = processing.fold_events(root)[SESSION_KEY]
            self.assertEqual(event_after["state"], "parked")
            self.assertEqual(int(event_after.get("attempts", attempts_before)),
                             attempts_before)


class NonZeroExitTests(unittest.TestCase):
    def test_nonzero_exit_is_transient_no_cache_written(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            override = _write_override(root, "failing-agent.py")
            rc, out = _atomize(root, override, "2026-07-10T00:00:00Z")
            self.assertEqual(rc, 0)
            self.assertEqual(processing.state_of(root, SESSION_KEY), "split")
            self.assertIn("exited with code 3", out)
            self.assertEqual(_cache_json_files(root), [])


class TimeoutTests(unittest.TestCase):
    def test_timeout_is_transient_no_cache_written(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            override = _write_override(root, "hanging-agent.py", timeout_seconds=1)
            rc, out = _atomize(root, override, "2026-07-10T00:00:00Z")
            self.assertEqual(rc, 0)
            self.assertEqual(processing.state_of(root, SESSION_KEY), "split")
            self.assertIn("timed out after 1s", out)
            self.assertEqual(_cache_json_files(root), [])


if __name__ == "__main__":
    unittest.main()
```

斷言取材規則：CLI stdout 是 `json.dumps(..., ensure_ascii=True)`，中文會被 `\uXXXX` 轉義——**只對 ASCII 片段做 substring 斷言**（`llm promote failed`／`exited with code 3`／`timed out after 1s`，皆來自 `agent_exec.py`/`llm_promoter.py` 既有訊息）。

- [ ] **Step 3: 跑測試確認結果**

執行：`python3 -m pytest tests/test_atomizer_backend_matrix.py -v`
預期（PR-A 已 merge 的 main 上）：`ProseWrappedJson`／`NonZeroExit`／`Timeout` 直接 PASS（機制既有）；`TruncatedOutput` PASS 依賴 PR-A 的 parked 鏈——**若 FAIL，先跑 `python3 -m pytest tests/test_ledger_processing.py -v` 確認 PR-A 契約 1 已在 main**；契約在而測試 FAIL 才是本批 bug（多半是斷言與 PR-A 實作的容差問題，修測試容差、不改 PR-A 行為）。此 Task 為純測試新增，無產品碼變更。

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/atomizer/prose-agent.py tests/fixtures/atomizer/truncated-agent.py \
        tests/fixtures/atomizer/failing-agent.py tests/fixtures/atomizer/hanging-agent.py \
        tests/test_atomizer_backend_matrix.py
git commit -m "test(atomizer): mock 情境矩陣——散文包 JSON/截斷/non-zero/timeout 對應 promoted/parked(invalid_output)/transient"
```

---

### Task 6: `dream supervise --once` ＋ 無 systemd E2E

**Files:**
- Modify: `paulsha_hippo/cli.py:125-130`（supervise subparser）、`paulsha_hippo/cli.py:794-798`（`_dream_supervise`）
- Test: `tests/test_ops.py`（新增 wiring test）、Create: `tests/test_dream_supervise_e2e.py`

**Interfaces:**
- Consumes: `ops.run_dream_supervise(*, interval, extra_argv=None, once=False, runner=None)`（既有，`once` 參數已存在）；`dream run` argparse 對重複旗標 last-wins（`--promoter`/`--max-load` 覆蓋 supervise 內建的 `--require-idle --promoter llm`）
- Produces: CLI `hippo dream supervise --interval N --once [--memory-root P] [--max-load F] [--promoter identity|llm] [--agent-command S]`——`--once` 單輪即返；其餘旗標透傳為 dream run extra argv

- [ ] **Step 1: 寫失敗測試（CLI wiring）**

`tests/test_ops.py` 檔尾新增：

```python
class SuperviseCliWiringTests(unittest.TestCase):
    def test_supervise_cli_forwards_once_and_overrides(self):
        from paulsha_hippo import cli as memory_cli
        captured: dict = {}

        def fake_supervise(*, interval, extra_argv=None, once=False, runner=None):
            captured.update(interval=interval, extra_argv=list(extra_argv or []), once=once)
            return 0

        with mock.patch.object(ops, "run_dream_supervise", side_effect=fake_supervise):
            rc = memory_cli.main([
                "dream", "supervise", "--interval", "5", "--once",
                "--memory-root", "/mr", "--max-load", "99.5",
                "--promoter", "identity", "--agent-command", "python x.py",
            ])
        self.assertEqual(rc, 0)
        self.assertTrue(captured["once"])
        self.assertEqual(captured["interval"], 5)
        self.assertEqual(captured["extra_argv"], [
            "--memory-root", "/mr", "--max-load", "99.5",
            "--promoter", "identity", "--agent-command", "python x.py"])
```

- [ ] **Step 2: 跑測試確認 FAIL**

執行：`python3 -m pytest tests/test_ops.py::SuperviseCliWiringTests -v`
預期：FAIL——`SystemExit: 2`（argparse：`unrecognized arguments: --once ...`），`cli.main` 捕捉後 rc=2 ≠ 0。

- [ ] **Step 3: 改 `cli.py`**

（a）supervise subparser——把

```python
    dream_supervise = dream_subparsers.add_parser(
        "supervise", help="前景常駐：每 interval 秒 dream run --require-idle（非 systemd 主機用）"
    )
    dream_supervise.add_argument("--interval", type=int, default=3600)
    dream_supervise.add_argument("--memory-root")
    dream_supervise.set_defaults(func=_dream_supervise)
```

改為

```python
    dream_supervise = dream_subparsers.add_parser(
        "supervise", help="前景常駐：每 interval 秒 dream run --require-idle（非 systemd 主機用）"
    )
    dream_supervise.add_argument("--interval", type=int, default=3600)
    dream_supervise.add_argument("--memory-root")
    dream_supervise.add_argument("--once", action="store_true",
                                 help="只跑一輪就結束（無 systemd 主機的單輪驗收，#10）")
    dream_supervise.add_argument("--max-load", type=float, default=None,
                                 help="透傳 dream run --max-load（覆蓋內建 1.0）")
    dream_supervise.add_argument("--promoter", choices=["identity", "llm"], default=None,
                                 help="透傳 dream run --promoter（覆蓋內建 llm）")
    dream_supervise.add_argument("--agent-command", default=None,
                                 help="透傳 dream run --agent-command")
    dream_supervise.set_defaults(func=_dream_supervise)
```

（b）`_dream_supervise`——把

```python
def _dream_supervise(args) -> int:
    from paulsha_hippo import ops

    extra = ["--memory-root", args.memory_root] if args.memory_root else []
    return ops.run_dream_supervise(interval=args.interval, extra_argv=extra)
```

改為

```python
def _dream_supervise(args) -> int:
    from paulsha_hippo import ops

    extra = ["--memory-root", args.memory_root] if args.memory_root else []
    if args.max_load is not None:
        extra += ["--max-load", str(args.max_load)]
    if args.promoter:
        extra += ["--promoter", args.promoter]
    if args.agent_command:
        extra += ["--agent-command", args.agent_command]
    # dream run 的 argparse 對重複旗標 last-wins：extra 的 --promoter/--max-load
    # 覆蓋 run_dream_supervise 內建的 --require-idle --promoter llm 基底。
    return ops.run_dream_supervise(interval=args.interval, extra_argv=extra, once=args.once)
```

- [ ] **Step 4: 跑 wiring 測試確認 PASS**

執行：`python3 -m pytest tests/test_ops.py::SuperviseCliWiringTests -v`
預期：PASS。

- [ ] **Step 5: 寫 E2E 測試（先跑確認可過）**

建立 `tests/test_dream_supervise_e2e.py`，完整內容：

```python
"""#10 原始 checklist：無 systemd 主機 dream supervise 前台實測一輪（spec §3.5.7）。

E2E 路徑：cli.main dream supervise --once → 等一個 interval → dream run
（--require-idle，--max-load 放大避免忙碌機器假 skip）→ atomize（llm promoter
＋fake-agent）→ janitor → moc。斷言蒸餾產物與 dream ledger。
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import cli
from paulsha_hippo.ledger import dream as dream_ledger
from paulsha_hippo.ledger import processing

_REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "raw" / "s1.md"
FAKE_AGENT = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "fake-agent.py"


class DreamSuperviseE2ETests(unittest.TestCase):
    def test_supervise_once_runs_full_dream_round_without_systemd(self):
        with TemporaryDirectory(dir=_REPO_ROOT) as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-07-10" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)

            # 隔離 HOME＋清掉 PSC_/HIPPO_ env：dream run 用預設 override 掛點
            # （<HOME>/.config/paulshaclaw/atomizer.override.yaml），known_projects
            # 指到 tmp 的 projects.yaml。
            home = root / "home"
            override_dir = home / ".config" / "paulshaclaw"
            override_dir.mkdir(parents=True, exist_ok=True)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            (override_dir / "atomizer.override.yaml").write_text(
                f'known_projects_file: "{projects}"\n', encoding="utf-8")

            clean_env = {k: v for k, v in os.environ.items()
                         if not k.startswith(("PSC_", "HIPPO_"))}
            clean_env["HOME"] = str(home)
            with mock.patch.dict(os.environ, clean_env, clear=True):
                rc = cli.main([
                    "dream", "supervise", "--interval", "1", "--once",
                    "--memory-root", str(root),
                    "--max-load", "1000000",
                    "--agent-command", f"{sys.executable} {FAKE_AGENT}",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(processing.state_of(root, "claude:sess-e2e"), "promoted")
            slices = sorted((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertGreaterEqual(len(slices), 1)
            last = dream_ledger.last_run(root)
            self.assertIsNotNone(last)
            self.assertIn(last["status"], ("ok", "partial"))
            self.assertIn("atomize", last.get("passes", {}))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: 跑 E2E 確認 PASS**

執行：`python3 -m pytest tests/test_dream_supervise_e2e.py -v`
預期：PASS（含 1 秒 interval 睡眠，單測 <15s）。若 FAIL 且訊息含 dream lock，確認 PR-A 契約 3 的 lock 路徑在 tmp root 下（`<root>/runtime/locks/dream.lock`）——跨測試不共享，理論上不衝突。

- [ ] **Step 7: 本機前台實測一輪（#10 checklist 項，驗收素材）**

執行（真 memory root、identity promoter 避免動真 LLM；只驗 supervise 迴圈與 dream 鏈）：

```bash
python3 -m paulsha_hippo.cli dream supervise --interval 5 --once \
  --memory-root ~/.agents/memory --promoter identity --max-load 1000000
```

預期：等 5 秒後跑一輪 dream run、印 JSON 結果（`passes` 含 atomize/janitor/moc）、process 結束 rc=0。輸出貼進 PR body。

- [ ] **Step 8: Commit**

```bash
git add paulsha_hippo/cli.py tests/test_ops.py tests/test_dream_supervise_e2e.py
git commit -m "feat(supervise): --once 單輪模式＋透傳旗標——無 systemd 主機 E2E 驗收路徑（#10 checklist）"
```

---

### Task 7: 真蒸餾 smoke ×3 available preset（env gate＋skip 回報制）＋矩陣覆蓋 guard

**Files:**
- Modify: `tests/test_atomizer_llm_live.py:1-52`（保留既有 gemma4 live 測試，新增矩陣 class＋覆蓋 guard）

**Interfaces:**
- Consumes: `backends.PRESETS`／`backends.probe_preset()`（Task 1）；`AgentExecClient`／`AgentExecError`（既有）；`run_static_gate_check_file`（既有）
- Produces: env gate `PSC_ATOMIZE_LIVE=1` 下的 3 個 live 測試（claude/codex/copilot）＋無 gate 常跑的矩陣覆蓋 guard（available 的 argv preset 必在 smoke 矩陣——翻 available 必補 smoke）；每 preset 證據行（stderr JSON：`{"smoke":"atomize-live","backend":...,"argv":[...],"slices":N}`）；未安裝或 probe 失敗 → `skipTest` 帶原因（風險表：不擋批次、列入 #10 缺項）；unavailable preset（gemini/antigravity）不在矩陣（固定缺項，非 runtime skip）

- [ ] **Step 1: 改寫測試檔**

`tests/test_atomizer_llm_live.py` 全檔改為（既有 `AtomizerLlmLiveTests` class 原樣保留在檔尾）：

```python
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import backends, cli
from paulsha_hippo.atomizer.agent_exec import AgentExecClient, AgentExecError
from paulsha_hippo.lib.lifecycle.gate import run_static_gate_check_file

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "raw" / "s1.md"

_LIVE_TIMEOUT_SECONDS = 300

_MATRIX_PRESETS = ("claude-headless", "codex-headless", "copilot-headless")


class SmokeMatrixCoverageTests(unittest.TestCase):
    """升級防護（docs/backend-matrix.md）：available 的 argv preset 必在 smoke 矩陣。

    unavailable preset（如 gemini-headless）翻 available=True 時本測試 FAIL，
    強制同 PR 補對應 live smoke——「round-trip 實證→翻 available→補 smoke」
    的機械 gate。無 env gate，一般 CI 常跑。
    """

    def test_matrix_covers_every_available_argv_preset(self):
        available_argv = tuple(
            name for name, preset in backends.PRESETS.items()
            if preset.available
            and "argv-stdin" in preset.capabilities
            and "user-defined" not in preset.capabilities
        )
        self.assertEqual(available_argv, _MATRIX_PRESETS)


@unittest.skipUnless(
    os.environ.get("PSC_ATOMIZE_LIVE"),
    "set PSC_ATOMIZE_LIVE=1 to enable real-backend distillation smokes",
)
class AtomizerLlmLiveMatrixTests(unittest.TestCase):
    """spec §3.5.5 真蒸餾 smoke：同一 fixture session × 每個 available argv preset。

    happy path（純 JSON 情境）一輪；其餘四情境見 mock 矩陣
    （tests/test_atomizer_backend_matrix.py）。未安裝或 probe 失敗（auth／
    配額／PATH 故障）→ skip 並回報原因——不擋批次，但列入 #10 缺項
    （spec §3.5 關單條件、§8 風險表）。registry 標 unavailable 的 preset
    （gemini-headless／antigravity-headless）不在本矩陣——固定缺項而非
    runtime skip；升級前提見 docs/backend-matrix.md。
    """

    def _smoke(self, preset_name: str) -> None:
        preset = backends.PRESETS[preset_name]
        # 第一層：executable/version probe（互動環境；快、免 LLM 配額）
        probe = backends.probe_preset(preset, env=dict(os.environ), timeout=60)
        if probe.ok is not True:
            self.skipTest(f"{preset_name} 本機不可用（version probe）：{probe.detail}")
        argv = [probe.executable] + list(preset.argv_template[1:])
        # 第二層：launch probe——一次極小 prompt 真喚起，auth/配額/PATH 故障
        # 在此轉 skip（誠實回報），其後蒸餾失敗才算真 finding。
        try:
            AgentExecClient(argv, timeout=120).run('請只輸出 ["ok"]，不要其他文字')
        except AgentExecError as exc:
            self.skipTest(f"{preset_name} 本機不可用（launch probe）：{exc}")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            override = root / "atomizer.override.yaml"
            command_lines = "".join(
                f"    - {json.dumps(item, ensure_ascii=False)}\n" for item in argv)
            override.write_text(
                (
                    f'known_projects_file: "{projects}"\n'
                    "agent_exec:\n"
                    "  command:\n"
                    f"{command_lines}"
                    f"  timeout_seconds: {_LIVE_TIMEOUT_SECONDS}\n"
                    f"  model: {preset_name}\n"
                ),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "atomize", "--memory-root", str(root),
                    "--now", "2026-07-10T03:00:00Z",
                    "--promoter", "llm", "--override", str(override),
                ])
            self.assertEqual(rc, 0, buf.getvalue())
            slice_paths = sorted((root / "knowledge").rglob("*.md"))
            self.assertGreaterEqual(len(slice_paths), 1, buf.getvalue())
            for slice_path in slice_paths:
                result = run_static_gate_check_file(slice_path)
                self.assertTrue(result.ok, result.errors)
            # 驗收證據（backend、輸出 slice 數）——workflow 由測試輸出擷取進 PR body
            print(json.dumps({
                "smoke": "atomize-live", "backend": preset_name,
                "argv": argv, "slices": len(slice_paths),
            }, ensure_ascii=False), file=sys.stderr)

    def test_live_claude_headless(self):
        self._smoke("claude-headless")

    def test_live_codex_headless(self):
        self._smoke("codex-headless")

    def test_live_copilot_headless(self):
        self._smoke("copilot-headless")


FIXTURE_LEGACY = FIXTURE


@unittest.skipUnless(
    os.environ.get("PSC_ATOMIZE_LIVE"),
    "set PSC_ATOMIZE_LIVE=1 to enable the real claude-gemma4 atomizer test",
)
class AtomizerLlmLiveTests(unittest.TestCase):
    def test_live_llm_atomize_produces_gate_valid_slice(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            override = root / "atomizer.override.yaml"
            override.write_text(f'known_projects_file: "{projects}"\n', encoding="utf-8")

            rc = cli.main(["atomize",
                    "--memory-root",
                    str(root),
                    "--now",
                    "2026-06-02T03:00:00Z",
                    "--promoter",
                    "llm",
                    "--override",
                    str(override),
                ]
            )

            self.assertEqual(rc, 0)
            slice_paths = sorted((root / "knowledge" / "paulshaclaw").rglob("*.md"))
            self.assertGreaterEqual(len(slice_paths), 1)
            for slice_path in slice_paths:
                result = run_static_gate_check_file(slice_path)
                self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
```

（注意：`run_static_gate_check_file` 原檔從 `paulsha_hippo.lib.lifecycle.gate` import——保持不變；舊 class 內容除 import 整併外逐字保留。）

- [ ] **Step 2: 無 gate 跑——live 測試 skip、覆蓋 guard 常跑（CI 安全）**

執行：`python3 -m pytest tests/test_atomizer_llm_live.py -v -ra`
預期：`SmokeMatrixCoverageTests` 1 PASSED（無 env gate，一般 CI 內建）；其餘 4 tests SKIPPED（3 matrix live＋1 legacy；reason: `set PSC_ATOMIZE_LIVE=1 ...`）。

- [ ] **Step 3: 帶 gate 真跑 ×3 available preset（驗收證據，約 5–15 分鐘）**

執行：`PSC_ATOMIZE_LIVE=1 python3 -m pytest "tests/test_atomizer_llm_live.py::AtomizerLlmLiveMatrixTests" -v -s -ra`
預期（2026-07-10 本機基線）：

```
test_live_claude_headless PASSED    ← stderr 證據行 {"smoke":"atomize-live","backend":"claude-headless",...,"slices":N}
test_live_codex_headless PASSED
test_live_copilot_headless PASSED
```

skip/fail 以實際輸出為準——**不得竄改測試遷就結果**。`gemini-headless` 不在矩陣（registry 標 unavailable，固定缺項）：若實作期間 auth 已備，不得只加測掩蓋——必須依 docs/backend-matrix.md 升級前提走完整三步（round-trip 實證→argv_template 定案＋翻 available→補 live smoke，coverage guard 機械強制），並同步更新實測記錄。把整段輸出（含證據行與 skip 原因）存進 PR body。若某 preset 因暫時性配額失敗，重跑該單測一次（風險表：smoke 標記可重試）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_atomizer_llm_live.py
git commit -m "test(atomizer): 真蒸餾 smoke ×3 available preset＋矩陣覆蓋 guard——PSC_ATOMIZE_LIVE gate、雙層 probe、probe 失敗轉 skip 回報"
```

---

### Task 8: openai-compatible 真端點 integration smoke（env gate）

**Files:**
- Test: `tests/test_openai_smoke_integration.py`（Create）

**Interfaces:**
- Consumes: `agent_exec.HttpAgentClient` 經 `atomizer.override.yaml` 的 `agent_exec.backend: openai-compatible` 掛點（既有）；`run_static_gate_check_file`
- Produces: env gate 三變數——`HIPPO_SMOKE_OPENAI_BASE_URL`（必要）、`HIPPO_SMOKE_OPENAI_MODEL`（建議）、`HIPPO_SMOKE_OPENAI_API_KEY_ENV`（選配，指向存 key 的 env 名，config 永不放值）；未設 base url 時整檔 skip、不進一般 CI

- [ ] **Step 1: 寫測試**

建立 `tests/test_openai_smoke_integration.py`，完整內容：

```python
"""spec §3.5.6 openai-compatible 真端點 smoke（integration profile，env gate）。

未設 HIPPO_SMOKE_OPENAI_BASE_URL 時整檔 skip——不進一般 CI。
本機有 gemma4/vLLM/ollama 類端點時：

    HIPPO_SMOKE_OPENAI_BASE_URL="$PSC_CLAUDE_GEMMA4_UPSTREAM_URL" \
    HIPPO_SMOKE_OPENAI_MODEL=<served-model-name> \
    python3 -m pytest tests/test_openai_smoke_integration.py -v -s

需要 Bearer key 的端點另設 HIPPO_SMOKE_OPENAI_API_KEY_ENV=<存 key 的 env 名>。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli
from paulsha_hippo.lib.lifecycle.gate import run_static_gate_check_file

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atomizer" / "raw" / "s1.md"

BASE_URL = os.environ.get("HIPPO_SMOKE_OPENAI_BASE_URL", "")
MODEL = os.environ.get("HIPPO_SMOKE_OPENAI_MODEL", "")
API_KEY_ENV = os.environ.get("HIPPO_SMOKE_OPENAI_API_KEY_ENV", "")


@unittest.skipUnless(
    BASE_URL, "set HIPPO_SMOKE_OPENAI_BASE_URL to run the real-endpoint smoke")
class OpenAiCompatibleSmokeTests(unittest.TestCase):
    def test_real_endpoint_distills_fixture_session(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-05-31" / "s1.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE, raw)
            projects = root / "projects.yaml"
            projects.write_text("projects:\n  - paulshaclaw\n", encoding="utf-8")
            override = root / "atomizer.override.yaml"
            override.write_text(
                (
                    f'known_projects_file: "{projects}"\n'
                    "agent_exec:\n"
                    "  backend: openai-compatible\n"
                    f"  base_url: {BASE_URL}\n"
                    + (f"  api_key_env: {API_KEY_ENV}\n" if API_KEY_ENV else "")
                    + (f"  model: {MODEL}\n" if MODEL else "")
                    + "  timeout_seconds: 300\n"
                ),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "atomize", "--memory-root", str(root),
                    "--now", "2026-07-10T03:00:00Z",
                    "--promoter", "llm", "--override", str(override),
                ])
            self.assertEqual(rc, 0, buf.getvalue())
            slice_paths = sorted((root / "knowledge").rglob("*.md"))
            self.assertGreaterEqual(len(slice_paths), 1, buf.getvalue())
            for slice_path in slice_paths:
                result = run_static_gate_check_file(slice_path)
                self.assertTrue(result.ok, result.errors)
            print(json.dumps({
                "smoke": "openai-compatible", "base_url_set": True,
                "model": MODEL or "(config default)", "slices": len(slice_paths),
            }, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
```

（R-21 注意：測試與文件僅引用 env 變數名，不落任何真實端點 URL/IP。）

- [ ] **Step 2: 無 gate 跑——確認 skip**

執行：`python3 -m pytest tests/test_openai_smoke_integration.py -v -ra`
預期：1 SKIPPED（reason: `set HIPPO_SMOKE_OPENAI_BASE_URL ...`）。

- [ ] **Step 3: 有端點則真跑一輪（驗收證據）**

執行：

```bash
HIPPO_SMOKE_OPENAI_BASE_URL="$PSC_CLAUDE_GEMMA4_UPSTREAM_URL" \
HIPPO_SMOKE_OPENAI_MODEL=<served-model-name> \
python3 -m pytest tests/test_openai_smoke_integration.py -v -s
```

預期：端點在線 → PASSED＋stderr 證據行；`$PSC_CLAUDE_GEMMA4_UPSTREAM_URL` 未設或端點離線 → 如實記錄「openai-compatible 真端點證據缺」，補列進 Task 9 的缺項清單（本 PR 因 gemini 固定缺項本就走 `Refs #10`）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_openai_smoke_integration.py
git commit -m "test(atomizer): openai-compatible 真端點 integration smoke——HIPPO_SMOKE_OPENAI_* env gate，不進一般 CI"
```

---

### Task 9: docs 同步 ＋ changelog.d 碎片 ＋ CHANGELOG `[Unreleased]` ＋ 全套驗證 ＋ #10 收斂 PR 說明（`Refs`＋固定缺項拆新 issue）

**Files:**
- Create: `docs/backend-matrix.md`
- Modify: `README.md`（三個錨行：引言 blockquote／「常駐」bullet／「蒸餾 LLM 三檔位」行——:8／:22／:30 為 2026-07-10 main 快照行號，一律以行內容定位）
- Modify: `CHANGELOG.md`（`[Unreleased]` 段——R-09 以此檔為準，policy_check 的 `_unreleased_has_bullet_entry` 只檢查 `## [Unreleased]` 下有 bullet，與 changelog.d 完全無關）
- Create: `changelog.d/feature-10-backend-matrix.md`

**Interfaces:**
- Consumes: Task 1–8 全部產出；`python3 -m policy_check --repo .`（R-02/R-04/R-09/R-18/R-21/R-22 gate）
- Produces: #10 收斂判定材料（`Refs #10`＋固定缺項→新 issue 規則落在 PR body）；R-18 docs 同步完成；R-09 CHANGELOG `[Unreleased]` entry＋changelog.d 碎片（兩者並存——`[Unreleased]` 供 R-09 gate，碎片供 release 彙整）

- [ ] **Step 1: 建 `docs/backend-matrix.md`**

完整內容：

```markdown
# Backend preset 矩陣（#10）

> registry 真源：`paulsha_hippo/backends.py`（`PRESETS`，契約見
> `docs/superpowers/specs/2026-07-10-all-issues-resolution-design.md` §3.5）。
> 本文件記錄各 preset 的 argv 契約、doctor probe、前置條件與實測狀態
> （基線 2026-07-10）。機制：argv presets 全走 custom-argv 機制——prompt 由
> stdin 餵入、stdout 取回輸出（`AgentExecClient`）；HTTP 檔位走
> `HttpAgentClient`。機制零新增。

| preset | argv template | doctor probe | 前置條件 | 實測狀態（2026-07-10） |
|---|---|---|---|---|
| `claude-headless` | `claude -p` | `claude --version` | Claude Code 已登入 | ✓ v0.1.0 既有已驗檔位（原生執行檔，無 node PATH 問題） |
| `codex-headless` | `codex exec --skip-git-repo-check --sandbox read-only --color never -` | `codex --version` | Codex CLI 已登入 | ✓ stdin→stdout round-trip：stdout 僅含 final message，log 全走 stderr |
| `copilot-headless` | `copilot -s --no-color` | `copilot --version` | Copilot CLI 已登入 | ✓ stdin 為唯一 prompt 來源。⚠ 帶非空 `-p` 時 stdin 注入不可靠（實測內容丟失、agent 徘徊），preset 刻意不用 `-p` |
| `gemini-headless` | —（unavailable；候選未驗證：`gemini -p "執行 stdin 提供的任務指示"`，僅由 `--help` 推得、不入 registry template） | —（unavailable 宣告層短路） | 升級前提見下節 | ✗ unavailable：無成功 stdin→stdout round-trip 實證——2026-07-10 實測 `--version` rc=0、headless 呼叫 rc=41（selectedType=vertex-ai 無 `GOOGLE_CLOUD_PROJECT`/`GOOGLE_API_KEY` env）；依 spec §8「接不上就標 unavailable + 回報，不猜 argv」。`init` 選單顯示但選了 rc 2；不在 smoke 矩陣 |
| `antigravity-headless` | —（未確認） | — | — | ✗ unavailable：命令契約未確認（spec §2 非目標）；`init --backend` 選單顯示但選了會 rc 2 |
| `openai-compatible` | —（HTTP） | —（integration smoke） | `base_url` 必填；key 一律走 `api_key_env`（config 不放值） | env-gate smoke：`HIPPO_SMOKE_OPENAI_BASE_URL`（見 `tests/test_openai_smoke_integration.py`） |
| `custom-argv` | 使用者自訂 | — | argv[0] 建議絕對路徑 | ✓ 既有機制（預設 gemma4 wrapper 沿用） |

## unavailable preset 升級前提（gemini-headless／antigravity-headless）

翻 `available=True` 的必要前提（缺一不可，同一 PR 完成）：

1. 真實認證備妥後，以候選 argv 完成**一次成功的 stdin→stdout round-trip**
   （rc=0、stdout 取回可解析回覆本文），並把實測記錄更新進上表。
2. registry（`paulsha_hippo/backends.py`）同步：`argv_template` 填入實測定案
   argv、`available=True`。
3. 同 PR 補對應 live smoke（`tests/test_atomizer_llm_live.py`）——
   `SmokeMatrixCoverageTests` 強制「available 的 argv preset 必在 smoke 矩陣」，
   漏補即 FAIL。

實測證據記錄（gemini-headless，2026-07-10）：`gemini --version` rc=0；headless
round-trip **失敗 rc=41**——本機認證 selectedType=vertex-ai 而無對應 env
（`GOOGLE_CLOUD_PROJECT`/`GOOGLE_API_KEY`）。候選 argv 僅由 `--help` 文字
（`-p`：「Appended to input on stdin (if any)」）推得，無任何成功實證——依
spec §8 風險表「不猜 argv」標 unavailable。antigravity-headless：執行檔不存在、
命令契約未確認（spec §2 非目標）。

## systemd service 環境注意

- `codex`／`gemini` 是 node script（`#!/usr/bin/env node`）：即使 config 寫了
  絕對路徑 argv[0]，systemd --user service 的 PATH 沒有 node 目錄時仍會啟動
  失敗。解法：service unit 加 `Environment=PATH=...`（含 node bin 目錄）或改用
  self-contained backend（`claude-headless`／`openai-compatible`）。
  `hippo doctor` 的 preset probe 以 service-effective PATH 執行，能直接暴露
  這類故障。
- 蒸餾子程序一律帶 `HIPPO_SELF_SESSION=1`（`agent_exec` 注入）——三家 CLI 的
  hippo hooks 讀到即跳過 queue write，不會遞迴自捕捉。

## smoke 執行方式

    # 三 available preset 真蒸餾（claude/codex/copilot；probe 失敗者 skip 並回報
    # 原因；unavailable preset 不在矩陣，見上節升級前提）
    PSC_ATOMIZE_LIVE=1 python3 -m pytest tests/test_atomizer_llm_live.py -v -s -ra

    # openai-compatible 真端點（integration profile）
    HIPPO_SMOKE_OPENAI_BASE_URL=<endpoint> HIPPO_SMOKE_OPENAI_MODEL=<model> \
    python3 -m pytest tests/test_openai_smoke_integration.py -v -s

    # mock 情境矩陣（散文包 JSON／截斷／non-zero／timeout；一般 CI 內建）
    python3 -m pytest tests/test_atomizer_backend_matrix.py -v
```

- [ ] **Step 2: 改 README**

（a）引言 blockquote（「已驗環境：WSL2＋systemd＋claude-headless。…」行；README.md:8 為 2026-07-10 main 快照行號，一律以行內容定位）——把

```markdown
> 已驗環境：WSL2＋systemd＋claude-headless。其他 backend（codex/copilot/openai-compatible headless）與無 systemd 主機為 opt-in，見 [#10](https://github.com/hamanpaul/paulsha-hippo/issues/10)。
```

改為

```markdown
> 已驗環境：WSL2＋systemd＋claude-headless。backend preset 矩陣（codex/copilot headless、openai-compatible；gemini/antigravity 尚不可用）見 `docs/backend-matrix.md` 與 `hippo doctor` 的 preset probe；無 systemd 主機用 `hippo dream supervise`（追蹤 [#10](https://github.com/hamanpaul/paulsha-hippo/issues/10)）。
```

（b）「常駐」bullet（「- 常駐：systemd user units 自動偵測…」行；README.md:22 為 main 快照行號，一律以行內容定位）——把

```markdown
- 常駐：systemd user units 自動偵測；不可用時 `hippo dream supervise` 前景模式
```

改為

```markdown
- 常駐：systemd user units 自動偵測；不可用時 `hippo dream supervise` 前景模式（`--once` 可單輪驗收）
```

（c）蒸餾檔位行（「蒸餾 LLM 三檔位：…」行；README.md:30 為 main 快照行號，一律以行內容定位）——把

```markdown
蒸餾 LLM 三檔位：`claude-headless`（預設，零 key 管理）／`openai-compatible`（ollama、vLLM、內網端點）／`custom-argv`。
```

改為

```markdown
蒸餾 backend presets：`claude-headless`（預設，零 key 管理）／`codex-headless`／`copilot-headless`／`openai-compatible`（ollama、vLLM、內網端點）／`custom-argv`；`gemini-headless`（無 round-trip 實證）與 `antigravity-headless`（命令契約未確認）尚不可用。矩陣、前置條件與升級前提見 `docs/backend-matrix.md`。
```

**合併規則（README 跨批次共用錨行；PR-A Task 12 Step 2／PR-B Task 6 Step 7／PR-C Task 7 Steps 1-2／PR-F Task 7 Step 3 帶同一條規則）**：若上列任一錨行已被 sibling 批次改寫（rebase 後與引用的 old 內容不符），不得整行覆蓋——以當前行內容為基底，僅追加／改寫本批負責的片段，保留 sibling 已 merge 的全部新增。本批片段逐行為：（a）僅把「其他 backend（codex/copilot/openai-compatible headless）與無 systemd 主機為 opt-in，見 #10」這段敘述改寫為上述 preset 矩陣＋`hippo dream supervise` 指引，行內 sibling 新增的其他敘述原樣保留；（b）僅在 `hippo dream supervise` 前景模式之後追加「（`--once` 可單輪驗收）」；（c）僅把三檔位清單擴充為 available preset 清單並追加 `gemini-headless`／`antigravity-headless` 尚不可用註記與「矩陣、前置條件與升級前提見 `docs/backend-matrix.md`」；sibling 已 merge 的命令與後續補充行一律原樣保留，不得覆蓋或刪除。

- [ ] **Step 3: 建 changelog.d 碎片**

建立 `changelog.d/feature-10-backend-matrix.md`：

```markdown
### Added
- Backend preset registry（`paulsha_hippo/backends.py`，契約 7）：`claude/codex/copilot-headless` 三個實測 argv presets（custom-argv 機制包裝、機制零新增）＋`gemini-headless`（僅 rc=41 觀察、無 round-trip 實證——spec §8 不猜 argv，候選 argv 降記錄）／`antigravity-headless`（命令契約未確認）標 unavailable；`hippo init --backend` 選單由 registry 驅動（unavailable 顯示不可選）、寫入時 argv[0] 絕對路徑化（fail-closed 不落半套 config）；`hippo doctor` 新增 per-preset probe 報告（service-effective 環境，能暴露 node-shebang 類 service PATH 故障）。
- `hippo dream supervise` 新增 `--once`／`--max-load`／`--promoter`／`--agent-command`：無 systemd 主機可前台單輪驗收（#10 原始 checklist 項）。
- 測試矩陣：mock 情境 ×4（散文包 JSON／截斷／non-zero／timeout→promoted/parked(invalid_output)/transient）、真蒸餾 smoke ×3 available preset（`PSC_ATOMIZE_LIVE` gate、probe 失敗轉 skip 回報）＋available⊆smoke 矩陣覆蓋 guard、openai-compatible 真端點 integration smoke（`HIPPO_SMOKE_OPENAI_*` gate）、supervise 無 systemd E2E。

### Docs
- 新增 `docs/backend-matrix.md`（preset argv 契約／probe／前置條件／實測狀態／unavailable 升級前提與 gemini rc=41 證據）；README backend 段同步（R-18）。
```

- [ ] **Step 4: 更新 `CHANGELOG.md` `[Unreleased]` 段（R-09 gate 以此檔為準；比照 PR-A Task 12 Step 2）**

把 Step 3 碎片同內容的 bullet 併入 `CHANGELOG.md` 的 `## [Unreleased]`——四條 bullet 全數歸 `### Added`（碎片的 `### Docs` 分組僅供 release 彙整；新增 `docs/backend-matrix.md` 在 CHANGELOG 亦屬 Added）。rebase 後 `[Unreleased]` 若已有 sibling 批次（如 PR-A Task 12 Step 2）建立的 `### Added` 標題，bullet 直接併入既有標題下；若無，以新標題插入既有 `### Fixed` 段之後、`## [0.1.0]` 之前：

```markdown
### Added
- Backend preset registry（`paulsha_hippo/backends.py`，契約 7）：`claude/codex/copilot-headless` 三個實測 argv presets（custom-argv 機制包裝、機制零新增）＋`gemini-headless`（僅 rc=41 觀察、無 round-trip 實證——spec §8 不猜 argv，候選 argv 降記錄）／`antigravity-headless`（命令契約未確認）標 unavailable；`hippo init --backend` 選單由 registry 驅動（unavailable 顯示不可選）、寫入時 argv[0] 絕對路徑化（fail-closed 不落半套 config）；`hippo doctor` 新增 per-preset probe 報告（service-effective 環境，能暴露 node-shebang 類 service PATH 故障）。
- `hippo dream supervise` 新增 `--once`／`--max-load`／`--promoter`／`--agent-command`：無 systemd 主機可前台單輪驗收（#10 原始 checklist 項）。
- 測試矩陣：mock 情境 ×4（散文包 JSON／截斷／non-zero／timeout→promoted/parked(invalid_output)/transient）、真蒸餾 smoke ×3 available preset（`PSC_ATOMIZE_LIVE` gate、probe 失敗轉 skip 回報）＋available⊆smoke 矩陣覆蓋 guard、openai-compatible 真端點 integration smoke（`HIPPO_SMOKE_OPENAI_*` gate）、supervise 無 systemd E2E。
- 新增 `docs/backend-matrix.md`（preset argv 契約／probe／前置條件／實測狀態／unavailable 升級前提與 gemini rc=41 證據）；README backend 段同步（R-18）。
```

（rebase 後若 `[Unreleased]` 已有其他批次的相同 `### Added` 標題，同樣把 bullet 併入既有標題下，不重複標題——R-04 格式。R-09 的 `_unreleased_has_bullet_entry` 只認 `CHANGELOG.md`，changelog.d 碎片本身**不**滿足 R-09、僅供 release 彙整。）

- [ ] **Step 5: 全套驗證**

依序執行並記錄輸出：

```bash
python3 -m pytest tests/ -q
python3 -m policy_check --repo .
```

預期：pytest `0 failed`（env-gated smoke 顯示為 skipped）；policy_check 全部 pass、無任何 FAIL（R-20/R-14 等不受本批影響；R-09 由 Step 4 的 `[Unreleased]` bullet 滿足——changelog.d 碎片供 release 彙整、本身不滿足 R-09；R-18 由 Step 1–2 satisfied；R-21 注意本批新檔全用 `~`/env 表示法）。

- [ ] **Step 6: 組 PR 說明（條件式 Closes #10——spec §3.5 關單條件）**

PR title：`feat(backends): #10 backend 矩陣——preset registry + init/doctor/smoke/supervise 全鏈`

PR body 模板（zh-tw；由 workflow 帶實證填寫）：

```markdown
## 摘要
- preset registry（契約 7）：paulsha_hippo/backends.py 單一真源；ops._BACKENDS 由 registry 導出
- claude/codex/copilot headless 依實測 argv 接線；gemini-headless 標 unavailable（僅 rc=41 觀察、無 round-trip 實證，spec §8 不猜 argv——候選 argv 降記錄）
- init --backend 選單化（gemini/antigravity 顯示不可選）；argv[0] 絕對路徑化（沿 PR-A resolve_backend_argv）
- doctor per-preset probe（service-effective 環境）
- mock 情境矩陣 ×4＋真蒸餾 smoke ×3 available preset（＋矩陣覆蓋 guard）＋openai-compatible integration smoke＋supervise --once 無 systemd E2E

## 驗收證據（貼實測輸出）
- mock 情境矩陣：`pytest tests/test_atomizer_backend_matrix.py`（4/4 pass）
- supervise E2E：`pytest tests/test_dream_supervise_e2e.py`＋本機前台一輪輸出
- doctor preset 矩陣輸出（本機）
- 真蒸餾 smoke（PSC_ATOMIZE_LIVE=1）：claude ＿／codex ＿／copilot ＿（pass/skip＋原因）；gemini-headless 不在矩陣（unavailable 固定缺項，見關單判定）
- openai-compatible 真端點：＿（pass／證據缺原因）

## 關單判定（spec §3.5 關單條件；gemini-headless 為固定缺項）
`gemini-headless` 本批定案 registry 標 unavailable（僅 rc=41 auth 失敗觀察、
無 stdin→stdout round-trip 實證，spec §8「不猜 argv」）——此為**固定缺項**
（拆新 issue 的一項，非 runtime skip、不隨本機 auth 狀態變動）。
故本 PR 一律 `Refs #10`＋label `policy-exempt:issue-link`（R-17），不用 `Closes #10`：
- 缺項（固定）：gemini-headless round-trip 實證＋argv_template 定案＋翻
  available＋補 live smoke（升級前提見 docs/backend-matrix.md；收口批次拆新
  issue 承接）
- 缺項（依實測補列）：＿＿（如 openai-compatible 真端點證據缺、三 available
  preset smoke 任一 skip）
收口批次把缺項拆成新 issue 後，才關 #10。

## Checklist（policy v1.0.12）
- [ ] CHANGELOG.md `[Unreleased]` 已有本批 bullet（R-09 由 `[Unreleased]` bullet 滿足）＋ changelog.d 碎片已附（changelog.d/feature-10-backend-matrix.md，供 release 彙整）
- [ ] `python3 -m policy_check --repo .` 零 failure
- [ ] 全套 pytest 通過（env-gated smoke 如實標 skip）
- [ ] R-18 docs 同步（README＋docs/backend-matrix.md）
- [ ] tier:shareable：新增文件無個人絕對路徑／機敏標記
- [ ] zh-tw／conventional-commit
```

判定規則（依 Task 7 Step 3 與 Task 8 Step 3 的實際結果）：
- gemini-headless 為固定缺項（registry unavailable，非 runtime skip）→ 本 PR **一律** `Refs #10`＋`policy-exempt:issue-link`；缺項固定含「gemini-headless round-trip 實證＋翻 available＋補 smoke（拆新 issue 承接）」。
- 三 available preset smoke／openai smoke／supervise E2E 依實際 pass/skip 補列缺項（如「openai-compatible 真端點實證」）；即使全 PASS 也不改用 `Closes`——#10 由收口批次拆出缺項 issue 後關閉。

- [ ] **Step 7: Commit**

```bash
git add docs/backend-matrix.md README.md CHANGELOG.md changelog.d/feature-10-backend-matrix.md
git commit -m "docs(backend): backend-matrix 矩陣文件＋README/CHANGELOG [Unreleased] 同步＋changelog 碎片（R-09/R-18/R-21）"
```

---

## Plan Self-Review 紀錄

- **Spec §3.5 覆蓋對照**：(1) declarative registry → Task 1；(2) codex/copilot 實測接線＋gemini（無 round-trip 實證）/antigravity 標 unavailable → Task 1/2；(3) init 選單化＋絕對路徑 → Task 2/3；(4) doctor per-preset probe → Task 4；(5) 真蒸餾 smoke＋四 mock 情境 → Task 5/7；(6) openai-compatible integration profile → Task 8；(7) supervise E2E → Task 6；關單條件 → Task 9。驗收四項（smoke 證據／mock 全過／doctor 判定／supervise E2E）各有對應 Step。
- **契約一致性**：`BackendPreset`／`PRESETS`／`_BACKENDS = tuple(PRESETS)` 與契約 7 逐字一致；`resolve_backend_argv`／`BackendUnavailableError` 僅按契約 2 簽名消費；parked 欄位斷言僅用契約 1 列舉鍵。
- **型別／命名一致性**：`probe_preset` 簽名在 Task 1（定義）、Task 4（doctor 呼叫）、Task 7（live gating）三處一致；`ProbeResult` 欄位序一致。
- **已知環境敏感點**（不是 placeholder，是執行時分歧的顯式處理）：Task 4 Step 5、Task 7 Step 3、Task 8 Step 3 的「本機預期」為 2026-07-10 基線，實際輸出以執行時為準（spec §1「驗收以執行時實測為準」）。
- **Adversarial review 修正（gemini-headless 降 unavailable）**：本機唯一觀察為 rc=41（auth 未備）、argv 僅由 `--help` 文字推得、無任何成功 stdin→stdout round-trip——依 spec §8「接不上就 registry 標 unavailable + 回報，不猜 argv」改列 unavailable（與 antigravity 同級：argv_template 置空，候選 argv 降記錄於 backends.py 註記＋docs/backend-matrix.md）；升級前提（真實認證後 round-trip 成功一次→argv_template 定案＋翻 available＋補 smoke，`SmokeMatrixCoverageTests` 機械強制）入 docs；真蒸餾 smoke 矩陣改 ×3 available preset；#10 收斂改以 gemini 為固定缺項（拆新 issue 的一項，非 runtime skip），PR 一律 `Refs #10`。
