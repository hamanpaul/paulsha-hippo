# PR-E Project Registry（#14）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** hippo 把 importer 已解析的 project mapping（slug/roots/remotes）持久化為 generated 檔 `project-hippo.yaml`（deterministic、atomic、opt-in），讀取端 union-read legacy `projects.yaml`，並以版本化契約文件 + producer contract test（逐 byte）錨定跨 repo 檔案契約。

**Architecture:** 新增 `paulsha_hippo/importer/registry.py` 作為 registry producer（render/parse/merge/locked atomic write/opt-in 設定），`paths.py` 提供路徑契約，`_git.py` 提供 worktree→主 repo root 歸併，`pipeline.py` 於 ingest 成功後 fail-open 回寫 discovery，`project_resolver.py` 預設載入改 union-read（legacy `projects.yaml` ∪ `project-hippo.yaml`）。cortex 側 consumer 不在本 repo，僅以 `docs/project-registry-contract.md` 保證檔案契約。

**Tech Stack:** Python 3.10+ stdlib（`fcntl`、`os.replace`、`pathlib`、`logging`、`unittest`/`pytest`）；手寫 YAML 子集 render/parse（沿用 `importer/config.py` 既有模式）；git CLI（`rev-parse --git-common-dir`）。

**Spec:** `docs/superpowers/specs/2026-07-10-all-issues-resolution-design.md` §3.3（驗收）、§6（workflow）、§7（合規）。
**Branch:** `feature/14-project-registry`（PR body 帶 `Closes #14`）。

## Global Constraints

以下逐字抄自 spec，全部 Task 隱含適用：

- spec §3.3.5：「寫入：temp file + atomic replace + 固定名 lock；**stdlib-only、零新依賴**。」
- spec §7：「分支一律 `feature/<issue>-<slug>`；禁 commit main。」
- spec §7：「每 code PR：changelog.d 碎片（repo 現行慣例）、PR checklist 全勾、`Closes #N`（R-17）、zh-tw（語言規範）、`policy_check` 零 failure。」
- spec §7：「`tier: shareable`（R-21）：所有新增文件（含本 spec、capability matrix、快照留言）不得含個人絕對路徑、機敏標記。」→ 本 plan 所有 fixture／docs／測試常數一律用 `/home/user`、`/data` 等中性路徑。
- spec §7：「R-18/R-22：behavior 變更同步 README／docs 引用」。
- spec §7：「測試新增全部進 CI 覆蓋（R-19；`tests.yml` 已自動跑 pytest）。」
- spec §2 非目標：「不 bump `VERSION`」。
- commit message 一律 zh-tw conventional-commit。
- **PR-E 零相依**（spec §6 拓撲：A、B、E 立即並行）：不得改動 `paulsha_hippo/ops.py`、`paulsha_hippo/ledger/processing.py`、`paulsha_hippo/backends.py`、`paulsha_hippo/moc/**`、atomizer——避免與 PR-A/B/C/D 撞檔。
- 所有命令於 repo 根目錄執行（worktree 根）。

**不做（邊界）：**
- 不搬移、不改寫 legacy `projects.yaml`（非破壞過渡，spec §3.3.6）。
- `moc/search._project_roots` 與 `atomizer.config.known_projects_file` 維持只讀 legacy（manual curation 面；亦避免撞 PR-A/PR-B 地盤）。union-read 只接在 `resolve_project` 預設載入。
- 不新增 CLI 子命令（spec §3.3 未要求；跨批次契約僅 PR-A/PR-F 加 CLI）。
- cortex 側 consumer（project-cortex.yaml union、手改資料遷移）不在本 repo——收口批次開 paulshaclaw issue（spec §3.3 跨 repo 邊界）。

---

### Task 1: 路徑契約 `paths.project_registry_path()`

**Files:**
- Modify: `paulsha_hippo/paths.py:130-141`（於 `projects_config_path` 之後、`resolution_report` 之前插入新函式）
- Create: `tests/test_project_registry.py`

**Interfaces:**
- Consumes: `paths._env_path(name: str) -> Path | None`、`paths.agents_path(*parts) -> Path`（既有）
- Produces: `paths.project_registry_path(memory_root_value: str | Path | None = None) -> Path` —— 回傳 generated registry 檔位置，優先序與 `projects_config_path` 完全同構（PSC_CONFIG_ROOT → memory_root sibling → agents_root），落點為 `<config 根>/paulsha/project-hippo.yaml`

- [ ] **Step 0: 分支準備（若 workflow 已在正確分支的 worktree 內啟動則跳過）**

```bash
git checkout main && git pull --ff-only && git checkout -b feature/14-project-registry
```

Run: `git branch --show-current`
Expected: `feature/14-project-registry`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_project_registry.py`，內容：

```python
import os
import unittest
from unittest import mock

from paulsha_hippo import paths


class ProjectRegistryPathTests(unittest.TestCase):
    def test_prefers_psc_config_root(self):
        with mock.patch.dict(os.environ, {"PSC_CONFIG_ROOT": "/data/psc-config-root"}, clear=False):
            self.assertEqual(
                str(paths.project_registry_path("/data/custom-memory")),
                "/data/psc-config-root/.agents/config/paulsha/project-hippo.yaml",
            )

    def test_paulshaclaw_shaped_psc_config_root_uses_home_base(self):
        with mock.patch.dict(
            os.environ, {"PSC_CONFIG_ROOT": "/data/home-x/.config/paulshaclaw"}, clear=False
        ):
            self.assertEqual(
                str(paths.project_registry_path()),
                "/data/home-x/.agents/config/paulsha/project-hippo.yaml",
            )

    def test_memory_root_variant_uses_sibling_config_dir(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PSC_CONFIG_ROOT", None)
            self.assertEqual(
                str(paths.project_registry_path("/data/agents/memory")),
                "/data/agents/config/paulsha/project-hippo.yaml",
            )

    def test_default_under_agents_root(self):
        with mock.patch.dict(os.environ, {"HIPPO_AGENTS_ROOT": "/data/agents2"}, clear=False):
            for name in ("PSC_CONFIG_ROOT", "PSC_AGENTS_ROOT"):
                os.environ.pop(name, None)
            self.assertEqual(
                str(paths.project_registry_path()),
                "/data/agents2/config/paulsha/project-hippo.yaml",
            )


if __name__ == "__main__":
    unittest.main()
```

（註：`mock.patch.dict` 進場時快照整個 environ、離場時整體還原，故 context 內 `os.environ.pop(...)` 安全。此模式沿用 `tests/test_project_resolver.py::test_default_projects_path_prefers_psc_config_root`。）

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: 4 個測試全 FAIL，錯誤為 `AttributeError: module 'paulsha_hippo.paths' has no attribute 'project_registry_path'`

- [ ] **Step 3: 最小實作**

在 `paulsha_hippo/paths.py` 用 Edit 插入（old_string 為既有檔案內容結尾兩函式交界處）：

old_string:
```python
    return agents_path("config", "projects.yaml")


def resolution_report() -> dict[str, str]:
```

new_string:
```python
    return agents_path("config", "projects.yaml")


def project_registry_path(memory_root_value: str | Path | None = None) -> Path:
    """project-hippo.yaml（generated registry）定位——與 projects.yaml 同 config 根、paulsha/ 子層。

    契約：docs/project-registry-contract.md（issue #14）。優先序與 projects_config_path 同構。
    """
    legacy_base = _env_path("PSC_CONFIG_ROOT")
    if legacy_base is not None:
        if legacy_base.name == "paulshaclaw" and legacy_base.parent.name == ".config":
            base = legacy_base.parents[1]
        else:
            base = legacy_base
        return base / ".agents" / "config" / "paulsha" / "project-hippo.yaml"
    if memory_root_value is not None:
        return Path(memory_root_value).expanduser().parent / "config" / "paulsha" / "project-hippo.yaml"
    return agents_path("config", "paulsha", "project-hippo.yaml")


def resolution_report() -> dict[str, str]:
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/paths.py tests/test_project_registry.py
git commit -m "feat(paths): project registry 路徑契約 project_registry_path（#14）"
```

---

### Task 2: `_git.git_main_toplevel()` —— worktree 歸併主 repo root

**Files:**
- Modify: `paulsha_hippo/importer/_git.py:1-5`（import 區）與檔尾（`sibling_repo_count` 之後，line 69 後追加）
- Test: `tests/test_git_helper.py`（檔尾 `GitHelperTests` class 內追加測試方法）

**Interfaces:**
- Consumes: `_git._run_git(args: list[str], cwd, timeout) -> Optional[str]`（既有）
- Produces: `_git.git_main_toplevel(toplevel: str | Path | None) -> Optional[str]` —— linked worktree 回傳主 repo root；一般 checkout 回傳自身；偵測失敗回退 `str(toplevel)`；falsy 輸入回 `None`；never raises

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_git_helper.py` 的 `GitHelperTests` class 尾端（`test_nested_outer_repo_does_not_inherit` 方法之後、class 結束前）追加：

```python
    def test_git_main_toplevel_worktree_resolves_main_root(self) -> None:
        with TemporaryDirectory() as tmp:
            main = Path(tmp) / "mainrepo"
            main.mkdir()
            _init_repo(main)
            subprocess.run(
                ["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@example.com",
                 "commit", "--allow-empty", "-m", "init"],
                check=True, capture_output=True,
            )
            worktree = Path(tmp) / "wt"
            subprocess.run(
                ["git", "-C", str(main), "worktree", "add", "-b", "wt-branch", str(worktree)],
                check=True, capture_output=True,
            )
            wt_top = _git.git_toplevel(str(worktree))
            self.assertEqual(Path(wt_top).resolve(), worktree.resolve())
            main_top = _git.git_main_toplevel(wt_top)
            self.assertEqual(Path(main_top).resolve(), main.resolve())

    def test_git_main_toplevel_normal_checkout_returns_itself(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "solo"
            repo.mkdir()
            _init_repo(repo)
            top = _git.git_toplevel(str(repo))
            self.assertEqual(Path(_git.git_main_toplevel(top)).resolve(), repo.resolve())

    def test_git_main_toplevel_falsy_returns_none(self) -> None:
        self.assertIsNone(_git.git_main_toplevel(None))
        self.assertIsNone(_git.git_main_toplevel(""))

    def test_git_main_toplevel_non_repo_falls_back_to_input(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(_git.git_main_toplevel(tmp), str(tmp))
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_git_helper.py -v -k git_main_toplevel`
Expected: 4 個測試 FAIL，`AttributeError: module 'paulsha_hippo.importer._git' has no attribute 'git_main_toplevel'`

- [ ] **Step 3: 最小實作**

`paulsha_hippo/importer/_git.py` 先改 import 區。

old_string:
```python
from __future__ import annotations
from pathlib import Path
import subprocess
from typing import Optional
```

new_string:
```python
from __future__ import annotations
import os
from pathlib import Path
import subprocess
from typing import Optional
```

再於檔尾（`sibling_repo_count` 函式之後）追加：

```python
def git_main_toplevel(toplevel: str | Path | None) -> Optional[str]:
    """把（可能是 linked worktree 的）toplevel 歸併為主 repo root。

    linked worktree → 主 checkout root；一般 checkout → 自身；
    rev-parse 失敗 → 回退輸入值；falsy 輸入 → None。best-effort、never raises。
    """
    if not toplevel:
        return None
    try:
        common = _run_git(["rev-parse", "--git-common-dir"], cwd=toplevel)
        if not common:
            return str(toplevel)
        common_path = Path(os.path.normpath(str(Path(toplevel, common))))
        if common_path.name == ".git":
            return str(common_path.parent)
        return str(toplevel)
    except Exception:
        return str(toplevel)
```

（原理：linked worktree 下 `--git-common-dir` 回主 repo 的絕對 `.git` 路徑；主 checkout 於 root 執行回相對 `.git`。`Path(toplevel, common)` 在 common 為絕對路徑時以絕對值為準。）

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_git_helper.py -v`
Expected: 全部 PASS（既有 6 個 + 新 4 個 = `10 passed`）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/_git.py tests/test_git_helper.py
git commit -m "feat(importer): git_main_toplevel——worktree 歸併主 repo root（#14）"
```

---

### Task 3: `registry.py` render / parse —— deterministic YAML v1

**Files:**
- Create: `paulsha_hippo/importer/registry.py`
- Modify: `tests/test_project_registry.py`（import 區 + 檔尾追加 class）

**Interfaces:**
- Consumes: `config.ProjectConfig`（frozen dataclass：`slug: str, roots: tuple[str, ...], remotes: tuple[str, ...], aliases: tuple[str, ...]`）、`config._trimmed_lines(text) -> list[tuple[int, str]]`、`config._inline_list(value) -> tuple[str, ...]`、`paths.project_registry_path`（Task 1）
- Produces:
  - `registry.SCHEMA_VERSION: int = 1`
  - `registry.REGISTRY_FILENAME = "project-hippo.yaml"`、`registry.LOCK_FILENAME = ".project-hippo.yaml.lock"`、`registry.TMP_FILENAME = ".project-hippo.yaml.tmp"`
  - `registry.GENERATED_HEADER_LINES: tuple[str, str, str]`
  - `registry.render_registry(projects: Iterable[ProjectConfig]) -> str`（canonical bytes：slug 排序、清單去重排序、LF、檔尾一個換行）
  - `registry.parse_registry(text: str) -> tuple[ProjectConfig, ...]`
  - `registry.registry_schema_version(text: str) -> int | None`
  - `registry.load_registry(path: str | Path | None) -> tuple[ProjectConfig, ...]`（缺檔/讀失敗回 `()`，fail-open）
  - `registry.default_registry_path(memory_root: str | Path | None = None) -> Path`

- [ ] **Step 1: 寫失敗測試**

`tests/test_project_registry.py` import 區改為（Edit old→new）：

old_string:
```python
import os
import unittest
from unittest import mock

from paulsha_hippo import paths
```

new_string:
```python
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo import paths
from paulsha_hippo.importer.config import ProjectConfig
from paulsha_hippo.importer.registry import (
    load_registry,
    parse_registry,
    registry_schema_version,
    render_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
```

檔尾（`if __name__ == "__main__":` 之前）追加：

```python
class _ScratchDirTestCase(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass


class RenderParseTests(_ScratchDirTestCase):
    def test_render_registry_is_deterministic_and_sorted(self):
        projects = (
            ProjectConfig(slug="zeta", roots=("/data/z2", "/data/z1", "/data/z1"), remotes=()),
            ProjectConfig(
                slug="alpha",
                roots=(),
                remotes=("github.com/acme/alpha",),
                aliases=("a2", "a1"),
            ),
        )
        expected = (
            "# GENERATED — 本檔由 paulsha-hippo 自動產生（project registry discovery record），請勿手改。\n"
            "# 使用者 override 請寫 manual 檔（projects.yaml / project-cortex.yaml），詳見契約文件。\n"
            "# contract: docs/project-registry-contract.md\n"
            "schema_version: 1\n"
            "projects:\n"
            "  - slug: alpha\n"
            "    roots: []\n"
            "    remotes:\n"
            "      - github.com/acme/alpha\n"
            "    aliases: [a1, a2]\n"
            "  - slug: zeta\n"
            "    roots:\n"
            "      - /data/z1\n"
            "      - /data/z2\n"
            "    remotes: []\n"
            "    aliases: []\n"
        )
        self.assertEqual(render_registry(projects), expected)

    def test_render_registry_empty_projects(self):
        rendered = render_registry(())
        self.assertTrue(rendered.endswith("schema_version: 1\nprojects: []\n"))

    def test_parse_registry_round_trips_render(self):
        projects = (
            ProjectConfig(
                slug="alpha",
                roots=("/data/a",),
                remotes=("github.com/acme/alpha",),
                aliases=("a1",),
            ),
            ProjectConfig(slug="zeta", roots=("/data/z",), remotes=(), aliases=()),
        )
        self.assertEqual(parse_registry(render_registry(projects)), projects)

    def test_parse_registry_tolerates_comments_and_empty(self):
        self.assertEqual(parse_registry(""), ())
        self.assertEqual(parse_registry("# only comment\nschema_version: 1\nprojects: []\n"), ())

    def test_registry_schema_version_reads_header(self):
        self.assertEqual(registry_schema_version("schema_version: 1\nprojects: []\n"), 1)
        self.assertIsNone(registry_schema_version("projects: []\n"))
        self.assertIsNone(registry_schema_version("schema_version: abc\n"))

    def test_load_registry_missing_file_returns_empty(self):
        self.assertEqual(load_registry(self.root / "absent.yaml"), ())
        self.assertEqual(load_registry(None), ())

    def test_load_registry_reads_rendered_file(self):
        path = self.root / "project-hippo.yaml"
        projects = (ProjectConfig(slug="alpha", roots=("/data/a",), remotes=()),)
        path.write_text(render_registry(projects), encoding="utf-8")
        self.assertEqual(load_registry(path), projects)
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'paulsha_hippo.importer.registry'`

- [ ] **Step 3: 最小實作**

建立 `paulsha_hippo/importer/registry.py`：

```python
"""Project registry producer（#14）：generated project-hippo.yaml 的 render/parse/寫入。

契約文件：docs/project-registry-contract.md（schema_version 對應）。
stdlib-only；手寫 YAML 子集 parser 沿用 importer/config.py 既有模式。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from paulsha_hippo import paths

from .config import ProjectConfig, _inline_list, _trimmed_lines

LOGGER = logging.getLogger("paulsha_hippo.importer")

SCHEMA_VERSION = 1
REGISTRY_FILENAME = "project-hippo.yaml"
LOCK_FILENAME = ".project-hippo.yaml.lock"
TMP_FILENAME = ".project-hippo.yaml.tmp"

GENERATED_HEADER_LINES = (
    "# GENERATED — 本檔由 paulsha-hippo 自動產生（project registry discovery record），請勿手改。",
    "# 使用者 override 請寫 manual 檔（projects.yaml / project-cortex.yaml），詳見契約文件。",
    "# contract: docs/project-registry-contract.md",
)


def default_registry_path(memory_root: str | Path | None = None) -> Path:
    return paths.project_registry_path(memory_root)


def render_registry(projects: Iterable[ProjectConfig]) -> str:
    """輸出 canonical bytes：slug 字典序、各清單去重排序、LF、檔尾恰一換行。"""
    lines: list[str] = list(GENERATED_HEADER_LINES)
    lines.append(f"schema_version: {SCHEMA_VERSION}")
    ordered = sorted(projects, key=lambda project: project.slug)
    if not ordered:
        lines.append("projects: []")
        return "\n".join(lines) + "\n"
    lines.append("projects:")
    for project in ordered:
        lines.append(f"  - slug: {project.slug}")
        for key, values in (("roots", project.roots), ("remotes", project.remotes)):
            deduped = sorted(set(values))
            if deduped:
                lines.append(f"    {key}:")
                lines.extend(f"      - {item}" for item in deduped)
            else:
                lines.append(f"    {key}: []")
        alias_values = sorted(set(project.aliases))
        if alias_values:
            lines.append(f"    aliases: [{', '.join(alias_values)}]")
        else:
            lines.append("    aliases: []")
    return "\n".join(lines) + "\n"


def _finalize_registry_item(
    projects: list[ProjectConfig], current: dict[str, list[str] | str] | None
) -> None:
    if current is None:
        return
    slug = str(current.get("slug") or "").strip()
    if not slug:
        return
    projects.append(
        ProjectConfig(
            slug=slug,
            roots=tuple(str(item) for item in current.get("roots", [])),
            remotes=tuple(str(item) for item in current.get("remotes", [])),
            aliases=tuple(str(item) for item in current.get("aliases", [])),
        )
    )


def parse_registry(text: str) -> tuple[ProjectConfig, ...]:
    projects: list[ProjectConfig] = []
    current: dict[str, list[str] | str] | None = None
    current_list_key: str | None = None
    in_projects = False
    for indent, line in _trimmed_lines(text):
        stripped = line.strip()
        if indent == 0:
            _finalize_registry_item(projects, current)
            current = None
            current_list_key = None
            in_projects = stripped == "projects:"
            continue
        if not in_projects:
            continue
        if indent == 2 and stripped.startswith("- "):
            _finalize_registry_item(projects, current)
            current = {}
            current_list_key = None
            rest = stripped[2:].strip()
            if ":" in rest:
                key, value = rest.split(":", 1)
                if key.strip() == "slug":
                    current["slug"] = value.strip().strip("\"'")
            continue
        if current is None:
            continue
        if indent == 4 and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = raw_value.strip()
            if key in {"roots", "remotes"}:
                if value.startswith("["):
                    current[key] = list(_inline_list(value))
                    current_list_key = None
                else:
                    current[key] = []
                    current_list_key = key
                continue
            if key == "aliases":
                current["aliases"] = list(_inline_list(value))
                current_list_key = None
                continue
            current[key] = value.strip("\"'")
            current_list_key = None
            continue
        if indent >= 6 and stripped.startswith("- ") and current_list_key in {"roots", "remotes"}:
            current.setdefault(current_list_key, []).append(stripped[2:].strip().strip("\"'"))
    _finalize_registry_item(projects, current)
    return tuple(projects)


def registry_schema_version(text: str) -> int | None:
    for indent, line in _trimmed_lines(text):
        stripped = line.strip()
        if indent == 0 and stripped.startswith("schema_version:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            try:
                return int(value)
            except ValueError:
                return None
    return None


def load_registry(path: str | Path | None) -> tuple[ProjectConfig, ...]:
    """讀 generated registry；缺檔／讀失敗回空（fail-open：registry 永不阻斷讀取端）。"""
    if path is None:
        return ()
    registry_path = Path(path)
    try:
        text = registry_path.read_text(encoding="utf-8")
    except OSError:
        return ()
    version = registry_schema_version(text)
    if version is not None and version > SCHEMA_VERSION:
        LOGGER.warning(
            "project registry schema_version %s 高於支援的 %s，仍以 v%s 規則盡力解析: %s",
            version,
            SCHEMA_VERSION,
            SCHEMA_VERSION,
            registry_path,
        )
    return parse_registry(text)
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: `11 passed`（4 path + 7 render/parse）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/registry.py tests/test_project_registry.py
git commit -m "feat(importer): registry render/parse——deterministic project-hippo.yaml v1（#14）"
```

---

### Task 4: `merge_discovery` + `record_discovery`（固定 lock、冪等）

本 Task 的 `record_discovery` 先以**直接寫檔**落地（最小實作）；原子性（temp+replace）由 Task 5 以失敗測試驅動升級——TDD 順序不可顛倒。

**Files:**
- Modify: `paulsha_hippo/importer/registry.py`（import 區 + 檔尾追加兩函式）
- Modify: `tests/test_project_registry.py`（import 區 + 檔尾追加 class）

**Interfaces:**
- Consumes: Task 3 全部（`render_registry`/`parse_registry`/`ProjectConfig`/常數）
- Produces:
  - `registry.merge_discovery(existing: Iterable[ProjectConfig], incoming: ProjectConfig) -> tuple[ProjectConfig, ...]` —— 同 slug 併集（roots/remotes/aliases 去重排序），新 slug 追加
  - `registry.record_discovery(*, slug: str, roots: Sequence[str] = (), remotes: Sequence[str] = (), aliases: Sequence[str] = (), registry_path: str | Path) -> bool` —— 固定名 flock 互斥；內容未變回 `False` 且不重寫；變更回 `True`；空 slug raise `ValueError`

- [ ] **Step 1: 寫失敗測試**

`tests/test_project_registry.py` import 區 Edit：

old_string:
```python
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
```

new_string:
```python
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
```

registry import Edit：

old_string:
```python
from paulsha_hippo.importer.registry import (
    load_registry,
    parse_registry,
    registry_schema_version,
    render_registry,
)
```

new_string:
```python
from paulsha_hippo.importer.registry import (
    load_registry,
    merge_discovery,
    parse_registry,
    record_discovery,
    registry_schema_version,
    render_registry,
)
```

檔尾追加：

```python
class MergeDiscoveryTests(unittest.TestCase):
    def test_merge_unions_and_sorts_same_slug(self):
        existing = (
            ProjectConfig(slug="alpha", roots=("/data/b",), remotes=("github.com/acme/alpha",)),
        )
        merged = merge_discovery(
            existing, ProjectConfig(slug="alpha", roots=("/data/a", "/data/b"), remotes=())
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].roots, ("/data/a", "/data/b"))
        self.assertEqual(merged[0].remotes, ("github.com/acme/alpha",))

    def test_merge_appends_new_slug(self):
        existing = (ProjectConfig(slug="alpha", roots=("/data/a",), remotes=()),)
        merged = merge_discovery(existing, ProjectConfig(slug="beta", roots=("/data/b",), remotes=()))
        self.assertEqual([project.slug for project in merged], ["alpha", "beta"])


class RecordDiscoveryTests(_ScratchDirTestCase):
    def registry_path(self) -> Path:
        return self.root / "project-hippo.yaml"

    def test_first_discovery_creates_file(self):
        path = self.registry_path()
        changed = record_discovery(
            slug="alpha",
            roots=("/data/a",),
            remotes=("github.com/acme/alpha",),
            registry_path=path,
        )
        self.assertTrue(changed)
        parsed = parse_registry(path.read_text(encoding="utf-8"))
        self.assertEqual([project.slug for project in parsed], ["alpha"])

    def test_repeat_discovery_is_idempotent(self):
        path = self.registry_path()
        record_discovery(
            slug="alpha",
            roots=("/data/a",),
            remotes=("github.com/acme/alpha",),
            registry_path=path,
        )
        before = path.read_bytes()
        changed = record_discovery(
            slug="alpha",
            roots=("/data/a",),
            remotes=("github.com/acme/alpha",),
            registry_path=path,
        )
        self.assertFalse(changed)
        self.assertEqual(path.read_bytes(), before)

    def test_empty_slug_raises_value_error(self):
        with self.assertRaises(ValueError):
            record_discovery(slug="", roots=("/data/a",), registry_path=self.registry_path())

    def test_lock_and_artifacts_use_fixed_names_only(self):
        path = self.registry_path()
        for index in range(5):
            record_discovery(slug=f"p-{index}", roots=(f"/data/p-{index}",), registry_path=path)
        names = {item.name for item in self.root.iterdir()}
        self.assertLessEqual(
            names,
            {"project-hippo.yaml", ".project-hippo.yaml.lock", ".project-hippo.yaml.tmp"},
        )
        self.assertIn(".project-hippo.yaml.lock", names)

    def test_concurrent_discoveries_all_land(self):
        path = self.registry_path()
        slugs = [f"proj-{index:02d}" for index in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(
                pool.map(
                    lambda slug: record_discovery(
                        slug=slug, roots=(f"/data/{slug}",), registry_path=path
                    ),
                    slugs,
                )
            )
        parsed = parse_registry(path.read_text(encoding="utf-8"))
        self.assertEqual([project.slug for project in parsed], sorted(slugs))
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: collection error `ImportError: cannot import name 'merge_discovery' from 'paulsha_hippo.importer.registry'`

- [ ] **Step 3: 最小實作**

`paulsha_hippo/importer/registry.py` import 區 Edit：

old_string:
```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable
```

new_string:
```python
from __future__ import annotations

import fcntl
import logging
from pathlib import Path
from typing import Iterable, Sequence
```

檔尾追加：

```python
def merge_discovery(
    existing: Iterable[ProjectConfig], incoming: ProjectConfig
) -> tuple[ProjectConfig, ...]:
    """同 slug 併集（roots/remotes/aliases 去重排序）；新 slug 追加。"""
    merged: list[ProjectConfig] = []
    found = False
    for project in existing:
        if project.slug != incoming.slug:
            merged.append(project)
            continue
        found = True
        merged.append(
            ProjectConfig(
                slug=project.slug,
                roots=tuple(sorted(set(project.roots) | set(incoming.roots))),
                remotes=tuple(sorted(set(project.remotes) | set(incoming.remotes))),
                aliases=tuple(sorted(set(project.aliases) | set(incoming.aliases))),
            )
        )
    if not found:
        merged.append(incoming)
    return tuple(merged)


def record_discovery(
    *,
    slug: str,
    roots: Sequence[str] = (),
    remotes: Sequence[str] = (),
    aliases: Sequence[str] = (),
    registry_path: str | Path,
) -> bool:
    """把一筆 discovery 併入 generated registry；回傳檔案是否變更。

    互斥：同目錄固定名 lock（LOCK_FILENAME）flock(LOCK_EX)——固定名、非 per-key，
    不產生無界 lock namespace（對照 #19 教訓）。內容未變則跳寫（冪等）。
    """
    if not slug:
        raise ValueError("slug must be non-empty")
    path = Path(registry_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(LOCK_FILENAME)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            try:
                existing_text: str | None = path.read_text(encoding="utf-8")
            except OSError:
                existing_text = None
            existing = parse_registry(existing_text) if existing_text is not None else ()
            incoming = ProjectConfig(
                slug=slug,
                roots=tuple(str(item) for item in roots if item),
                remotes=tuple(str(item) for item in remotes if item),
                aliases=tuple(str(item) for item in aliases if item),
            )
            rendered = render_registry(merge_discovery(existing, incoming))
            if existing_text == rendered:
                return False
            path.write_text(rendered, encoding="utf-8")
            return True
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: `18 passed`

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/registry.py tests/test_project_registry.py
git commit -m "feat(importer): record_discovery——固定 lock + 冪等合併寫入（#14）"
```

---

### Task 5: Crash recovery —— temp file + atomic replace

**Files:**
- Modify: `paulsha_hippo/importer/registry.py`（import 區 + `record_discovery` 寫檔段）
- Modify: `tests/test_project_registry.py`（import 區 + 檔尾追加 class）

**Interfaces:**
- Consumes: Task 4 的 `record_discovery`
- Produces: `record_discovery` 升級為 `TMP_FILENAME` 暫存 + `os.replace` 原子替換（對外簽名不變）；不變量：**任一時刻 registry 檔要嘛不存在、要嘛是完整 canonical 內容**（寫一半殺進程 → 舊檔完好）

- [ ] **Step 1: 寫失敗測試**

`tests/test_project_registry.py` import 區 Edit：

old_string:
```python
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
```

new_string:
```python
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
```

檔尾追加：

```python
class CrashRecoveryTests(_ScratchDirTestCase):
    def test_interrupted_replace_keeps_previous_bytes_and_recovers(self):
        path = self.root / "project-hippo.yaml"
        record_discovery(slug="alpha", roots=("/data/a",), registry_path=path)
        before = path.read_bytes()
        with mock.patch(
            "paulsha_hippo.importer.registry.os.replace",
            side_effect=OSError("simulated crash"),
        ):
            with self.assertRaises(OSError):
                record_discovery(slug="beta", roots=("/data/b",), registry_path=path)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(record_discovery(slug="beta", roots=("/data/b",), registry_path=path))
        slugs = [project.slug for project in parse_registry(path.read_text(encoding="utf-8"))]
        self.assertEqual(slugs, ["alpha", "beta"])

    def test_sigkill_mid_write_leaves_complete_canonical_file(self):
        path = self.root / "project-hippo.yaml"
        record_discovery(slug="crash-proj", roots=("/data/crash/seed",), registry_path=path)
        child_code = textwrap.dedent(
            """
            import sys
            from paulsha_hippo.importer.registry import record_discovery

            registry_path = sys.argv[1]
            index = 0
            while True:
                index += 1
                record_discovery(
                    slug="crash-proj",
                    roots=(f"/data/crash/root-{index:06d}",),
                    registry_path=registry_path,
                )
            """
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code, str(path)],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.4)
        finally:
            proc.kill()
            proc.wait(timeout=5)
        text = path.read_text(encoding="utf-8")
        parsed = parse_registry(text)
        self.assertEqual([project.slug for project in parsed], ["crash-proj"])
        self.assertGreaterEqual(len(parsed[0].roots), 1)
        # canonical 自洽：任何殘缺（torn write）都會使 render(parse(x)) != x
        self.assertEqual(render_registry(parsed), text)
        names = {item.name for item in self.root.iterdir()}
        self.assertLessEqual(
            names,
            {"project-hippo.yaml", ".project-hippo.yaml.lock", ".project-hippo.yaml.tmp"},
        )
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_project_registry.py::CrashRecoveryTests -v`
Expected: `test_interrupted_replace_keeps_previous_bytes_and_recovers` 紅燈——`AttributeError: <module 'paulsha_hippo.importer.registry'> does not have the attribute 'os'`（Task 4 的最小實作直接寫檔、尚未 import os / 呼叫 `os.replace`，mock.patch 目標不存在）。SIGKILL 測試可能僥倖 PASS（direct write 非確定性），不影響紅燈判定。

- [ ] **Step 3: 實作原子替換**

`paulsha_hippo/importer/registry.py` import 區 Edit：

old_string:
```python
import fcntl
import logging
from pathlib import Path
```

new_string:
```python
import fcntl
import logging
import os
from pathlib import Path
```

`record_discovery` 寫檔段 Edit：

old_string:
```python
            rendered = render_registry(merge_discovery(existing, incoming))
            if existing_text == rendered:
                return False
            path.write_text(rendered, encoding="utf-8")
            return True
```

new_string:
```python
            rendered = render_registry(merge_discovery(existing, incoming))
            if existing_text == rendered:
                return False
            tmp_path = path.with_name(TMP_FILENAME)
            tmp_path.write_text(rendered, encoding="utf-8")
            os.replace(tmp_path, path)
            return True
```

（crash 遺留的 stale `.tmp` 固定名，下一次成功寫入直接覆蓋——毋須清理程序。）

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: `20 passed`

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/registry.py tests/test_project_registry.py
git commit -m "feat(importer): registry 寫入原子化——temp+os.replace，crash 舊檔完好（#14）"
```

---

### Task 6: Opt-in 設定 `project_registry.auto_write`

**Files:**
- Modify: `paulsha_hippo/importer/registry.py`（檔尾追加一函式）
- Modify: `tests/test_project_registry.py`（import 區 + 檔尾追加 class）

**Interfaces:**
- Consumes: `paths.hippo_config_root() -> Path`（既有；honors `HIPPO_CONFIG_ROOT` env）
- Produces: `registry.auto_write_enabled(config_path: str | Path | None = None) -> bool` —— 讀 `<hippo_config_root>/config.yaml` 的 `project_registry.auto_write`；缺檔／缺鍵／非 truthy 一律 `False`（預設 off）

- [ ] **Step 1: 寫失敗測試**

registry import Edit：

old_string:
```python
from paulsha_hippo.importer.registry import (
    load_registry,
    merge_discovery,
    parse_registry,
    record_discovery,
    registry_schema_version,
    render_registry,
)
```

new_string:
```python
from paulsha_hippo.importer.registry import (
    auto_write_enabled,
    load_registry,
    merge_discovery,
    parse_registry,
    record_discovery,
    registry_schema_version,
    render_registry,
)
```

檔尾追加：

```python
class AutoWriteEnabledTests(_ScratchDirTestCase):
    def write_config(self, text: str) -> Path:
        path = self.root / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_config_defaults_off(self):
        self.assertFalse(auto_write_enabled(self.root / "absent.yaml"))

    def test_enabled_when_true(self):
        path = self.write_config("memory_root: /data/agents/memory\nproject_registry:\n  auto_write: true\n")
        self.assertTrue(auto_write_enabled(path))

    def test_disabled_when_false_or_wrong_section(self):
        self.assertFalse(auto_write_enabled(self.write_config("project_registry:\n  auto_write: false\n")))
        self.assertFalse(auto_write_enabled(self.write_config("other_section:\n  auto_write: true\n")))
        self.assertFalse(auto_write_enabled(self.write_config("auto_write: true\n")))

    def test_truthy_variants(self):
        for raw in ("true", "True", "yes", "on", "1", '"true"'):
            path = self.write_config(f"project_registry:\n  auto_write: {raw}\n")
            self.assertTrue(auto_write_enabled(path), raw)

    def test_default_path_uses_hippo_config_root(self):
        with mock.patch.dict(os.environ, {"HIPPO_CONFIG_ROOT": str(self.root)}, clear=False):
            self.write_config("project_registry:\n  auto_write: true\n")
            self.assertTrue(auto_write_enabled())
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: collection error `ImportError: cannot import name 'auto_write_enabled'`

- [ ] **Step 3: 最小實作**

`paulsha_hippo/importer/registry.py` 檔尾追加：

```python
def auto_write_enabled(config_path: str | Path | None = None) -> bool:
    """讀 project_registry.auto_write（預設 off）；缺檔／缺鍵一律 False（opt-in）。"""
    path = Path(config_path) if config_path is not None else paths.hippo_config_root() / "config.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    in_section = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            in_section = stripped == "project_registry:"
            continue
        if in_section and ":" in stripped:
            key, value = stripped.split(":", 1)
            if key.strip() == "auto_write":
                return value.strip().strip("\"'").lower() in {"true", "yes", "on", "1"}
    return False
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: `25 passed`

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/registry.py tests/test_project_registry.py
git commit -m "feat(importer): project_registry.auto_write opt-in 設定讀取（#14）"
```

---

### Task 7: 讀取端 union-read（legacy `projects.yaml` ∪ `project-hippo.yaml`）

**Files:**
- Modify: `paulsha_hippo/importer/registry.py`（import 區 + 檔尾追加一函式）
- Modify: `paulsha_hippo/importer/project_resolver.py:10`（import）與 `:75-84`（簽名 + 預設載入）
- Modify: `tests/test_project_resolver.py`（import 區 + 檔尾追加 class，`if __name__` 之前）

**Interfaces:**
- Consumes: `config.load_projects_config(path) -> ProjectsConfig`、`config.ProjectsConfig(projects: tuple[ProjectConfig, ...], aliases: dict[str, str])`（既有）、Task 3 `load_registry`、Task 1 `default_registry_path`
- Produces:
  - `registry.load_union_projects_config(legacy_path: str | Path | None, registry_path: str | Path | None) -> ProjectsConfig` —— legacy 條目在前、同 slug 併 roots/remotes/aliases（legacy 值序在前）、alias 衝突 manual 優先（`LOGGER.warning`）
  - `resolve_project(*, cwd=None, git_toplevel=None, remote_url=None, projects=None, config_path=None, memory_root=None, registry_path: str | None = None) -> str` —— 新增 `registry_path` kwarg；`projects=None` 時預設載入改 union-read（既有呼叫端 hooks/wakeup/backfill/pipeline 全部自動生效）

- [ ] **Step 1: 寫失敗測試**

`tests/test_project_resolver.py` import 區 Edit：

old_string:
```python
from paulsha_hippo.importer.config import ProjectsConfig, default_projects_path, load_projects_config
from paulsha_hippo.importer.project_resolver import resolve_project
```

new_string:
```python
from paulsha_hippo.importer.config import (
    ProjectConfig,
    ProjectsConfig,
    default_projects_path,
    load_projects_config,
)
from paulsha_hippo.importer.project_resolver import resolve_project
from paulsha_hippo.importer.registry import load_union_projects_config, render_registry
```

檔尾（`if __name__ == "__main__":` 之前）追加：

```python
class UnionReadTests(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def write_projects_config(self, text: str) -> Path:
        path = self.root / "projects.yaml"
        path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")
        return path

    def write_registry(self, projects) -> Path:
        path = self.root / "config" / "paulsha" / "project-hippo.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_registry(projects), encoding="utf-8")
        return path

    def test_union_adds_registry_only_projects(self):
        legacy = self.write_projects_config(
            """
            version: 1
            projects:
              manual-proj:
                remotes:
                  - github.com/acme/manual
            """
        )
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", remotes=("github.com/acme/widget",)),)
        )
        config = load_union_projects_config(legacy, registry_path)
        self.assertEqual([project.slug for project in config.projects], ["manual-proj", "widget"])

    def test_union_merges_same_slug_manual_first(self):
        legacy = self.write_projects_config(
            """
            version: 1
            projects:
              widget:
                roots:
                  - /data/manual-root
            """
        )
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", roots=("/data/discovered-root",), remotes=("github.com/acme/widget",)),)
        )
        config = load_union_projects_config(legacy, registry_path)
        self.assertEqual(len(config.projects), 1)
        self.assertEqual(config.projects[0].roots, ("/data/manual-root", "/data/discovered-root"))
        self.assertEqual(config.projects[0].remotes, ("github.com/acme/widget",))

    def test_alias_collision_manual_wins_with_warning(self):
        legacy = self.write_projects_config(
            """
            version: 1
            projects:
              manual-proj:
                aliases: [shared]
            """
        )
        registry_path = self.write_registry(
            (ProjectConfig(slug="generated-proj", aliases=("shared",)),)
        )
        with self.assertLogs("paulsha_hippo.importer", level="WARNING") as captured:
            config = load_union_projects_config(legacy, registry_path)
        self.assertEqual(config.aliases["shared"], "manual-proj")
        self.assertIn("shared", "\n".join(captured.output))

    def test_missing_registry_keeps_legacy_behavior(self):
        legacy = self.write_projects_config(
            """
            version: 1
            projects:
              paulshaclaw:
                remotes:
                  - github.com/hamanpaul/paulshaclaw
            """
        )
        config = load_union_projects_config(legacy, self.root / "absent" / "project-hippo.yaml")
        self.assertEqual([project.slug for project in config.projects], ["paulshaclaw"])

    def test_resolve_project_reads_registry_remote_by_default_load(self):
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", remotes=("github.com/acme/widget",)),)
        )
        project = resolve_project(
            cwd="/unmatched/path",
            git_toplevel="/another/unmatched/path",
            remote_url="git@github.com:acme/widget.git",
            config_path=str(self.root / "absent-projects.yaml"),
            registry_path=str(registry_path),
        )
        self.assertEqual(project, "widget")

    def test_resolve_project_reads_registry_roots_by_default_load(self):
        registry_path = self.write_registry(
            (ProjectConfig(slug="widget", roots=("/data/widget",)),)
        )
        project = resolve_project(
            cwd="/data/widget/src/module",
            config_path=str(self.root / "absent-projects.yaml"),
            registry_path=str(registry_path),
        )
        self.assertEqual(project, "widget")
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_project_resolver.py -v`
Expected: collection error `ImportError: cannot import name 'load_union_projects_config' from 'paulsha_hippo.importer.registry'`

- [ ] **Step 3: 實作**

(a) `paulsha_hippo/importer/registry.py` config import Edit：

old_string:
```python
from .config import ProjectConfig, _inline_list, _trimmed_lines
```

new_string:
```python
from .config import (
    ProjectConfig,
    ProjectsConfig,
    _inline_list,
    _trimmed_lines,
    load_projects_config,
)
```

(b) `paulsha_hippo/importer/registry.py` 檔尾追加：

```python
def load_union_projects_config(
    legacy_path: str | Path | None,
    registry_path: str | Path | None,
) -> ProjectsConfig:
    """Union-read：legacy projects.yaml（manual）∪ project-hippo.yaml（generated）。

    Manual 條目在前；同 slug 併 roots/remotes/aliases（manual 值序在前）；
    alias 衝突 manual 優先並記 warning。legacy 檔不搬移不改寫（非破壞過渡）。
    """
    legacy = load_projects_config(legacy_path)
    discovered = load_registry(registry_path)
    if not discovered:
        return legacy
    merged: list[ProjectConfig] = []
    index_by_slug: dict[str, int] = {}
    for project in legacy.projects:
        index_by_slug[project.slug] = len(merged)
        merged.append(project)
    for project in discovered:
        index = index_by_slug.get(project.slug)
        if index is None:
            index_by_slug[project.slug] = len(merged)
            merged.append(project)
            continue
        base = merged[index]
        merged[index] = ProjectConfig(
            slug=base.slug,
            roots=base.roots + tuple(item for item in project.roots if item not in base.roots),
            remotes=base.remotes
            + tuple(item for item in project.remotes if item not in base.remotes),
            aliases=base.aliases
            + tuple(item for item in project.aliases if item not in base.aliases),
        )
    aliases: dict[str, str] = {}
    for project in merged:
        for alias in project.aliases:
            if alias in aliases:
                if aliases[alias] != project.slug:
                    LOGGER.warning(
                        "alias collision for %s: keeping %s, ignoring %s",
                        alias,
                        aliases[alias],
                        project.slug,
                    )
                continue
            aliases[alias] = project.slug
    return ProjectsConfig(projects=tuple(merged), aliases=aliases)
```

(c) `paulsha_hippo/importer/project_resolver.py` import Edit：

old_string:
```python
from . import _git
from .config import ProjectsConfig, default_projects_path, load_projects_config
```

new_string:
```python
from . import _git
from .config import ProjectsConfig, default_projects_path
from .registry import default_registry_path, load_union_projects_config
```

(d) `resolve_project` 簽名與預設載入 Edit：

old_string:
```python
def resolve_project(
    *,
    cwd: str | None = None,
    git_toplevel: str | None = None,
    remote_url: str | None = None,
    projects: ProjectsConfig | None = None,
    config_path: str | None = None,
    memory_root: str | None = None,
) -> str:
    loaded_projects = projects or load_projects_config(config_path or default_projects_path(memory_root))
```

new_string:
```python
def resolve_project(
    *,
    cwd: str | None = None,
    git_toplevel: str | None = None,
    remote_url: str | None = None,
    projects: ProjectsConfig | None = None,
    config_path: str | None = None,
    memory_root: str | None = None,
    registry_path: str | None = None,
) -> str:
    loaded_projects = projects
    if loaded_projects is None:
        # union-read（#14 過渡）：legacy projects.yaml ∪ generated project-hippo.yaml
        loaded_projects = load_union_projects_config(
            config_path or default_projects_path(memory_root),
            registry_path or default_registry_path(memory_root),
        )
```

- [ ] **Step 4: 跑測試確認 PASS（含既有 resolver 迴歸）**

Run: `python3 -m pytest tests/test_project_resolver.py tests/test_project_registry.py -v`
Expected: 全 PASS（`ProjectResolverTest`/`ResolveAutoDetectTests` 既有 26 個 + `UnionReadTests` 6 個 + registry 25 個）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/registry.py paulsha_hippo/importer/project_resolver.py tests/test_project_resolver.py
git commit -m "feat(importer): 讀取端 union-read legacy projects.yaml 與 project-hippo.yaml（#14）"
```

---

### Task 8: Pipeline 整合 —— ingest 成功後 opt-in 回寫 discovery

**Files:**
- Modify: `paulsha_hippo/importer/pipeline.py:1-28`（imports + LOGGER）、`:263-266` 之後（新 helper）、`:326-347`（preview 內 discovery）、`:373-388`（ingest 內 pop + 回寫）
- Create: `tests/test_registry_autowrite.py`

**Interfaces:**
- Consumes: Task 2 `_git.git_main_toplevel`、Task 4/5 `registry.record_discovery`、Task 6 `registry.auto_write_enabled`、Task 3 `registry.default_registry_path`、既有 `normalize_remote`
- Produces:
  - decision 新增 key：`decision["discovery"] = {"slug": str, "roots": list[str], "remotes": list[str]}`（preview/dry-run 亦回報；**ledger entry 不含此 key**——寫 ledger 前 pop）
  - `pipeline._record_registry_discovery(memory_root: Path, discovery: dict[str, Any] | None) -> None`（module-private；fail-open）
  - 行為：非 dry-run、非 skip 的 terminal statuses（written/updated/hash-duplicate/stale-skip）都會嘗試回寫；`slug == "_unknown"` 或 roots+remotes 全空不寫；`auto_write_enabled()` False 不寫

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_registry_autowrite.py`：

```python
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paulsha_hippo.importer.pipeline import ingest_queue_item
from paulsha_hippo.importer.registry import parse_registry


REPO_ROOT = Path(__file__).resolve().parents[1]


class RegistryAutoWriteTest(unittest.TestCase):
    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.base = Path(self.tmp.name)
        self.memory_root = self.base / "agents" / "memory"
        self.queue = self.memory_root / "runtime" / "queue"
        self.queue.mkdir(parents=True)
        self.hippo_config = self.base / "hippo-config"
        self.hippo_config.mkdir()
        self.env = mock.patch.dict(
            os.environ, {"HIPPO_CONFIG_ROOT": str(self.hippo_config)}, clear=False
        )
        self.env.start()
        os.environ.pop("PSC_CONFIG_ROOT", None)
        # project_registry_path(memory_root) = <memory_root 上一層>/config/paulsha/project-hippo.yaml
        self.registry_path = self.base / "agents" / "config" / "paulsha" / "project-hippo.yaml"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def enable_auto_write(self):
        (self.hippo_config / "config.yaml").write_text(
            "project_registry:\n  auto_write: true\n", encoding="utf-8"
        )

    def make_repo(self, name="widget", remote="git@github.com:acme/widget.git"):
        repo = self.base / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        if remote:
            subprocess.run(
                ["git", "-C", str(repo), "remote", "add", "origin", remote], check=True
            )
        return repo

    def payload(self, *, cwd, session_id="registry-sid-001", remote_url=None):
        data = {
            "tool": "copilot-cli",
            "session_id": session_id,
            "capture_scope": "session_end",
            "ended_at": "2026-07-10T10:00:00+00:00",
            "cwd": str(cwd),
            "repo": "",
            "commit": "",
            "turn_count": 2,
            "user_prompts": ["implement registry"],
            "assistant_summary": "summary",
            "touched_files": ["src/registry.py"],
            "referenced_artifacts": [],
        }
        if remote_url is not None:
            data["remote_url"] = remote_url
        return data

    def ingest(self, payload, name="item.json", **kwargs):
        path = self.queue / name
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return ingest_queue_item(path, memory_root=self.memory_root, **kwargs)

    def read_projects(self):
        return parse_registry(self.registry_path.read_text(encoding="utf-8"))

    def test_auto_write_default_off(self):
        repo = self.make_repo(name="offrepo", remote="git@github.com:acme/offrepo.git")
        decision = self.ingest(self.payload(cwd=repo, session_id="registry-sid-off"))
        self.assertEqual(decision["status"], "written")
        self.assertIn("discovery", decision)
        self.assertFalse(self.registry_path.exists())

    def test_auto_write_records_discovery_and_is_idempotent(self):
        self.enable_auto_write()
        repo = self.make_repo()
        decision = self.ingest(self.payload(cwd=repo), name="a.json")
        self.assertEqual(decision["status"], "written")
        projects = self.read_projects()
        self.assertEqual([project.slug for project in projects], ["github.com/acme/widget"])
        self.assertEqual(projects[0].remotes, ("github.com/acme/widget",))
        self.assertEqual(len(projects[0].roots), 1)
        self.assertEqual(Path(projects[0].roots[0]).resolve(), repo.resolve())
        before = self.registry_path.read_bytes()
        second = self.ingest(self.payload(cwd=repo), name="b.json")
        self.assertEqual(second["status"], "hash-duplicate")
        self.assertEqual(self.registry_path.read_bytes(), before)
        ledger = self.memory_root / "runtime" / "ledger" / "import.jsonl"
        for line in ledger.read_text(encoding="utf-8").splitlines():
            self.assertNotIn('"discovery"', line)

    def test_multi_remote_normalizes_and_dedupes(self):
        self.enable_auto_write()
        repo = self.make_repo()
        self.ingest(
            self.payload(
                cwd=repo,
                session_id="registry-sid-multi",
                remote_url="https://x-access-token@github.com/ACME/widget.git",
            )
        )
        projects = self.read_projects()
        self.assertEqual(projects[0].remotes, ("github.com/acme/widget",))

    def test_worktree_cwd_registers_main_root(self):
        self.enable_auto_write()
        repo = self.make_repo(name="mainrepo", remote="git@github.com:acme/mainrepo.git")
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
             "commit", "--allow-empty", "-m", "init"],
            check=True, capture_output=True,
        )
        worktree = self.base / "wt"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "wt-branch", str(worktree)],
            check=True, capture_output=True,
        )
        self.ingest(self.payload(cwd=worktree, session_id="registry-sid-wt"))
        projects = self.read_projects()
        self.assertEqual(len(projects[0].roots), 1)
        self.assertEqual(Path(projects[0].roots[0]).resolve(), repo.resolve())

    def test_registry_failure_does_not_break_ingest(self):
        self.enable_auto_write()
        repo = self.make_repo(name="failrepo", remote="git@github.com:acme/failrepo.git")
        with mock.patch(
            "paulsha_hippo.importer.registry.record_discovery",
            side_effect=OSError("disk full"),
        ):
            with self.assertLogs("paulsha_hippo.importer", level="WARNING") as captured:
                decision = self.ingest(self.payload(cwd=repo, session_id="registry-sid-fail"))
        self.assertEqual(decision["status"], "written")
        self.assertTrue(Path(decision["inbox_path"]).exists())
        self.assertIn("fail-open", "\n".join(captured.output))

    def test_non_repo_cwd_without_remote_not_recorded(self):
        self.enable_auto_write()
        folder = self.base / "plain-folder"
        folder.mkdir()
        decision = self.ingest(self.payload(cwd=folder, session_id="registry-sid-plain"))
        self.assertEqual(decision["status"], "written")
        self.assertFalse(self.registry_path.exists())

    def test_dry_run_does_not_write_registry(self):
        self.enable_auto_write()
        repo = self.make_repo(name="dryrepo", remote="git@github.com:acme/dryrepo.git")
        decision = self.ingest(
            self.payload(cwd=repo, session_id="registry-sid-dry"), name="dry.json", dry_run=True
        )
        self.assertIn("discovery", decision)
        self.assertFalse(self.registry_path.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_registry_autowrite.py -v`
Expected: 7 個測試 FAIL（`KeyError`/`AssertionError`——decision 無 `discovery`、registry 檔不存在）

- [ ] **Step 3: 實作 pipeline 變更（5 個 Edit + 1 個追加）**

(a) `paulsha_hippo/importer/pipeline.py` stdlib import Edit：

old_string:
```python
import fcntl
import json
import re
import shutil
import threading
```

new_string:
```python
import fcntl
import json
import logging
import re
import shutil
import threading
```

(b) 套件內 import Edit：

old_string:
```python
from .adapters import claude, codex, copilot
from .adapters.base import AdapterResult, NormalizedSession
from . import _git
from . import title
```

new_string:
```python
from .adapters import claude, codex, copilot
from .adapters.base import AdapterResult, NormalizedSession
from . import _git
from . import registry
from . import title
```

(c) LOGGER Edit：

old_string:
```python
_LEDGER_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LEDGER_THREAD_LOCKS_GUARD = threading.Lock()
```

new_string:
```python
_LEDGER_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LEDGER_THREAD_LOCKS_GUARD = threading.Lock()

LOGGER = logging.getLogger("paulsha_hippo.importer")
```

(d) 於 `_persisted_session` 之後追加 helper：

old_string:
```python
def _persisted_session(session: NormalizedSession, *, raw_payload_pointer: str) -> NormalizedSession:
    persisted: NormalizedSession = dict(session)
    persisted["raw_payload_pointer"] = raw_payload_pointer
    return persisted
```

new_string:
```python
def _persisted_session(session: NormalizedSession, *, raw_payload_pointer: str) -> NormalizedSession:
    persisted: NormalizedSession = dict(session)
    persisted["raw_payload_pointer"] = raw_payload_pointer
    return persisted


def _record_registry_discovery(memory_root: Path, discovery: dict[str, Any] | None) -> None:
    """Opt-in（project_registry.auto_write）時把已解析的 project mapping 寫回 registry（#14）。

    Fail-open：registry 寫入失敗不得影響 ingest 主流程，僅記 warning。
    slug 為 _unknown 或 roots+remotes 全空（非 repo、無 remote 的雜訊 session）不寫。
    """
    if not discovery:
        return
    slug = discovery.get("slug")
    roots = [item for item in discovery.get("roots", []) if item]
    remotes = [item for item in discovery.get("remotes", []) if item]
    if not slug or slug == "_unknown":
        return
    if not roots and not remotes:
        return
    if not registry.auto_write_enabled():
        return
    try:
        registry.record_discovery(
            slug=slug,
            roots=roots,
            remotes=remotes,
            registry_path=registry.default_registry_path(memory_root),
        )
    except (OSError, ValueError) as exc:
        LOGGER.warning("project registry auto-write failed (fail-open): %s", exc)
```

(e) preview 內 discovery 計算 Edit（`_preview_queue_item_unlocked`）：

old_string:
```python
    archive_path = _archive_path(root, month, key, status, incoming_hash)
    rendered_session = _persisted_session(session, raw_payload_pointer=str(archive_path))
    provenance_repo = normalize_remote(_git.git_remote(_git.git_toplevel(session.get("cwd")))) or "_unknown"
```

new_string:
```python
    archive_path = _archive_path(root, month, key, status, incoming_hash)
    rendered_session = _persisted_session(session, raw_payload_pointer=str(archive_path))
    discovered_toplevel = _git.git_toplevel(session.get("cwd"))
    discovered_remote = normalize_remote(_git.git_remote(discovered_toplevel))
    provenance_repo = discovered_remote or "_unknown"
    main_root = _git.git_main_toplevel(discovered_toplevel)
    payload_remote = normalize_remote(remote_url)
```

(f) decision 附掛 discovery Edit：

old_string:
```python
    decision["classifier_bucket"] = bucket
    decision["project"] = project
    decision["rendered"] = render_markdown(
```

new_string:
```python
    decision["classifier_bucket"] = bucket
    decision["project"] = project
    decision["discovery"] = {
        "slug": project,
        "roots": [main_root] if main_root else [],
        "remotes": sorted({value for value in (payload_remote, discovered_remote) if value}),
    }
    decision["rendered"] = render_markdown(
```

(g) ingest 內 pop + 回寫 Edit（`ingest_queue_item`）：

old_string:
```python
            with _locked_ledger(root):
                decision = _preview_queue_item_unlocked(queue_path, memory_root=root)
                rendered = decision.pop("rendered")
                inbox_path = Path(decision["inbox_path"])
```

new_string:
```python
            with _locked_ledger(root):
                decision = _preview_queue_item_unlocked(queue_path, memory_root=root)
                rendered = decision.pop("rendered")
                discovery = decision.pop("discovery", None)
                inbox_path = Path(decision["inbox_path"])
```

(h) 回寫 + 還原 decision Edit：

old_string:
```python
                elif decision["status"] in _TERMINAL_STATUSES:
                    _archive_queue(queue_path, archive_path)
                    _append_ledger(root, decision)
                    _remove_queue(queue_path)
            return decision
```

new_string:
```python
                elif decision["status"] in _TERMINAL_STATUSES:
                    _archive_queue(queue_path, archive_path)
                    _append_ledger(root, decision)
                    _remove_queue(queue_path)
            _record_registry_discovery(root, discovery)
            if discovery is not None:
                decision["discovery"] = discovery
            return decision
```

（設計註記：discovery 在寫 ledger **前** pop——ledger schema 零變動、零迴歸風險；回傳前還原以便呼叫端／dry-run 觀察。registry 寫入位於 per-key lock 內、ledger lock 外，且自帶固定名 flock。）

- [ ] **Step 4: 跑測試確認 PASS（含 importer 全迴歸）**

Run: `python3 -m pytest tests/test_registry_autowrite.py tests/test_idempotency.py tests/test_importer_cli.py tests/test_self_and_empty_capture.py tests/test_importer_scope_rank.py -v`
Expected: 全 PASS（decision 新 key 為 additive，既有測試不斷言 key 全集）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/pipeline.py tests/test_registry_autowrite.py
git commit -m "feat(importer): ingest 管線 opt-in 回寫 discovery 至 project registry（#14）"
```

---

### Task 9: 契約文件 + producer contract test（逐 byte）+ README 同步

**Files:**
- Create: `docs/project-registry-contract.md`
- Create: `tests/fixtures/registry/project-hippo.expected.yaml`
- Modify: `tests/test_project_registry.py`（檔尾追加 class）
- Modify: `README.md`（Usage 段「設定：」行之後補一行——原始 main 快照 line 29，一律以行內容定位；跨批次錨區，rebase 後不得整行覆蓋，見 Step 6 合併規則）

**Interfaces:**
- Consumes: Task 4/5 `record_discovery`（producer 真路徑）
- Produces:
  - 檔案契約文件 `docs/project-registry-contract.md`（schema_version 1；path/schema/正規化/determinism/寫入協定/merge 語義/版本演進）
  - fixture `tests/fixtures/registry/project-hippo.expected.yaml`（canonical bytes 錨點）
  - 不變量：`record_discovery` 固定輸入輸出 == fixture bytes == 契約文件 canonical example block（三方逐 byte 一致）

- [ ] **Step 1: 寫失敗測試**

`tests/test_project_registry.py` 檔尾追加：

```python
class ProducerContractTests(_ScratchDirTestCase):
    FIXTURE = REPO_ROOT / "tests" / "fixtures" / "registry" / "project-hippo.expected.yaml"
    CONTRACT_DOC = REPO_ROOT / "docs" / "project-registry-contract.md"
    MARKER = "<!-- contract-fixture:tests/fixtures/registry/project-hippo.expected.yaml -->"

    def test_producer_output_matches_fixture_byte_for_byte(self):
        path = self.root / "paulsha" / "project-hippo.yaml"
        record_discovery(
            slug="github.com/acme/widget",
            roots=("/home/user/projects/widget",),
            remotes=("github.com/acme/widget",),
            registry_path=path,
        )
        record_discovery(
            slug="scratch-notes",
            roots=("/home/user/scratch/notes",),
            remotes=(),
            registry_path=path,
        )
        self.assertEqual(path.read_bytes(), self.FIXTURE.read_bytes())

    def test_contract_doc_canonical_example_matches_fixture(self):
        doc = self.CONTRACT_DOC.read_text(encoding="utf-8")
        self.assertIn(self.MARKER, doc)
        after = doc.split(self.MARKER, 1)[1]
        self.assertTrue(after.lstrip().startswith("```yaml"))
        block = after.split("```yaml\n", 1)[1].split("```", 1)[0]
        self.assertEqual(block, self.FIXTURE.read_text(encoding="utf-8"))
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_project_registry.py::ProducerContractTests -v`
Expected: 2 個 FAIL（`FileNotFoundError`：fixture 與契約文件不存在）

- [ ] **Step 3: 建 fixture**

建立 `tests/fixtures/registry/project-hippo.expected.yaml`，內容**逐 byte** 如下（UTF-8、LF、檔尾一個換行）：

```yaml
# GENERATED — 本檔由 paulsha-hippo 自動產生（project registry discovery record），請勿手改。
# 使用者 override 請寫 manual 檔（projects.yaml / project-cortex.yaml），詳見契約文件。
# contract: docs/project-registry-contract.md
schema_version: 1
projects:
  - slug: github.com/acme/widget
    roots:
      - /home/user/projects/widget
    remotes:
      - github.com/acme/widget
    aliases: []
  - slug: scratch-notes
    roots:
      - /home/user/scratch/notes
    remotes: []
    aliases: []
```

- [ ] **Step 4: 寫契約文件**

建立 `docs/project-registry-contract.md`，完整內容（下方以四個反引號為外層圍欄，內層 `yaml` 圍欄屬於文件內容本身）：

````markdown
# Project Registry 檔案契約（project-hippo.yaml）

> **schema_version: 1**（本文件對應）。producer：paulsha-hippo（本 repo）；consumer：cortex（paulshaclaw）等——**檔案契約、零 code 依賴**（雙方各自 parse，不共享程式）。
> 來源：issue #14；spec `docs/superpowers/specs/2026-07-10-all-issues-resolution-design.md` §3.3。

## 1. 路徑契約

- 預設落點：`~/.agents/config/paulsha/project-hippo.yaml`（與 cortex 手寫檔 `project-cortex.yaml` 同層；hippo 不產生、不讀取 `project-cortex.yaml` 內容，僅共享目錄）。
- 程式定位：`paulsha_hippo.paths.project_registry_path(memory_root_value=None)`，優先序與 `projects.yaml`（`projects_config_path`）同構：
  1. `PSC_CONFIG_ROOT` 已設 → `<基底>/.agents/config/paulsha/project-hippo.yaml`（`PSC_CONFIG_ROOT` 形如 `<HOME>/.config/paulshaclaw` 時基底取其上兩層，否則取其本身）。
  2. 呼叫端帶 memory_root → `<memory_root 上一層>/config/paulsha/project-hippo.yaml`。
  3. 否則 `<agents_root>/config/paulsha/project-hippo.yaml`（`agents_root` 預設 `~/.agents`，可由 `HIPPO_AGENTS_ROOT` / `PSC_AGENTS_ROOT` 覆寫）。
- 同目錄固定名輔助檔：lock `.project-hippo.yaml.lock`、暫存 `.project-hippo.yaml.tmp`（consumer 應忽略）。

## 2. Schema（v1）

YAML 子集。producer 只輸出下列結構；consumer 建議寬鬆解析（忽略未知欄位）。

- 檔頭：固定 3 行 `#` 註解（generated 宣告、override 指引、本文件路徑）。
- `schema_version`：int，必填，目前 `1`。
- `projects`：list（空集輸出 inline `projects: []`）。每項：
  - `slug`：str，必填——importer `resolve_project` 產出的 project 識別。
  - `roots`：list[str]——絕對路徑；**linked worktree 一律歸併為主 repo root**（`git rev-parse --git-common-dir`）。
  - `remotes`：list[str]——正規化 remote 識別（見 §3）。
  - `aliases`：list[str]——v1 producer 恆輸出 `[]`（hippo 無 alias 發現來源；欄位保留前向相容），inline 形式。
- 空 list 輸出 inline `[]`；非空輸出 block list（`      - item`）。

## 3. Remote 正規化（去 credential、統一 scheme）

與 `paulsha_hippo.importer.project_resolver.normalize_remote` 同一實作：

- 去 credential：`https://token@github.com/...` → host 起首；scp 形 `user@host:...` 剝除 `user@`。
- 去 scheme：統一為無 scheme 的 `host/owner/repo` 識別形。
- host 小寫；`github.com` 之 owner/repo 一併小寫；非預設 port 保留為 `host:port`（ssh github 22 除外）。
- 去尾 `.git`（不分大小寫）、去尾 `/`。
- `owner/repo` 短形補 `github.com/` 前綴。

## 4. Determinism（byte-level 規則）

同一組輸入必產生逐 byte 相同輸出（producer contract test 錨定，見 §8）：

- 編碼 UTF-8、換行 LF、檔尾恰一個換行。
- `projects` 依 `slug` 字典序排序；`roots`/`remotes`/`aliases` 各自去重後字典序排序。
- 縮排固定：list 項 `  - slug: ...`、欄位 4 空格、子項 `      - `；值不加引號。

## 5. 寫入協定（producer 側）

- **Opt-in**：`~/.config/paulsha-hippo/config.yaml` 設 `project_registry.auto_write: true` 才寫（預設 off）。
- 觸發點：importer ingest 完成（dry-run 不寫）；`slug` 為 `_unknown`、或 roots 與 remotes 全空的 session 不寫。
- 互斥：固定名 lock `flock(LOCK_EX)`；原子性：寫 `.project-hippo.yaml.tmp` 後 `os.replace`；內容未變則跳寫（冪等）。
- Fail-open：registry 寫入失敗不影響 ingest 主流程（記 warning）。
- **分權**：generated 檔不允許手改（檔頭註明；手改內容會在下次寫入被 canonical 化覆蓋）。使用者 override 一律放 manual 檔——hippo 側 legacy `projects.yaml`，或 cortex 側 `project-cortex.yaml`。

## 6. 讀取端 merge 語義

- **hippo 讀取端**（`resolve_project` 預設載入）union-read：legacy `projects.yaml` ∪ `project-hippo.yaml`。同 slug 併 roots/remotes/aliases（manual 條目與值序在前）；alias 衝突 manual 優先（記 warning）。legacy `projects.yaml` 不搬移、不改寫（非破壞過渡）。
- **cortex 讀取端**：`project-cortex.yaml`（curated intent）∪ `project-hippo.yaml`（discovered activity），union 去重＝真正監控集——cortex 側行為不在本 repo 範圍，本文件僅保證檔案契約。
- Consumer 解析建議：忽略未知欄位；`schema_version` 大於已知版本時 best-effort 讀 v1 欄位。

## 7. 版本演進

- 破壞性變更（欄位改名／語義改變／格式改變）→ `schema_version` +1，並同步更新本文件與 producer contract test fixture。
- 純新增欄位 → 不 bump（consumer 忽略未知欄位）。

## 8. Canonical example（producer contract test 錨點）

下列範例由 `tests/test_project_registry.py::ProducerContractTests` 以固定輸入驅動真實 producer（`record_discovery`）產出，與 `tests/fixtures/registry/project-hippo.expected.yaml` 及本 block **逐 byte 比對**——三方不一致即測試 FAIL。

<!-- contract-fixture:tests/fixtures/registry/project-hippo.expected.yaml -->
```yaml
# GENERATED — 本檔由 paulsha-hippo 自動產生（project registry discovery record），請勿手改。
# 使用者 override 請寫 manual 檔（projects.yaml / project-cortex.yaml），詳見契約文件。
# contract: docs/project-registry-contract.md
schema_version: 1
projects:
  - slug: github.com/acme/widget
    roots:
      - /home/user/projects/widget
    remotes:
      - github.com/acme/widget
    aliases: []
  - slug: scratch-notes
    roots:
      - /home/user/scratch/notes
    remotes: []
    aliases: []
```
````

（注意：契約文件內 canonical example 的閉合圍欄（三個反引號）必須緊接 `    aliases: []` 的下一行——fenced block 內容需與 fixture 逐 byte 相等，含尾換行。）

- [ ] **Step 5: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_project_registry.py -v`
Expected: `27 passed`

- [ ] **Step 6: README 同步（R-18）**

`README.md`（Usage 段「設定：」行；行號為原始 main 快照，一律以行內容定位）Edit：

old_string:
```markdown
設定：單一檔 `~/.config/paulsha-hippo/config.yaml` + `HIPPO_*` env 覆寫；密鑰一律 `secret.env`（0600）。
```

new_string:
```markdown
設定：單一檔 `~/.config/paulsha-hippo/config.yaml` + `HIPPO_*` env 覆寫；密鑰一律 `secret.env`（0600）。
Project registry：設 `project_registry.auto_write: true`（預設 off）後，importer 自動把已解析的 project mapping 寫入 generated 檔 `~/.agents/config/paulsha/project-hippo.yaml`（勿手改；讀取端自動 union-read legacy `projects.yaml`）。契約見 `docs/project-registry-contract.md`。
```

**合併規則（README 跨批次錨區；與 PR-B Task 6 Step 7 等 sibling README 步驟同一條規則）**：若「設定：」錨行已被 sibling 批次改寫（rebase 後與引用的 old 內容不符），不得整行覆蓋——以當前行內容為基底，僅追加本批新增片段，保留 sibling 已 merge 的全部新增。本批新增片段只有一個：在「設定：…」行（以當前實際內容為準）之後插入整行「Project registry：…」補充行；「設定：」行本身不改寫，sibling 已 merge 的其他行（日常命令行的命令擴充及其後續補充行等）一律原樣保留，不得覆蓋或刪除。

- [ ] **Step 7: 機敏掃描自查（R-21）+ Commit**

Run: `grep -rn "/home/$(whoami)" docs/project-registry-contract.md tests/fixtures/registry/ README.md; echo exit=$?`
Expected: 無匹配輸出、`exit=1`（新檔案不含個人絕對路徑）

```bash
git add docs/project-registry-contract.md tests/fixtures/registry/project-hippo.expected.yaml tests/test_project_registry.py README.md
git commit -m "docs: project registry 契約文件 + producer contract test 逐 byte 錨定（#14）"
```

---

### Task 10: changelog.d 碎片 + CHANGELOG `[Unreleased]` + 全套驗證

**Files:**
- Create: `changelog.d/feature-14-project-registry.md`
- Modify: `CHANGELOG.md`（`[Unreleased]` 段——R-09 以此檔為準，policy_check 的 `_unreleased_has_bullet_entry` 只檢查 `## [Unreleased]` 下有 bullet，與 changelog.d 完全無關）
- Test: 全套 `tests/` + `python3 -m policy_check --repo .`

**Interfaces:**
- Consumes: Task 1–9 全部產出
- Produces: R-09 CHANGELOG `[Unreleased]` entry＋changelog.d 碎片（repo 慣例兩者並存——碎片供 release 彙整，`[Unreleased]` 供 R-09 gate）；PR-E 綠燈證據

- [ ] **Step 1: 新增 changelog.d 碎片（格式沿 `changelog.d/fix-dream-service-interpreter.md` 現行慣例）**

建立 `changelog.d/feature-14-project-registry.md`：

```markdown
### Added
- Project registry（#14）：importer ingest 後將已解析的 project mapping（slug/roots/remotes）寫入 generated 檔 `paulsha/project-hippo.yaml`——schema_version 1、deterministic 輸出（排序去重、逐 byte 可重現）、remote 正規化去 credential、worktree 歸併主 repo root、temp+atomic replace+固定名 lock、opt-in `project_registry.auto_write`（預設 off）、fail-open；讀取端（resolve_project 預設載入）union-read legacy `projects.yaml` 與新檔（非破壞過渡）；檔案契約文件 `docs/project-registry-contract.md` 由 producer contract test 逐 byte 錨定。
```

- [ ] **Step 2: 更新 `CHANGELOG.md` `[Unreleased]` 段（R-09 gate 以此檔為準；比照 PR-A Task 12 Step 2）**

把 Step 1 碎片同內容的 bullet 以 `### Added` 標題併入 `CHANGELOG.md` 的 `## [Unreleased]`——插入既有 `### Fixed` 段之後、`## [0.1.0]` 之前：

```markdown
### Added
- Project registry（#14）：importer ingest 後將已解析的 project mapping（slug/roots/remotes）寫入 generated 檔 `paulsha/project-hippo.yaml`——schema_version 1、deterministic 輸出（排序去重、逐 byte 可重現）、remote 正規化去 credential、worktree 歸併主 repo root、temp+atomic replace+固定名 lock、opt-in `project_registry.auto_write`（預設 off）、fail-open；讀取端（resolve_project 預設載入）union-read legacy `projects.yaml` 與新檔（非破壞過渡）；檔案契約文件 `docs/project-registry-contract.md` 由 producer contract test 逐 byte 錨定。
```

（rebase 後若 `[Unreleased]` 已有其他批次的相同 `### Added` 標題，把 bullet 併入既有標題下，不重複標題——R-04 格式。R-09 的 `_unreleased_has_bullet_entry` 只認 `CHANGELOG.md`，changelog.d 碎片本身**不**滿足 R-09。）

- [ ] **Step 3: 全套測試**

Run: `python3 -m pytest -q`
Expected: 全綠（`0 failed`；總數 = 既有 953+ 本 plan 新增約 40）

- [ ] **Step 4: policy check**

Run: `python3 -m policy_check --repo .`
Expected: 結尾各規則全 pass、零 failure（R-09 由 Step 2 的 `[Unreleased]` bullet 滿足；碎片供 release 彙整，不滿足 R-09；R-18 由 Task 9 README 行滿足；R-21 掃描 pass）

- [ ] **Step 5: Commit**

```bash
git add changelog.d/feature-14-project-registry.md CHANGELOG.md
git commit -m "chore: changelog.d 碎片 + CHANGELOG [Unreleased]——PR-E project registry（#14）"
```

- [ ] **Step 6: 交回 workflow**

本 plan 到此為止——開 PR（title `feat(importer): project registry——generated project-hippo.yaml + union-read（#14）`、body 含 `Closes #14` + checklist 全勾、zh-tw）、rebase 重驗、merge 由 workflow 主編排執行（spec §6），不在本 plan 步驟內。

---

## 跨批次介面對照（spec §6 + workflow 共享契約自查）

- 本 plan **不動**：`ops.py`、`ledger/processing.py`、`backends.py`、`moc/**`、`cli.py`、atomizer、hooks——與 PR-A/B/C/D/F 零撞檔。
- 本 plan **不消費**任何其他批次的新介面（PR-E 零相依，可立即並行開工）。
- 本 plan 對外提供（PR-F 及 cortex 側可依賴）：
  - 檔案契約：`<config 根>/paulsha/project-hippo.yaml`（schema_version 1，`docs/project-registry-contract.md`）
  - `paths.project_registry_path(memory_root_value: str | Path | None = None) -> Path`
  - `importer._git.git_main_toplevel(toplevel: str | Path | None) -> Optional[str]`
  - `importer.registry`：`SCHEMA_VERSION`、`render_registry`、`parse_registry`、`registry_schema_version`、`load_registry`、`merge_discovery`、`record_discovery`、`auto_write_enabled`、`load_union_projects_config`、`default_registry_path`
  - `resolve_project(..., registry_path: str | None = None)`（向後相容擴充；hooks/wakeup/backfill/pipeline 既有呼叫自動獲得 union-read）

## Self-Review 紀錄（spec §3.3 覆蓋對照）

| spec §3.3 要求 | Task |
|---|---|
| 1. generated 檔、paths.py config 根約定、與 project-cortex.yaml 同層 | Task 1, 9 |
| 2. schema slug/roots/remotes/aliases + 正規化去 credential + 去重排序 deterministic | Task 3, 8 |
| 3. 分權：generated 不手改（檔頭註明）、override 走 manual 檔 | Task 3（header）, 9（§5） |
| 4. opt-in `project_registry.auto_write`（預設 off） | Task 6, 8 |
| 5. temp + atomic replace + 固定名 lock、stdlib-only | Task 4, 5 |
| 6. union-read legacy projects.yaml、非破壞搬移 | Task 7 |
| 7. 契約版本化（docs + schema_version）+ producer contract test 逐 byte | Task 9 |
| 驗收：crash recovery | Task 5 |
| 驗收：重複 discovery 冪等 | Task 4, 8 |
| 驗收：worktree 歸併主 repo root | Task 2, 8 |
| 驗收：多 remote 正規化去重 | Task 8 |
| 驗收：producer contract test 過 | Task 9 |

**Plan 拍板紀錄（spec 未明寫、本 plan 落定）：**
1. registry 目錄拍板 `<config 根>/paulsha/project-hippo.yaml`（spec「與 project-cortex.yaml 同層」＋ issue #14「傾向 `~/.agents/config/paulsha/`」）；cortex 側落點若不同，改 `paths.project_registry_path` 一處 + 契約文件 §1。
2. union-read 範圍限定 `resolve_project` 預設載入；`moc/search._project_roots` 與 atomizer `known_projects_file` 維持只讀 legacy（manual curation 面 + 避開 PR-A/B 地盤）。
3. discovery 回寫觸發涵蓋全部非 skip terminal statuses（含 hash-duplicate/stale-skip）——discovery 與 dedup 結果正交，取冪等最大資訊面。
4. issue #14 原文「保留使用者手改、註解保留」與 spec §3.3.3「generated 檔不允許手改」衝突——依 spec（較新拍板）：generated 檔機器所有、canonical 化輸出；手寫 override 走 manual 檔。
5. generated 檔 `aliases` 恆輸出 `[]`（hippo 無 alias 發現來源；欄位保留前向相容）。
