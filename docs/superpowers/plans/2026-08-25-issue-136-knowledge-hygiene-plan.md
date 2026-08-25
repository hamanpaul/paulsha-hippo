---
status: accepted
work_item: issue-136-knowledge-hygiene
---

# Knowledge hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落實 `docs/superpowers/specs/2026-08-25-issue-136-knowledge-hygiene-design.md` 的五個 fix（provenance.commit＋cites、同主題新鮮度／supersedes、`hippo show --agent`、episodic 層、follow-up ledger）與 1d janitor commit check；fix 6 留 later。

**Architecture:** 所有變更都掛在既有管線的既有落點上：SessionEnd hook（stdlib-only、複製部署）→ importer（`adapters/base.py` → `sanitizer` → `frontmatter.render_markdown`）→ atomizer（`_split_pass` → `build_from_proposal` → `_attach_unambiguous_supersedes` → `validate` → `_publish_session`）→ index／shortlist（`moc/search.py` → `hooks/_shortlist_common.py` → `retrieval.format_shortlist`）→ janitor／dream／wakeup。一次性 migration 全部仿 `paulsha_hippo/tags_migration.py`（scan → dry-run 報表 → `frontmatter_io.update()` apply → 冪等）。新 flag 由新模組 `paulsha_hippo/runtime_flags.py` 從 canonical `config.yaml`（`paths.atomizer_config_path()`）best-effort 讀取，缺鍵一律預設值，不需 config migration。

**Tech Stack:** Python 3.10+（stdlib、PyYAML 可選）、pytest、argparse CLI（`paulsha_hippo/cli.py`）、SQLite FTS5（既有 index）。

**Target branch:** `feature/136-knowledge-hygiene`（repo 禁止直接 commit `main`）。

**Issue:** [#136](https://github.com/hamanpaul/paulsha-hippo/issues/136)——PR body 寫 `Closes #136`（R-17）。

## Global Constraints

- 語言 zh-tw；TDD：每個 task 先寫失敗測試、跑到 RED，再實作到 GREEN，再 commit。
- 基準程式碼 HEAD `f674aef`（`main` 目前為 `b03db45`＝#135 merge，與 `f674aef` 內容零差異，2026-08-25 已逐一重驗本文行號）；動手前仍用 `grep -n` 重新定位。
- SessionEnd 截取 hooks（`claude_session_end.py`／`codex_session_end.py`／`copilot_session_end.py`）**必須維持 stdlib-only、複製部署可獨立執行**（`tests/test_hooks_selfcontained.py`）；任何 git 探測都 inline 在各 hook、best-effort、timeout 2s、失敗留空、exit 0。
- 新 hook 檔／新 sibling module 必須同步加進 `paulsha_hippo/hooks/install.sh` 第 206–211 行的複製清單（本 plan 不新增 hook 檔，只改既有三支）。
- `provenance` 子鍵一律字串：`repo / commit / path / commit_source / branch / dirty`；`dirty ∈ {"true","false"}`，`commit_source ∈ {"hook","import-discovery","backfill-approx"}`；缺省省略，舊讀者只取前三鍵仍可讀。
- 所有 migration 命令：`--dry-run`（預設）不寫任何檔；`--apply` 走 `frontmatter_io.update()`（parse-equivalent、body 逐位元不變）；apply→dry-run 回報 0→再 apply 為 no-op；只碰 `memory_layer == "knowledge"`（mark-episodic 的 revert 例外）。
- 新 config 鍵全部 optional 且有預設：`shortlist.collapse_same_topic: true`、`shortlist.read_hint: show`、`followups.enabled: true`、`episodic_filter: true`；同步寫進 `paulsha_hippo/atomizer/atomizer.yaml` 模板（`hippo init` 由它複製）。`atomizer/config.py::load_config` 只讀已知區段，未知頂層鍵不會 fail。
- janitor override 實際路徑是 `paths.config_path("janitor.override.yaml")` = `~/.config/paulshaclaw/janitor.override.yaml`（**不是** spec §10 寫的 `~/.config/paulsha-hippo/`）。
- 交付治理（AGENTS.md）：每個 task 群一個 `changelog.d/<slug>.md` 碎片；新 CLI 命令要同步 `README.md` 第 31 行「日常命令」清單（R-16）；`python3 -m pytest tests/ -q`、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。全套測試在非巢狀 sibling worktree 跑（巢狀下 `test_project_resolver` 2 個假失敗為已知）。
- commit 前 `rm -rf .psc_tmp`；不要 `git add -A`。
- 不碰 `~/.agents/memory`（live 記憶庫）；所有測試用 `tmp_path`。

## File Structure

| 檔案 | 責任 | 動作 |
|---|---|---|
| `paulsha_hippo/hooks/{claude,codex,copilot}_session_end.py` | 截取時 inline `_git_snapshot(cwd)` → payload `commit/git_branch/git_dirty` | Modify |
| `paulsha_hippo/importer/adapters/base.py` | `NormalizedSession` 加 `git_branch/git_dirty`；`build_session` 讀取 | Modify |
| `paulsha_hippo/importer/sanitizer.py` | `git_branch` 進字串 sanitize 白名單 | Modify |
| `paulsha_hippo/importer/_git.py` | 新 `git_head(toplevel)`、`git_rev_before(toplevel, iso_ts)`、`git_commit_exists(toplevel, sha)` | Modify |
| `paulsha_hippo/importer/frontmatter.py` | `render_markdown` 6 鍵 provenance | Modify |
| `paulsha_hippo/importer/pipeline.py` | commit fallback（import-discovery）＋ `commit_source` | Modify |
| `paulsha_hippo/atomizer/pipeline.py` | 3 處 provenance 3 鍵 → 6 鍵；episodic 降層；cross-session supersedes | Modify |
| `paulsha_hippo/atomizer/slice_frontmatter.py` | `render` 6 鍵；`cites`／`episodic_reason` 進 `_SCALAR_ORDER`；`validate` 放寬 layer；`extract_cites` | Modify |
| `paulsha_hippo/janitor/record_source.py`、`rules.py`、`scanner.py` | provenance 6 鍵；`check_provenance_commit` 語意 | Modify |
| `paulsha_hippo/runtime_flags.py` | 讀 config.yaml 的 hygiene flags，best-effort | Create |
| `paulsha_hippo/show.py` | `hippo show <ref> --agent` 渲染＋read 歸因 | Create |
| `paulsha_hippo/usage_read.py` | `append_read_event()`：與 post_tool_use hook 同 schema 的 read 事件 | Create |
| `paulsha_hippo/retrieval.py` | hint 模式 `read|show`、行尾附 slice_id | Modify |
| `paulsha_hippo/hooks/_shortlist_common.py` | 同主題折疊 keep-newest、show hint | Modify |
| `paulsha_hippo/hooks/_wakeup_common.py` | recall 指引改 show | Modify |
| `paulsha_hippo/topic.py` | `is_same_topic()`、`title_tokens()`、`collapse_same_topic()` | Create |
| `paulsha_hippo/importer/config.py` | `projects.yaml` 頂層 `families:` 解析 | Modify |
| `paulsha_hippo/moc/search.py` | `search()` 回傳加 `captured_at` | Modify |
| `paulsha_hippo/provenance_backfill.py` | `hippo knowledge backfill-provenance` | Create |
| `paulsha_hippo/supersedes_link.py` | `hippo knowledge link-supersedes` | Create |
| `paulsha_hippo/noise.py` | `episodic_reason()`（非 deletion-grade） | Modify |
| `paulsha_hippo/episodic_migration.py` | `hippo knowledge mark-episodic` | Create |
| `paulsha_hippo/moc/frontmatter_io.py` | `update(path, updates, *, remove=())` | Modify |
| `paulsha_hippo/atomizer/skills/atomize-knowledge-slice.md` | CONCEPT_ANALYSIS 排除 session 狀態句 | Modify |
| `paulsha_hippo/atomizer/config.py`、`atomizer/atomizer.yaml` | `episodic_filter` 欄位＋模板新鍵 | Modify |
| `paulsha_hippo/followups.py` | 抽取／ledger fold／verify | Create |
| `paulsha_hippo/dream/orchestrator.py`、`dream/cli.py` | `followups_fn` 階段 | Modify |
| `paulsha_hippo/wakeup/builder.py` | brief 末尾 follow-ups 一行 | Modify |
| `custom-skills/hippo-memory-kpi/scripts/report.py` | `followups` 區塊 | Modify |
| `paulsha_hippo/cli.py` | 接線 `show`、`followups`、`knowledge backfill-provenance|link-supersedes|mark-episodic` | Modify |
| `tests/test_hooks_git_snapshot.py`、`tests/test_runtime_flags.py`、`tests/test_show_cli.py`、`tests/test_topic.py`、`tests/test_shortlist_collapse.py`、`tests/test_backfill_provenance.py`、`tests/test_followups_extract.py`、`tests/test_followups_verify.py`、`tests/test_mark_episodic.py`、`tests/test_link_supersedes.py` | 新測試 | Create |

---

### Task 0: TTL 維持預設 90 天（決定 2026-08-25，無 override）

**Files:**
- 無 repo 檔案變更；不建立 `~/.config/paulshaclaw/janitor.override.yaml`

**決定與依據：** 使用者裁定 90 天未被使用即降級是合理政策，不加 override。已驗證：decay 是軟性的——檔案留在磁碟、`hippo search --include-decayed` 仍可取回，只是離開 shortlist；reactivation 只在同 source 重新 import 時發生。knowledge 層最早 `created_at` 為 2026-06-17，首波 `ttl_expired` 約 2026-09-15（60–90 天 bucket 153 筆、30–60 天 966 筆）；janitor 僅在 `hippo dream run` 或手動 `hippo janitor scan` 時執行，無 cron。

**已知量測盲區（由 Task 5 修）：** 目前只有 agent 實際 Read 檔案才寫 read event，shortlist 出現不算；4,178 筆中僅 785 筆有 read 記錄。Task 5 落地前的 decay 以年齡為主。

- [ ] **Step 1: 任何真實 scan 前先 dry-run 並保留報表**

Run: `hippo janitor scan --memory-root ~/.agents/memory --dry-run > /tmp/janitor-plan-$(date +%F).json && python3 -c "import json;d=json.load(open('/tmp/janitor-plan-$(date +%F).json'))['summary'];print(d['scanned'],d['decayed'])"`
Expected: 兩個整數；decayed >0 屬預期（09-15 後），逐筆看 `plan[]` 的 `reason`，只允許 `ttl_expired` / `superseded`。

- [ ] **Step 2: fix 2b（Task 15）跑 janitor 前重複 Step 1** — supersedes decay 與 ttl decay 共用同一次 scan，報表要能分辨兩者。

---

### Task 1: fix 1a — SessionEnd hook 填 `commit / git_branch / git_dirty`

**Files:**
- Modify: `paulsha_hippo/hooks/claude_session_end.py:92-114`
- Modify: `paulsha_hippo/hooks/codex_session_end.py:102-107`
- Modify: `paulsha_hippo/hooks/copilot_session_end.py:136-145`
- Test: `tests/test_hooks_git_snapshot.py`（新）

**Interfaces:**
- Produces: queue payload 新鍵 `commit: str(40-hex)`、`git_branch: str`、`git_dirty: bool`——僅在 `payload["cwd"]` 是 git repo 且 payload 原本沒有該鍵時寫入；任何失敗不寫、exit 0。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_hooks_git_snapshot.py
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "paulsha_hippo" / "hooks"
CAPTURE_HOOKS = ("claude_session_end.py", "codex_session_end.py", "copilot_session_end.py")


def _git_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    (path / "dirty.txt").write_text("x", encoding="utf-8")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _run(hook: str, payload: dict, memory_root: Path, path_env: str = "/usr/bin:/bin") -> Path:
    env = {"PATH": path_env, "HOME": str(memory_root.parent), "PSC_MEMORY_ROOT": str(memory_root),
           "PSC_IMPORTER_DISABLED": "1"}
    p = subprocess.run([sys.executable, str(HOOKS / hook)], input=json.dumps(payload),
                       text=True, capture_output=True, env=env, cwd=str(memory_root.parent))
    assert p.returncode == 0, p.stderr
    queue = list((memory_root / "runtime" / "queue").glob("*.json"))
    assert len(queue) == 1, queue
    return queue[0]


def test_hooks_fill_git_snapshot_from_cwd(tmp_path):
    head = _git_repo(tmp_path / "repo")
    for hook in CAPTURE_HOOKS:
        mr = tmp_path / hook.replace(".py", "") / "memory"
        mr.mkdir(parents=True)
        written = json.loads(_run(hook, {"session_id": "s1", "cwd": str(tmp_path / "repo")}, mr).read_text())
        assert written["commit"] == head, hook
        assert written["git_branch"] in ("master", "main"), hook
        assert written["git_dirty"] is True, hook


def test_hook_keeps_payload_commit_when_present(tmp_path):
    _git_repo(tmp_path / "repo")
    mr = tmp_path / "memory"; mr.mkdir()
    written = json.loads(_run("claude_session_end.py",
                              {"session_id": "s1", "cwd": str(tmp_path / "repo"), "commit": "e300b08"}, mr).read_text())
    assert written["commit"] == "e300b08"


def test_hook_non_repo_and_missing_git_leave_keys_absent(tmp_path):
    plain = tmp_path / "plain"; plain.mkdir()
    mr1 = tmp_path / "m1"; mr1.mkdir()
    written = json.loads(_run("claude_session_end.py", {"session_id": "s1", "cwd": str(plain)}, mr1).read_text())
    assert "commit" not in written and "git_branch" not in written and "git_dirty" not in written
    _git_repo(tmp_path / "repo")
    mr2 = tmp_path / "m2"; mr2.mkdir()
    # PATH 只剩一個空目錄：找不到 git → 仍 exit 0、queue 寫入、無 git 鍵
    empty_bin = tmp_path / "emptybin"; empty_bin.mkdir()
    written = json.loads(_run("claude_session_end.py", {"session_id": "s2", "cwd": str(tmp_path / "repo")},
                              mr2, path_env=str(empty_bin)).read_text())
    assert "commit" not in written
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_hooks_git_snapshot.py -q`
Expected: 3 failed（`KeyError: 'commit'` / `assert 'commit' not in ...` 反向失敗只在 Step 4 後才會 pass）

- [ ] **Step 3: 在三支 hook 各 inline 同一段 helper（stdlib-only）**

在 `TOOL = ...` 之後加入（三檔相同）：

```python
_GIT_TIMEOUT = 2


def _git_snapshot(cwd: object) -> dict:
    """best-effort：cwd 為 git repo 時回 {commit, git_branch, git_dirty}；任何失敗回 {}。"""
    if not isinstance(cwd, str) or not cwd:
        return {}

    def _run(args: list) -> "str | None":
        try:
            proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                                  timeout=_GIT_TIMEOUT)
        except Exception:
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    head = _run(["rev-parse", "HEAD"])
    if not head:
        return {}
    out = {"commit": head}
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch:
        out["git_branch"] = branch
    status = _run(["status", "--porcelain", "--untracked-files=normal"])
    if status is not None:
        out["git_dirty"] = bool(status)
    return out
```

在 `queue_payload["capture_id"] = capture_id` 之後加：

```python
        for key, value in _git_snapshot(queue_payload.get("cwd")).items():
            queue_payload.setdefault(key, value)
```

（codex hook 的 `subprocess` 已 import；copilot／claude 亦已 import `subprocess`。`_supplement_from_history` 之後再做 snapshot，讓 history 提供的 cwd 也能用。）

- [ ] **Step 4: 跑測試確認通過＋自足性回歸**

Run: `python3 -m pytest tests/test_hooks_git_snapshot.py tests/test_hooks_selfcontained.py tests/test_hooks.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/hooks/claude_session_end.py paulsha_hippo/hooks/codex_session_end.py paulsha_hippo/hooks/copilot_session_end.py tests/test_hooks_git_snapshot.py
git commit -m "feat(hooks): session-end 截取 git commit/branch/dirty 快照"
```

---

### Task 2: fix 1a/1b — importer 保存 6 鍵 provenance 與 `commit_source`

**Files:**
- Modify: `paulsha_hippo/importer/adapters/base.py:17-30`（TypedDict）、`:132-178`（`build_session`）
- Modify: `paulsha_hippo/importer/sanitizer.py:38`
- Modify: `paulsha_hippo/importer/_git.py`（加 `git_head`）
- Modify: `paulsha_hippo/importer/frontmatter.py:99-126`
- Modify: `paulsha_hippo/importer/pipeline.py:599-655`
- Test: `tests/test_importer_capture_contract.py`（追加）、`tests/test_git_helper.py`（追加）

**Interfaces:**
- Produces: `_git.git_head(toplevel) -> str | None`；`render_markdown(..., provenance_commit: str | None = None, provenance_commit_source: str | None = None, provenance_branch: str | None = None, provenance_dirty: bool | None = None)`；inbox frontmatter `provenance:` 區塊含 `commit_source`（永遠有：`hook` 或 `import-discovery`），`branch`／`dirty` 缺省省略。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_git_helper.py（追加在 GitHelperTests 內）
    def test_git_head_returns_sha_or_none(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "r"; repo.mkdir(); _init_repo(repo)
            subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            head = _git.git_head(str(repo))
            self.assertRegex(head, r"^[0-9a-f]{40}$")
            self.assertIsNone(_git.git_head(None))
            self.assertIsNone(_git.git_head(tmp))  # 非 repo
```

```python
# tests/test_importer_capture_contract.py（追加）
def _write_queue(tmp_path: Path, payload: dict) -> Path:
    q = tmp_path / "runtime" / "queue"; q.mkdir(parents=True, exist_ok=True)
    p = q / "codex__capture-contract__cap-1.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_payload_commit_lands_in_inbox_provenance_with_hook_source(tmp_path):
    q = _write_queue(tmp_path, _payload(cwd="/nonexistent", commit="abc123def", git_branch="feature/x", git_dirty=True))
    preview = pipeline.preview_queue_item(q, memory_root=tmp_path)
    fm = preview["rendered"].split("---")[1]
    assert "  commit: abc123def" in fm
    assert "  commit_source: hook" in fm
    assert "  branch: feature/x" in fm
    assert '  dirty: "true"' in fm


def test_missing_commit_falls_back_to_import_discovery(tmp_path, monkeypatch):
    from paulsha_hippo.importer import _git
    monkeypatch.setattr(_git, "git_toplevel", lambda cwd: "/repo/top")
    monkeypatch.setattr(_git, "git_remote", lambda top: "github.com/o/r")
    monkeypatch.setattr(_git, "git_main_toplevel", lambda top: top)
    monkeypatch.setattr(_git, "git_head", lambda top: "f" * 40)
    q = _write_queue(tmp_path, _payload(cwd="/repo/top"))
    fm = pipeline.preview_queue_item(q, memory_root=tmp_path)["rendered"].split("---")[1]
    assert "  commit: " + "f" * 40 in fm
    assert "  commit_source: import-discovery" in fm
    assert "  branch:" not in fm and "  dirty:" not in fm


def test_no_commit_anywhere_stays_unknown_without_source_lie(tmp_path, monkeypatch):
    from paulsha_hippo.importer import _git
    monkeypatch.setattr(_git, "git_toplevel", lambda cwd: None)
    monkeypatch.setattr(_git, "git_head", lambda top: None)
    q = _write_queue(tmp_path, _payload(cwd="/nowhere"))
    fm = pipeline.preview_queue_item(q, memory_root=tmp_path)["rendered"].split("---")[1]
    assert "  commit: _unknown" in fm
    assert "  commit_source:" not in fm
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_git_helper.py tests/test_importer_capture_contract.py -q -k "git_head or commit"`
Expected: FAIL（`AttributeError: module has no attribute 'git_head'`、`assert '  commit_source: hook' in fm`）

- [ ] **Step 3: 實作**

`importer/_git.py` 末尾：

```python
def git_head(toplevel: str | Path | None) -> Optional[str]:
    """Return HEAD sha for toplevel, or None（best-effort、never raises）。"""
    if not toplevel:
        return None
    return _run_git(["rev-parse", "HEAD"], cwd=toplevel)
```

`importer/adapters/base.py`：`NormalizedSession` 加兩個欄位 `git_branch: str | None` 與 `git_dirty: bool | None`；`build_session` 的 dict 加：

```python
        "git_branch": string_or_none(payload.get("git_branch")),
        "git_dirty": (payload.get("git_dirty") if isinstance(payload.get("git_dirty"), bool) else None),
```

（`content_hash` 的 subset **不加**這兩鍵，避免既有 session 重算 hash 變 `updated`。）

`importer/sanitizer.py:38`：`for key in ("assistant_summary", "session_title", "cwd", "repo", "commit", "git_branch"):`

`importer/frontmatter.py::render_markdown` 簽名加 4 個 kwargs（見 Interfaces），provenance 區塊改為：

```python
    commit_value = session.get("commit") if provenance_commit is None else provenance_commit
    prov_lines = [
        "provenance:",
        f"  repo: {_required_frontmatter_value(repo_value)}",
        f"  commit: {_required_frontmatter_value(commit_value)}",
        f"  path: {_required_frontmatter_value(session.get('raw_payload_pointer'))}",
    ]
    if provenance_commit_source:
        prov_lines.append(f"  commit_source: {_frontmatter_value(provenance_commit_source)}")
    branch = session.get("git_branch") if provenance_branch is None else provenance_branch
    if branch:
        prov_lines.append(f"  branch: {_frontmatter_value(branch)}")
    dirty = session.get("git_dirty") if provenance_dirty is None else provenance_dirty
    if isinstance(dirty, bool):
        prov_lines.append(f'  dirty: "{"true" if dirty else "false"}"')
```

並把原本 `lines` 中三行 provenance 換成 `*prov_lines`。

`importer/pipeline.py::_preview_queue_item_unlocked`，在 `provenance_repo = ...` 之後：

```python
    payload_commit = session.get("commit")
    if payload_commit:
        provenance_commit, commit_source = str(payload_commit), "hook"
    else:
        discovered_head = _git.git_head(discovered_toplevel)
        provenance_commit = discovered_head or None
        commit_source = "import-discovery" if discovered_head else None
```

`render_markdown(...)` 呼叫加 `provenance_commit=provenance_commit, provenance_commit_source=commit_source`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_git_helper.py tests/test_importer_capture_contract.py tests/test_frontmatter.py tests/test_adapters.py tests/test_idempotency.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer tests/test_git_helper.py tests/test_importer_capture_contract.py
git commit -m "feat(importer): provenance 保存 commit_source/branch/dirty，缺 commit 時以 repo HEAD 補"
```

---

### Task 3: fix 1a — atomizer／janitor 六鍵 provenance 貫通

**Files:**
- Modify: `paulsha_hippo/atomizer/pipeline.py:856`、`:899-907`（`_render_fragment`）、`:923`（`_read_fragment`）
- Modify: `paulsha_hippo/atomizer/slice_frontmatter.py:202`
- Modify: `paulsha_hippo/janitor/record_source.py:124`
- Test: `tests/test_distillation_provenance.py`（追加）、`tests/test_atomizer_pipeline.py`（追加）

**Interfaces:**
- 新常數 `slice_frontmatter.PROVENANCE_KEYS = ("repo", "commit", "path", "commit_source", "branch", "dirty")`，四個檔案都改用它。
- `_read_fragment`／`_split_pass` 只保留**存在且非空**的鍵（不再把缺鍵補成 `""`），`_render_fragment` 只渲染存在的鍵；`render()` 同理（`repo/commit/path` 三鍵永遠渲染，其餘存在才渲染）。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_distillation_provenance.py（追加）
def test_six_key_provenance_round_trips_through_atom_frontmatter():
    prov = {"repo": "github.com/o/r", "commit": "a" * 40, "path": "/q.json",
            "commit_source": "hook", "branch": "main", "dirty": "false"}
    slice_ = slice_frontmatter.Slice(
        slice_id="sl-x", body="b\n",
        frontmatter={"provenance": prov, "distiller": {"profile_id": "claude"}, "slice_id": "sl-x",
                     "memory_layer": "knowledge", "supersedes": []})
    fm, _ = frontmatter_io.read(slice_frontmatter.render(slice_))
    assert fm["provenance"] == prov


def test_three_key_provenance_renders_without_optional_keys():
    prov = {"repo": "r", "commit": "_unknown", "path": "/q.json"}
    slice_ = slice_frontmatter.Slice(slice_id="sl-y", body="b\n",
                                     frontmatter={"provenance": prov, "distiller": {}, "slice_id": "sl-y"})
    text = slice_frontmatter.render(slice_)
    assert "commit_source" not in text and "branch" not in text and "dirty" not in text
```

```python
# tests/test_atomizer_pipeline.py（追加一個 test：inbox 6 鍵 → fragment → knowledge slice 6 鍵）
    def test_split_and_promote_preserve_six_key_provenance(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md"
            raw.parent.mkdir(parents=True)
            raw.write_text(_RAW.replace(
                "  path: docs/x.md\n",
                "  path: docs/x.md\n  commit_source: hook\n  branch: main\n  dirty: \"true\"\n"),
                encoding="utf-8")
            cfg, h = atomizer_config.load_config(override_path=None)
            pipeline.run(root, config=cfg, config_hash=h, now="2026-06-03T00:00:00Z",
                         promoter=IdentityPromoter())
            frag = next((root / "inbox" / "_slices").rglob("*.md"))
            ffm, _ = pipeline._parse_frontmatter(frag.read_text(encoding="utf-8"))
            self.assertEqual(ffm["provenance"]["branch"], "main")
            note = next((root / "knowledge" / "paulshaclaw").glob("*.md"))
            nfm, _ = pipeline._parse_frontmatter(note.read_text(encoding="utf-8"))
            self.assertEqual(nfm["provenance"]["commit_source"], "hook")
            self.assertEqual(nfm["provenance"]["dirty"], "true")
```

（`_RAW` 為 `tests/test_dream_cli.py` 的 inbox fixture 字串——複製到本測試檔頂部；identity promoter 用 `from paulsha_hippo.atomizer.promoter import IdentityPromoter`，若該類名不同，以 `atomizer/promoter.py` 內的 identity 實作為準。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_distillation_provenance.py tests/test_atomizer_pipeline.py -q -k "six_key or three_key"`
Expected: FAIL（provenance 只剩 3 鍵）

- [ ] **Step 3: 實作**

`slice_frontmatter.py` 頂部加 `PROVENANCE_KEYS = ("repo", "commit", "path", "commit_source", "branch", "dirty")`；`render()`：

```python
    provenance = fm.get("provenance") or {}
    if isinstance(provenance, dict):
        lines.append("provenance:")
        for pkey in PROVENANCE_KEYS:
            if pkey in ("repo", "commit", "path") or provenance.get(pkey):
                lines.append(f"  {pkey}: {json.dumps(str(provenance.get(pkey, '')), ensure_ascii=False)}")
```

`atomizer/pipeline.py` 兩處（`_split_pass` 856、`_read_fragment` 923）改為：

```python
        provenance = {k: str(provenance[k]) for k in slice_frontmatter.PROVENANCE_KEYS
                      if provenance.get(k) not in (None, "")}
```

`_render_fragment`：

```python
    prov_lines = ["provenance:"] + [f"  {k}: {json.dumps(str(provenance.get(k, '')), ensure_ascii=False)}"
                                    for k in slice_frontmatter.PROVENANCE_KEYS
                                    if k in ("repo", "commit", "path") or provenance.get(k)]
```

並把原本三行 `"provenance:", f"  repo: ...", f"  commit: ...", f"  path: ..."` 換成 `*prov_lines`。
`janitor/record_source.py:124`：`if k in ("repo", "commit", "path", "commit_source", "branch", "dirty")`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_distillation_provenance.py tests/test_atomizer_pipeline.py tests/test_slice_frontmatter.py tests/test_janitor_record_source.py tests/test_atomizer_e2e.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/atomizer/pipeline.py paulsha_hippo/atomizer/slice_frontmatter.py paulsha_hippo/janitor/record_source.py tests/test_distillation_provenance.py tests/test_atomizer_pipeline.py
git commit -m "feat(atomizer): provenance 六鍵貫通 fragment 與 knowledge slice"
```

---
### Task 4: `runtime_flags.py` — hygiene flags 單一讀取點

**Files:**
- Create: `paulsha_hippo/runtime_flags.py`
- Modify: `paulsha_hippo/atomizer/atomizer.yaml`（末尾加模板鍵）
- Test: `tests/test_runtime_flags.py`（新）

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class HygieneFlags:
      collapse_same_topic: bool = True
      read_hint: str = "show"          # "read" | "show"
      followups_enabled: bool = True
      episodic_filter: bool = True

  def load_flags(config_path: Path | None = None) -> HygieneFlags
  ```
  `config_path` 預設 `paths.atomizer_config_path()`；檔案不存在／無 PyYAML／解析失敗／型別錯 → 對應鍵回預設值，永不 raise。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_runtime_flags.py
from pathlib import Path
from paulsha_hippo import runtime_flags as rf


def test_defaults_when_file_missing(tmp_path):
    f = rf.load_flags(tmp_path / "nope.yaml")
    assert f == rf.HygieneFlags()


def test_reads_keys_and_falls_back_per_key(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("shortlist:\n  collapse_same_topic: false\n  read_hint: read\nfollowups:\n  enabled: 'nope'\nepisodic_filter: false\n",
                 encoding="utf-8")
    f = rf.load_flags(p)
    assert f.collapse_same_topic is False and f.read_hint == "read"
    assert f.followups_enabled is True      # 型別錯 → 預設
    assert f.episodic_filter is False


def test_invalid_read_hint_falls_back(tmp_path):
    p = tmp_path / "config.yaml"; p.write_text("shortlist:\n  read_hint: whatever\n", encoding="utf-8")
    assert rf.load_flags(p).read_hint == "show"


def test_template_declares_flags():
    tpl = Path(rf.__file__).resolve().parent / "atomizer" / "atomizer.yaml"
    text = tpl.read_text(encoding="utf-8")
    for key in ("collapse_same_topic", "read_hint", "followups:", "episodic_filter"):
        assert key in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_runtime_flags.py -q`
Expected: FAIL（`ModuleNotFoundError: paulsha_hippo.runtime_flags`）

- [ ] **Step 3: 實作**

```python
# paulsha_hippo/runtime_flags.py
"""issue-136-knowledge-hygiene flags：從 canonical config.yaml best-effort 讀取，缺鍵／壞檔一律預設。"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from paulsha_hippo import paths

_READ_HINTS = ("read", "show")


@dataclass(frozen=True)
class HygieneFlags:
    collapse_same_topic: bool = True
    read_hint: str = "show"
    followups_enabled: bool = True
    episodic_filter: bool = True


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def load_flags(config_path: Path | None = None) -> HygieneFlags:
    path = Path(config_path) if config_path is not None else paths.atomizer_config_path()
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return HygieneFlags()
    data = _mapping(data)
    shortlist = _mapping(data.get("shortlist"))
    followups = _mapping(data.get("followups"))
    hint = shortlist.get("read_hint")
    return HygieneFlags(
        collapse_same_topic=_bool(shortlist.get("collapse_same_topic"), True),
        read_hint=hint if hint in _READ_HINTS else "show",
        followups_enabled=_bool(followups.get("enabled"), True),
        episodic_filter=_bool(data.get("episodic_filter"), True),
    )
```

`atomizer/atomizer.yaml` 末尾追加：

```yaml
# issue-136-knowledge-hygiene（2026-08-25）：以下鍵全部 optional，缺鍵即預設值。
shortlist:
  collapse_same_topic: true   # 同 project／family 同主題只留最新（fix 2a）
  read_hint: show             # show：hint 建議 `hippo show --agent`；read：舊行為
followups:
  enabled: true               # follow-up ledger 抽取／verify／brief 一行（fix 5）
episodic_filter: true         # session 狀態句 slice 降層 episodic（fix 4）
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_runtime_flags.py tests/test_atomizer_config.py tests/test_canonical_config_contract.py tests/test_config_migration.py -q`
Expected: all passed（後三者確認模板新鍵不破壞 config 載入／migration 契約）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/runtime_flags.py paulsha_hippo/atomizer/atomizer.yaml tests/test_runtime_flags.py
git commit -m "feat(config): 新增 issue-136-knowledge-hygiene runtime flags 讀取點"
```

---

### Task 5: fix 3b — `hippo show <ref> --agent`＋read 歸因

**Files:**
- Create: `paulsha_hippo/usage_read.py`
- Create: `paulsha_hippo/show.py`
- Modify: `paulsha_hippo/cli.py`（`memory_subparsers` 加 `show`，放在 `recall` 旁）
- Test: `tests/test_show_cli.py`（新）

**Interfaces:**
- `usage_read.append_read_event(root: Path, *, tool: str, session_id: str, sl_id: str, path: Path, project: str) -> dict`：查 `offered_map_path(root, tool, session_id)` 的 `by_id` 決定 `offered`，append 一筆 `{"ts","session_id","tool","project","sl_id","path","source":"read","offered"}` 到 `runtime/ledger/memory_usage.jsonl`（與 `hooks/claude_post_tool_use.py:87-93` 同 schema）。
- `show.resolve_ref(memory_root: Path, ref: str) -> Path`：`ref` 是既存路徑 → 直接用；否則視為 slice_id，`glob knowledge/**/*--<ref>.md`，0 或 >1 命中 raise `ShowError`。
- `show.render_agent_view(path: Path) -> str`：
  ```
  # <title>
  slice_id: … | project: … | captured_at: … | artifact_kind: … | supersedes: [...] | commit: <sha>(<commit_source>)
  cites: README-ARC.md:108, src/x.c:12
  ---
  <body>
  ```
  不含 `distiller`／`checksum`／`publication_id`／`distilled_from`。
- CLI：`hippo show <ref> --memory-root R [--agent] [--tool T] [--session-id S]`；`--agent` 印 agent view（否則印整檔）；`--tool` 與 `--session-id` 必須同時給，給了才記 read 事件；exit 1 於 `ShowError`。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_show_cli.py
import io, json
from contextlib import redirect_stdout
from pathlib import Path
from paulsha_hippo import cli

_NOTE = (
    "---\nphase: review\nproject: proj\nslice_id: sl-aaaaaaaaaaaaaaaa\nartifact_kind: report\nversion: \"1\"\n"
    "created_at: \"2026-08-01 00:00:00\"\ncreated_by: codex\nsource_session: s0\ngate_required: false\n"
    "checksum: deadbeef\nmemory_layer: knowledge\nsource_agent: codex\ncaptured_at: \"2026-08-01 00:00:00\"\n"
    "supersedes: []\ndistilled_from: \"codex:s0\"\ntitle: \"Flash Layout\"\ntags:\n  - flash\n"
    "cites:\n  -\n    path: README-ARC.md\n    line: 108\n"
    "publication_id: pub1\nprovenance:\n  repo: \"r\"\n  commit: \"abc\"\n  path: \"/q.json\"\n  commit_source: hook\n"
    "distiller:\n  profile_id: claude\n  attempts: [{\"x\": 1}]\n---\n"
    "Layout 定義在 lds:35。\n\n第二段。\n"
)


def _seed(mr: Path) -> Path:
    p = mr / "knowledge" / "proj" / "flash-layout--sl-aaaaaaaaaaaaaaaa.md"
    p.parent.mkdir(parents=True); p.write_text(_NOTE, encoding="utf-8"); return p


def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def test_agent_view_strips_machine_frontmatter(tmp_path):
    _seed(tmp_path)
    rc, out = _run(["show", "sl-aaaaaaaaaaaaaaaa", "--memory-root", str(tmp_path), "--agent"])
    assert rc == 0
    assert out.startswith("# Flash Layout\n")
    assert "commit: abc(hook)" in out and "cites: README-ARC.md:108" in out
    for banned in ("distiller", "checksum", "publication_id", "attempts", "distilled_from"):
        assert banned not in out
    body = "Layout 定義在 lds:35。\n\n第二段。\n"
    assert out.endswith(body)
    assert len(out.encode("utf-8")) <= len(body.encode("utf-8")) + 400


def test_agent_view_records_read_event_with_offered_flag(tmp_path):
    p = _seed(tmp_path)
    wk = tmp_path / "runtime" / "wakeup"; wk.mkdir(parents=True)
    (wk / "claude-code__s1.offered.json").write_text(
        json.dumps({"by_path": {str(p): "sl-aaaaaaaaaaaaaaaa"}, "by_id": {"sl-aaaaaaaaaaaaaaaa": str(p)}}))
    rc, _ = _run(["show", "sl-aaaaaaaaaaaaaaaa", "--memory-root", str(tmp_path), "--agent",
                  "--tool", "claude-code", "--session-id", "s1"])
    assert rc == 0
    ev = [json.loads(l) for l in (tmp_path / "runtime" / "ledger" / "memory_usage.jsonl").read_text().splitlines()]
    assert len(ev) == 1 and ev[0]["source"] == "read" and ev[0]["offered"] is True
    assert ev[0]["sl_id"] == "sl-aaaaaaaaaaaaaaaa" and ev[0]["project"] == "proj"


def test_no_tool_session_no_event_and_path_ref_works(tmp_path):
    p = _seed(tmp_path)
    rc, out = _run(["show", str(p), "--memory-root", str(tmp_path), "--agent"])
    assert rc == 0 and "# Flash Layout" in out
    assert not (tmp_path / "runtime" / "ledger" / "memory_usage.jsonl").exists()


def test_unknown_ref_exits_1(tmp_path):
    rc, _ = _run(["show", "sl-nope", "--memory-root", str(tmp_path), "--agent"])
    assert rc == 1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_show_cli.py -q`
Expected: FAIL（argparse `invalid choice: 'show'`，exit 2 → `SystemExit`）

- [ ] **Step 3: 實作**

```python
# paulsha_hippo/usage_read.py
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from paulsha_hippo.hooks._wakeup_common import offered_map_path


def append_read_event(root: Path, *, tool: str, session_id: str, sl_id: str, path: Path, project: str) -> dict:
    """與 hooks/claude_post_tool_use.py 同 schema 的 read 事件（offered 由 per-session map by_id 判定）。"""
    offered = False
    try:
        by_id = json.loads(offered_map_path(root, tool, session_id).read_text(encoding="utf-8")).get("by_id", {})
        offered = sl_id in by_id
    except Exception:
        offered = False
    ev = {"ts": datetime.now(timezone.utc).isoformat(), "session_id": session_id, "tool": tool,
          "project": project, "sl_id": sl_id, "path": str(path), "source": "read", "offered": offered}
    led = root / "runtime" / "ledger"; led.mkdir(parents=True, exist_ok=True)
    with (led / "memory_usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev
```

```python
# paulsha_hippo/show.py
from __future__ import annotations
from pathlib import Path
from paulsha_hippo.moc import frontmatter_io as fio


class ShowError(Exception):
    pass


def resolve_ref(memory_root: Path, ref: str) -> Path:
    candidate = Path(ref)
    if candidate.is_file():
        return candidate
    hits = sorted((memory_root / "knowledge").rglob(f"*--{ref}.md")) if (memory_root / "knowledge").is_dir() else []
    if len(hits) != 1:
        raise ShowError(f"show: {ref}: {'not found' if not hits else 'ambiguous'} ({len(hits)} match)")
    return hits[0]


def _cites(fm: dict) -> str:
    items = fm.get("cites") if isinstance(fm.get("cites"), list) else []
    return ", ".join(f"{c.get('path')}:{c.get('line')}" for c in items if isinstance(c, dict) and c.get("path"))


def render_agent_view(path: Path) -> str:
    fm, body = fio.read(path.read_text(encoding="utf-8"))
    prov = fm.get("provenance") if isinstance(fm.get("provenance"), dict) else {}
    commit = str(prov.get("commit") or "_unknown")
    if prov.get("commit_source"):
        commit = f"{commit}({prov['commit_source']})"
    header = [f"# {fm.get('title') or fm.get('atom_title') or path.stem}",
              " | ".join([f"slice_id: {fm.get('slice_id', '')}", f"project: {fm.get('project', '')}",
                          f"captured_at: {fm.get('captured_at', '')}", f"artifact_kind: {fm.get('artifact_kind', '')}",
                          f"supersedes: {fm.get('supersedes') or []}", f"commit: {commit}"])]
    cites = _cites(fm)
    if cites:
        header.append(f"cites: {cites}")
    header.append("---")
    return "\n".join(header) + "\n" + body
```

`cli.py`：在 `recall` parser 附近加

```python
    show_p = memory_subparsers.add_parser("show", help="印出一筆 knowledge note；--agent 只印精簡 header＋body（省 ~70% token）")
    show_p.add_argument("ref", help="slice_id 或檔案路徑")
    show_p.add_argument("--memory-root", required=True)
    show_p.add_argument("--agent", action="store_true")
    show_p.add_argument("--tool", default=None)
    show_p.add_argument("--session-id", default=None)
    show_p.set_defaults(func=_show)
```

```python
def _show(args: argparse.Namespace) -> int:
    from . import show as show_mod
    root = Path(args.memory_root)
    try:
        path = show_mod.resolve_ref(root, args.ref)
    except show_mod.ShowError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.tool) != bool(args.session_id):
        print("show: --tool 與 --session-id 必須同時提供", file=sys.stderr)
        return 2
    text = show_mod.render_agent_view(path) if args.agent else path.read_text(encoding="utf-8")
    if args.tool and args.session_id:
        from .moc import frontmatter_io as fio
        from .usage_read import append_read_event
        fm, _ = fio.read(path.read_text(encoding="utf-8"))
        append_read_event(root, tool=args.tool, session_id=args.session_id,
                          sl_id=str(fm.get("slice_id", "")), path=path, project=str(fm.get("project", "")))
    sys.stdout.write(text)
    return 0
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_show_cli.py tests/test_cli.py tests/test_hippo_memory_kpi_skill.py -q`
Expected: all passed（KPI report 的 `viewed` 只認 `source=="read"` 且 offer 在 read 之前——事件 schema 相同即自動納入；`test_hippo_memory_kpi_skill.py` 加一案例：seed offered → `append_read_event` → report `usage.viewed.count == 1`）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/show.py paulsha_hippo/usage_read.py paulsha_hippo/cli.py tests/test_show_cli.py tests/test_hippo_memory_kpi_skill.py
git commit -m "feat(cli): hippo show --agent 精簡視圖並記 read 歸因"
```

---

### Task 6: fix 3b — shortlist hint 改指向 `hippo show --agent`

**Files:**
- Modify: `paulsha_hippo/retrieval.py:26,51-60`
- Modify: `paulsha_hippo/hooks/_shortlist_common.py:414-438`
- Modify: `paulsha_hippo/hooks/_wakeup_common.py:183-190`
- Test: `tests/test_retrieval.py`、`tests/test_shortlist_common.py`、`tests/test_user_prompt_submit_hook.py`（更新既有斷言）

**Interfaces:**
- `retrieval.format_shortlist(hits, *, hint: str = "read", show_command: str = "") -> str`：`hint=="show"` 時第一行為 `> 與當前任務相關的記憶（相關項執行 `<show_command> <slice_id>` 取精簡全文，比 Read 省約 70% token）：`；每行格式 `- [title] — summary — path — slice_id`（path 保留：offered map 以 path 為鍵）。
- `_shortlist_common.build_shortlist_and_record` 依 `runtime_flags.load_flags().read_hint` 決定；`show_command` = `" ".join(shlex.quote(a) for a in hippo_invocation(root) + ["show", "--memory-root", str(root), "--agent", "--tool", tool, "--session-id", session_id])`。
- `_wakeup_common.recall_guidance_hint` 第三行改「再用 `hippo show --agent <slice_id> …` 取精簡全文」。

- [ ] **Step 1: 更新／新增測試（先 RED）**

```python
# tests/test_retrieval.py（追加）
def test_format_shortlist_show_mode_appends_slice_id_and_command():
    out = format_shortlist([{"title": "T", "summary": "s", "path": "/m/k/a.md", "slice_id": "sl-1"}],
                           hint="show", show_command="/v/python -m paulsha_hippo show --memory-root /m --agent --tool claude-code --session-id s1")
    assert "hippo show" in out and out.rstrip().endswith("— /m/k/a.md — sl-1")
    assert "Read 開啟" not in out
```

```python
# tests/test_shortlist_common.py：test_shortlist_injects_and_records_offered 的
#   assert note in out and "Read" in out
# 改為
    assert note in out and "show --memory-root" in out and "sl-aaaaaaaaaaaaaaaa" in out
# 並新增：
def test_shortlist_read_hint_flag_restores_read_wording(tmp_path, monkeypatch):
    from paulsha_hippo import runtime_flags as rf
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    monkeypatch.setattr(SC, "load_flags", lambda: rf.HygieneFlags(read_hint="read"))
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "sidR", cwd="/x", prompt="SerialWrap 執行")
    assert "Read" in out and "show --memory-root" not in out
```

`tests/test_user_prompt_submit_hook.py::test_relevant_prompt_injects_shortlist`：`assert "a.md" in ctx and "Read" in ctx` → `assert "a.md" in ctx and "show --memory-root" in ctx`。

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_retrieval.py tests/test_shortlist_common.py tests/test_user_prompt_submit_hook.py -q`
Expected: FAIL（`TypeError: unexpected keyword 'hint'`；`"show --memory-root" in out`）

- [ ] **Step 3: 實作**

`retrieval.py`：

```python
_SHORTLIST_HINT = "> 與當前任務相關的記憶（相關項用 Read 開啟下列絕對路徑取全文）："
_SHORTLIST_HINT_SHOW = ("> 與當前任務相關的記憶（相關項執行 `{cmd} <slice_id>` 取精簡全文，"
                        "比 Read 省約 70% token）：")


def format_shortlist(hits: list[dict], *, hint: str = "read", show_command: str = "") -> str:
    if not hits:
        return ""
    lines = [_SHORTLIST_HINT_SHOW.format(cmd=show_command) if hint == "show" and show_command else _SHORTLIST_HINT]
    for h in hits:
        title = (h.get("title") or "").strip() or "(untitled)"
        summary = (h.get("summary") or "").strip()
        path = h.get("path") or ""
        line = f"- [{title}] — {summary} — {path}"
        if h.get("slice_id"):
            line += f" — {h['slice_id']}"
        lines.append(line)
    return "\n".join(lines)
```

`_shortlist_common.py`：頂部 `from paulsha_hippo.runtime_flags import load_flags`；在 `block = _redact(...)` 之前：

```python
            flags = load_flags()
            show_cmd = " ".join(shlex.quote(a) for a in hippo_invocation(root) + [
                "show", "--memory-root", str(root), "--agent", "--tool", tool, "--session-id", session_id])
            block = _redact(root, tool, project, session_id,
                            format_shortlist(claim, hint=flags.read_hint, show_command=show_cmd))
```

（`hippo_invocation` 已從 `_wakeup_common` import；`shlex` 已 import。）`_wakeup_common.recall_guidance_hint` 最後一行改為 `"再對輸出清單中的 slice_id 執行 `hippo show --agent <slice_id> --memory-root …` 取精簡全文。"`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_retrieval.py tests/test_shortlist_common.py tests/test_user_prompt_submit_hook.py tests/test_recall_cli.py tests/test_recall_guidance.py tests/test_copilot_user_prompt_submit_hook.py -q`
Expected: all passed（若 recall_guidance 測試斷言舊字串，同步改斷言）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/retrieval.py paulsha_hippo/hooks/_shortlist_common.py paulsha_hippo/hooks/_wakeup_common.py tests/test_retrieval.py tests/test_shortlist_common.py tests/test_user_prompt_submit_hook.py tests/test_recall_guidance.py
git commit -m "feat(shortlist): hint 改建議 hippo show --agent，行尾附 slice_id"
```

---

### Task 7: fix 2a — `topic.py` 同主題判定＋`projects.yaml` families

**Files:**
- Create: `paulsha_hippo/topic.py`
- Modify: `paulsha_hippo/importer/config.py:24-27`（`ProjectsConfig.families`）、`:73-133`（解析頂層 `families:`）
- Test: `tests/test_topic.py`（新）、`tests/test_project_registry.py`（追加）

**Interfaces:**
- `ProjectsConfig.families: tuple[tuple[str, ...], ...] = ()`；`projects.yaml` 頂層：
  ```yaml
  families:
    - [MCU-Octopus, ot-ti-mirror]
  ```
  （行內 list 形式，沿用 `_inline_list`；不在 `projects:` 區塊內。）
- `topic.family_key(project: str, families: Iterable[Iterable[str]]) -> str`：project 所屬 family 的排序後首個 slug；不在任何 family → project 本身。
- `topic.title_tokens(title: str, project: str = "") -> set[str]`：去掉 `project` 前綴（case-insensitive，含尾隨空白／冒號／破折號）後，用 `llm_promoter._GROUNDING_WORD_RE` ＋ CJK bigram 切 token（與 `_grounding_units` 同規則，複製常數，不 import promoter 以免拉 LLM 相依）。
- `topic.canonical_title(value) -> str`：與 `atomizer/pipeline._canonical_title` 同（搬到 topic，pipeline 改 import）。
- `topic.is_same_topic(a: Mapping, b: Mapping, *, families=()) -> bool`：a／b 至少含 `project`、`title`，可選 `aliases`；規則：`family_key` 相等 且（canonical title 相等 ∨ 任一 alias canonical 相等 ∨ 雙方 token ≥ 4 且 Jaccard ≥ 0.6）。
- `topic.collapse_same_topic(hits: list[dict], *, families=()) -> tuple[list[dict], dict[str, list[str]]]`：hits 需含 `slice_id`、`project`、`title`、`captured_at`；貪婪分組（依 captured_at 由新到舊掃，與已保留者同主題即折疊），回傳（保留清單、`{kept_id: [collapsed_ids]}`）；`captured_at` 缺或不可解析視為最舊。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_topic.py
from paulsha_hippo import topic

FAM = (("MCU-Octopus", "ot-ti-mirror"),)


def _n(sid, project, title, at, aliases=()):
    return {"slice_id": sid, "project": project, "title": title, "captured_at": at, "aliases": list(aliases)}


def test_exact_title_same_project():
    assert topic.is_same_topic(_n("a", "p", "CC2674P10 Flash 配置", "x"), _n("b", "p", "cc2674p10  flash 配置", "y"))


def test_alias_match():
    assert topic.is_same_topic(_n("a", "p", "X", "x", aliases=["雙目標 Flash Layout"]), _n("b", "p", "雙目標 Flash Layout", "y"))


def test_jaccard_boundary_and_short_title_guard():
    a = _n("a", "p", "dual profile build isolation contract", "x")
    b = _n("b", "p", "dual build profile isolation contract", "y")   # 5/5 token 交集
    assert topic.is_same_topic(a, b)
    c = _n("c", "p", "dual build profile isolation gate", "y")        # 4/6 = 0.66 → same
    assert topic.is_same_topic(a, c)
    d = _n("d", "p", "dual build profile", "y")                       # tokens < 4 → 只接受相等
    assert not topic.is_same_topic(a, d)
    e = _n("e", "p", "dual build profile isolation gate extra words", "y")  # 4/8 = 0.5 → not
    assert not topic.is_same_topic(a, e)


def test_project_prefix_stripped_and_family_bridges_projects():
    a = _n("a", "ot-ti-mirror", "ot-ti-mirror Flash Layout and Image Artifacts", "2026-08-17T00:00:00Z")
    b = _n("b", "MCU-Octopus", "雙目標 Flash Layout", "2026-08-21T00:00:00Z")
    assert not topic.is_same_topic(a, b)                       # 無 family：不同 project 永不同組
    assert not topic.is_same_topic(a, b, families=FAM)         # 有 family 但標題 Jaccard 不足 → 仍 False（review tier 才處理）
    assert topic.family_key("ot-ti-mirror", FAM) == topic.family_key("MCU-Octopus", FAM)


def test_collapse_keeps_newest_per_group():
    hits = [_n("old", "p", "Flash Layout", "2026-08-17T00:00:00Z"),
            _n("new", "p", "flash layout", "2026-08-21T00:00:00Z"),
            _n("other", "p", "Release Button DIO24", "2026-08-01T00:00:00Z")]
    kept, collapsed = topic.collapse_same_topic(hits)
    assert [h["slice_id"] for h in kept] == ["new", "other"]
    assert collapsed == {"new": ["old"]}
```

```python
# tests/test_project_registry.py（追加）
def test_projects_config_parses_top_level_families():
    from paulsha_hippo.importer.config import parse_projects_config
    cfg = parse_projects_config("version: 1\nfamilies:\n  - [MCU-Octopus, ot-ti-mirror]\n  - [a, b]\nprojects:\n  a:\n    slug: a\n")
    assert cfg.families == (("MCU-Octopus", "ot-ti-mirror"), ("a", "b"))
    assert cfg.projects[0].slug == "a"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_topic.py tests/test_project_registry.py -q -k "topic or families"`
Expected: FAIL（`ModuleNotFoundError: paulsha_hippo.topic`；`ProjectsConfig has no attribute families`）

- [ ] **Step 3: 實作**

```python
# paulsha_hippo/topic.py
"""同主題判定（fix 2）：純函式，不讀檔、不 import LLM 相依。"""
from __future__ import annotations
import re
from typing import Iterable, Mapping

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{2,}")
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_STOP = {"and", "are", "for", "from", "into", "one", "only", "that", "the", "their", "then", "this", "use", "with"}
MIN_TOKENS_FOR_JACCARD = 4
JACCARD_THRESHOLD = 0.6


def canonical_title(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def strip_project_prefix(title: str, project: str) -> str:
    if project and title.casefold().startswith(project.casefold()):
        return title[len(project):].lstrip(" :-—").strip()
    return title


def title_tokens(title: str, project: str = "") -> set[str]:
    text = strip_project_prefix(str(title or ""), project or "")
    units = {t.casefold() for t in _WORD_RE.findall(text) if t.casefold() not in _STOP}
    for run in _CJK_RE.findall(text):
        units.update(run[i:i + 2] for i in range(len(run) - 1))
    return units


def family_key(project: str, families: Iterable[Iterable[str]] = ()) -> str:
    for fam in families:
        members = sorted(str(m) for m in fam)
        if project in members:
            return members[0]
    return project


def _canon_set(note: Mapping) -> set[str]:
    names = [note.get("title")] + list(note.get("aliases") or [])
    return {canonical_title(n) for n in names if canonical_title(n)}


def is_same_topic(a: Mapping, b: Mapping, *, families: Iterable[Iterable[str]] = ()) -> bool:
    if family_key(str(a.get("project", "")), families) != family_key(str(b.get("project", "")), families):
        return False
    if _canon_set(a) & _canon_set(b):
        return True
    ta = title_tokens(str(a.get("title", "")), str(a.get("project", "")))
    tb = title_tokens(str(b.get("title", "")), str(b.get("project", "")))
    if len(ta) < MIN_TOKENS_FOR_JACCARD or len(tb) < MIN_TOKENS_FOR_JACCARD:
        return False
    union = ta | tb
    return bool(union) and len(ta & tb) / len(union) >= JACCARD_THRESHOLD


def collapse_same_topic(hits: list[dict], *, families: Iterable[Iterable[str]] = ()) -> tuple[list[dict], dict[str, list[str]]]:
    ordered = sorted(hits, key=lambda h: str(h.get("captured_at") or ""), reverse=True)
    kept: list[dict] = []
    collapsed: dict[str, list[str]] = {}
    for h in ordered:
        owner = next((k for k in kept if is_same_topic(k, h, families=families)), None)
        if owner is None:
            kept.append(h)
        else:
            collapsed.setdefault(str(owner["slice_id"]), []).append(str(h["slice_id"]))
    return kept, collapsed
```

`importer/config.py`：`ProjectsConfig` 加 `families: tuple[tuple[str, ...], ...] = ()`；`parse_projects_config` 迴圈開頭加分支：

```python
        if indent == 0 and stripped == "families:":
            in_projects = False; in_families = True; continue
        if in_families and indent >= 2 and stripped.startswith("- "):
            families.append(_inline_list(stripped[2:].strip())); continue
        if indent == 0:
            in_families = False
```

（`families: list[tuple[str, ...]] = []`、`in_families = False` 於迴圈前宣告；回傳 `ProjectsConfig(projects=..., aliases=..., families=tuple(families))`。）`load_union_projects_config` 若重建 `ProjectsConfig`，同步帶入 `families`。`atomizer/pipeline._canonical_title` 改為 `from paulsha_hippo.topic import canonical_title as _canonical_title`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_topic.py tests/test_project_registry.py tests/test_project_resolver.py tests/test_atomizer_pipeline.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/topic.py paulsha_hippo/importer/config.py paulsha_hippo/atomizer/pipeline.py tests/test_topic.py tests/test_project_registry.py
git commit -m "feat(topic): 同主題判定與 projects.yaml families"
```

---
### Task 8: fix 2a — `search()` 帶 `captured_at`，shortlist 折疊 keep-newest

**Files:**
- Modify: `paulsha_hippo/moc/search.py:481-530`（SELECT 加 `m.captured_at`，回傳 dict 加鍵；`_row_read_count` 索引改 8）
- Modify: `paulsha_hippo/hooks/_shortlist_common.py:168-184`（offered ledger 加 `collapsed`）、`:409-411`（claim 前折疊）
- Test: `tests/test_moc_search.py`（追加）、`tests/test_shortlist_collapse.py`（新）

**Interfaces:**
- `search()` 回傳每筆加 `"captured_at": str`（其餘鍵不變、排序不變）。
- `_append_offered_ledger(root, tool, session_id, project, offered, collapsed: dict[str, list[str]] | None = None)`：事件多一鍵 `"collapsed": {kept_sl_id: [sl_id, ...]}`（空 dict 時省略）；`_publish_offered`／`_record_offered` 透傳。
- 折疊只在 `flags.collapse_same_topic` 為 True 時；被折疊者**不**寫進 `offered`（沒 offer 就不能算 offered），只記 `collapsed` 供稽核；下一輪因為沒進 seen，若最新者已 offer 過，舊者仍會被同一規則再折疊（因為 `hits` 每輪都含最新者）——測試鎖住此行為。
- families 來源：`load_projects_config(default_projects_path(root)).families`（best-effort，失敗視為 `()`）。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_moc_search.py（追加）
def test_search_returns_captured_at(tmp_path):
    k = tmp_path / "knowledge" / "proj"; k.mkdir(parents=True)
    (k / "a.md").write_text("---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
                            "title: Alpha\ncaptured_at: '2026-06-29T00:00:00Z'\n---\nalpha body\n", encoding="utf-8")
    S.build_index(tmp_path, link_weights={})
    hit = S.search(tmp_path, "alpha", project="proj", limit=5, include_decayed=False)[0]
    assert hit["captured_at"] == "2026-06-29T00:00:00Z"
```

```python
# tests/test_shortlist_collapse.py
import json
from pathlib import Path
from paulsha_hippo.moc import search as S
from paulsha_hippo.hooks import _shortlist_common as SC
from paulsha_hippo import runtime_flags as rf


def _note(mr: Path, sid: str, title: str, at: str, body: str = "flash layout 說明"):
    k = mr / "knowledge" / "proj"; k.mkdir(parents=True, exist_ok=True)
    (k / f"{sid}.md").write_text(f"---\nmemory_layer: knowledge\nslice_id: {sid}\nproject: proj\n"
                                 f"title: {title}\ncaptured_at: '{at}'\n---\n{body}\n", encoding="utf-8")


def _seed(mr: Path):
    _note(mr, "sl-old0000000000001", "Flash Layout", "2026-08-17T00:00:00Z")
    _note(mr, "sl-new0000000000002", "flash layout", "2026-08-21T00:00:00Z")
    _note(mr, "sl-oth0000000000003", "Release Button DIO24", "2026-08-01T00:00:00Z", body="flash layout 與 release button")
    S.build_index(mr, link_weights={})


def _ledger(mr: Path):
    return [json.loads(l) for l in (mr / "runtime" / "ledger" / "offered.jsonl").read_text().splitlines()]


def test_collapse_keeps_newest_and_records_collapsed(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "s1", cwd="/x", prompt="flash layout")
    assert "sl-new0000000000002" in out and "sl-old0000000000001" not in out
    ev = _ledger(tmp_path)[0]
    assert [o["sl_id"] for o in ev["offered"]] == ["sl-new0000000000002", "sl-oth0000000000003"]
    assert ev["collapsed"] == {"sl-new0000000000002": ["sl-old0000000000001"]}


def test_collapsed_note_not_offered_next_round_either(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    _seed(tmp_path)
    SC.build_shortlist_and_record(tmp_path, "claude-code", "s1", cwd="/x", prompt="flash layout")
    out2 = SC.build_shortlist_and_record(tmp_path, "claude-code", "s1", cwd="/x", prompt="flash layout")
    assert "sl-old0000000000001" not in out2


def test_flag_off_restores_parallel_offer(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    monkeypatch.setattr(SC, "load_flags", lambda: rf.HygieneFlags(collapse_same_topic=False))
    _seed(tmp_path)
    out = SC.build_shortlist_and_record(tmp_path, "claude-code", "s2", cwd="/x", prompt="flash layout")
    assert "sl-old0000000000001" in out and "sl-new0000000000002" in out
    assert "collapsed" not in _ledger(tmp_path)[0]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_moc_search.py tests/test_shortlist_collapse.py -q -k "captured_at or collapse"`
Expected: FAIL（`KeyError: 'captured_at'`；舊者仍在 shortlist）

- [ ] **Step 3: 實作**

`moc/search.py::search`：SELECT 改為 `"... m.link_weight, m.active, m.path, m.captured_at{usage_select} "`；`_row_read_count`：`if len(row) <= 8: return 0` / `value = row[8]`；回傳 dict 加 `"captured_at": r[7]`。

`_shortlist_common.py`：頂部 `from paulsha_hippo.topic import collapse_same_topic`、`from paulsha_hippo.importer.config import load_projects_config, default_projects_path`；新 helper：

```python
def _families(root: Path) -> tuple:
    try:
        return tuple(load_projects_config(default_projects_path(root)).families)
    except Exception:
        return ()
```

`build_shortlist_and_record` 的 `seen = ...` 之後、`claim = ...` 之前：

```python
            flags = load_flags()
            collapsed: dict[str, list[str]] = {}
            pool = hits
            if flags.collapse_same_topic:
                pool, collapsed = collapse_same_topic(hits, families=_families(root))
            claim = [h for h in pool if h.get("slice_id") and h["slice_id"] not in seen][:SHORTLIST_K]
            collapsed = {k: v for k, v in collapsed.items() if any(h["slice_id"] == k for h in claim)}
```

（Task 6 已在下方建立 `flags`；合併為同一次 `load_flags()` 呼叫。）`_append_offered_ledger` 加 `collapsed` 參數：`if collapsed: ev["collapsed"] = collapsed`；`_publish_offered`／`_record_offered` 簽名加 `collapsed=None` 透傳；`build_shortlist_and_record` 呼叫 `_publish_offered(..., collapsed=collapsed)`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_moc_search.py tests/test_shortlist_collapse.py tests/test_shortlist_common.py tests/test_search_scoped_corpus.py tests/test_usage_funnel.py tests/test_hippo_memory_kpi_skill.py -q`
Expected: all passed（KPI／funnel 讀 `offered` 欄位，忽略未知鍵 `collapsed`）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/moc/search.py paulsha_hippo/hooks/_shortlist_common.py tests/test_moc_search.py tests/test_shortlist_collapse.py
git commit -m "feat(shortlist): 同主題折疊只留最新，offered ledger 記 collapsed"
```

---

### Task 9: fix 1c — `cites` 結構化抽取（新 note 路徑）

**Files:**
- Modify: `paulsha_hippo/atomizer/slice_frontmatter.py:18-24`（`_SCALAR_ORDER` 加 `cites`）、`:127-160`（`build_from_proposal` 加 `cites`）、新函式 `extract_cites`
- Test: `tests/test_slice_frontmatter.py`（追加）

**Interfaces:**
- `slice_frontmatter.CITE_RE = re.compile(r"(?<![\w/.-])([\w./-]+\.(?:md|py|c|h|cpp|hpp|lds|syscfg|yml|yaml|sh|json|toml|cmake|txt)):(\d{1,6})\b")`
- `extract_cites(body: str, *, limit: int = 32) -> list[dict]` → `[{"path": str, "line": int}]`，保序去重（以 (path, line) 為鍵）。
- `build_from_proposal` frontmatter 加 `"cites": extract_cites(body)`（空 list 也寫，`render` 對空 list 輸出 `cites: []`）。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_slice_frontmatter.py（追加）
def test_extract_cites_dedup_order_limit():
    body = ("Layout 定義在 `examples/apps/X/octopus.lds:35`；NVS 見 src/CC2674.syscfg:131。"
            "README-ARC.md:108 已不符；再提 src/CC2674.syscfg:131 一次。")
    assert slice_frontmatter.extract_cites(body) == [
        {"path": "examples/apps/X/octopus.lds", "line": 35},
        {"path": "src/CC2674.syscfg", "line": 131},
        {"path": "README-ARC.md", "line": 108},
    ]
    many = " ".join(f"f{i}.py:{i}" for i in range(50))
    assert len(slice_frontmatter.extract_cites(many)) == 32
    assert slice_frontmatter.extract_cites("時間 12:30 與 http://x:80 都不是引用") == []


def test_build_from_proposal_writes_cites_and_round_trips():
    proposal = SliceProposal(title="t", artifact_kind="report", project="paulshaclaw", tags=[],
                             body="見 README-ARC.md:108。", source_fragment_indices=[0], relations=[])
    slice_ = slice_frontmatter.build_from_proposal(proposal, _SESSION_META)
    assert slice_.frontmatter["cites"] == [{"path": "README-ARC.md", "line": 108}]
    fm, _ = fio.read(slice_frontmatter.render(slice_))
    assert fm["cites"] == [{"path": "README-ARC.md", "line": 108}]
    assert slice_frontmatter.validate(slice_.frontmatter, slice_.body) == [] or \
        all("cites" not in e for e in slice_frontmatter.validate(slice_.frontmatter, slice_.body))
```

（`SliceProposal` 建構參數以 `atomizer/llm_output.py` 的 dataclass 為準。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_slice_frontmatter.py -q -k cites`
Expected: FAIL（`AttributeError: extract_cites`）

- [ ] **Step 3: 實作**

```python
CITE_RE = re.compile(r"(?<![\w/.-])([\w./-]+\.(?:md|py|c|h|cpp|hpp|lds|syscfg|yml|yaml|sh|json|toml|cmake|txt)):(\d{1,6})\b")


def extract_cites(body: str, *, limit: int = 32) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for m in CITE_RE.finditer(body or ""):
        key = (m.group(1), int(m.group(2)))
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": key[0], "line": key[1]})
        if len(out) >= limit:
            break
    return out
```

`_SCALAR_ORDER` 在 `"source_fragments"` 之後加 `"cites"`；`build_from_proposal` 加 `"cites": extract_cites(body)`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_slice_frontmatter.py tests/test_atomizer_pipeline.py tests/test_moc_frontmatter_io.py tests/test_publication_transaction.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/atomizer/slice_frontmatter.py tests/test_slice_frontmatter.py
git commit -m "feat(atomizer): body 內 path:line 引用抽成 cites frontmatter"
```

---

### Task 10: fix 1b/1c migration — `hippo knowledge backfill-provenance`

**Files:**
- Modify: `paulsha_hippo/importer/_git.py`（加 `git_rev_before`）
- Create: `paulsha_hippo/provenance_backfill.py`
- Modify: `paulsha_hippo/cli.py`（`knowledge_subparsers` 加 `backfill-provenance`）
- Test: `tests/test_backfill_provenance.py`（新）

**Interfaces:**
- `_git.git_rev_before(toplevel, iso_ts: str) -> str | None`：`git rev-list -1 --before=<iso_ts> HEAD`。
- `provenance_backfill.run(memory_root: Path, *, apply: bool, project: str | None = None, rev_before=_git.git_rev_before, toplevel=_git.git_toplevel) -> tuple[dict, list[str]]`：
  - 走訪 `knowledge/**/*.md`（跳過 `-moc.md`、`memory_layer != "knowledge"`、`--project` 不符者）；
  - **commit**：`provenance.commit in ("", "_unknown")` 且 `provenance.path` 指向可讀 JSON → 取 `cwd` 與 `ended_at or timestamp`；`toplevel(cwd)` 非 None 且 `rev_before(toplevel, ts)` 非 None → 候選 `{commit, commit_source: "backfill-approx"}`；否則記 `reasons[<why>] += 1`（`no-archive` / `no-cwd` / `not-a-repo` / `no-commit-before-ts` / `no-timestamp`）。
  - **cites**：`extract_cites(body)` 與既有 `cites` 不同（或缺）→ 候選。
  - summary：`{"scanned", "commit_candidates", "commit_reasons": {...}, "cites_candidates", "updated", "details": [{"path", "commit", "cites_count"}]}`；apply 用 `fio.update(path, {"provenance": merged_prov, "cites": cites})`（只放有變的鍵）。
- CLI：`hippo knowledge backfill-provenance --memory-root R [--dry-run|--apply] [--project SLUG]`，印 JSON summary，warnings 到 stderr、有 warning exit 1（比照 `_normalize_tags`）。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_backfill_provenance.py
import json
from pathlib import Path
from paulsha_hippo import provenance_backfill as pb
from paulsha_hippo.moc import frontmatter_io as fio

_FM = ("---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: proj\ntitle: T\ncaptured_at: \"2026-08-17T03:32:41Z\"\n"
       "supersedes: []\nprovenance:\n  repo: r\n  commit: {commit}\n  path: {archive}\n"
       "distiller:\n  profile_id: claude\n---\n")


def _note(mr: Path, sid: str, commit: str, archive: str, body: str) -> Path:
    p = mr / "knowledge" / "proj" / f"t--{sid}.md"; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_FM.format(sid=sid, commit=commit, archive=archive) + body, encoding="utf-8")
    return p


def _archive(mr: Path, name: str, payload: dict) -> str:
    a = mr / "archive" / "queue" / "2026-08" / name; a.parent.mkdir(parents=True, exist_ok=True)
    a.write_text(json.dumps(payload)); return str(a)


def _fakes(head="c" * 40):
    return dict(toplevel=lambda cwd: "/repo" if cwd == "/repo" else None,
                rev_before=lambda top, ts: head if ts else None)


def test_dry_run_reports_and_writes_nothing(tmp_path):
    a = _archive(tmp_path, "s1.json", {"cwd": "/repo", "ended_at": "2026-08-17T03:00:00+00:00"})
    p1 = _note(tmp_path, "sl-1", "_unknown", a, "README-ARC.md:108 已不符\n")
    p2 = _note(tmp_path, "sl-2", "_unknown", str(tmp_path / "missing.json"), "無引用\n")
    before = (p1.read_bytes(), p2.read_bytes())
    summary, warnings = pb.run(tmp_path, apply=False, **_fakes())
    assert summary["commit_candidates"] == 1 and summary["commit_reasons"] == {"no-archive": 1}
    assert summary["cites_candidates"] == 1 and summary["updated"] == 0
    assert (p1.read_bytes(), p2.read_bytes()) == before and warnings == []


def test_apply_is_idempotent_and_marks_source(tmp_path):
    a = _archive(tmp_path, "s1.json", {"cwd": "/repo", "ended_at": "2026-08-17T03:00:00+00:00"})
    p = _note(tmp_path, "sl-1", "_unknown", a, "見 README-ARC.md:108。\n")
    s1, _ = pb.run(tmp_path, apply=True, **_fakes())
    assert s1["updated"] == 1
    fm, body = fio.read(p.read_text(encoding="utf-8"))
    assert fm["provenance"]["commit"] == "c" * 40 and fm["provenance"]["commit_source"] == "backfill-approx"
    assert fm["cites"] == [{"path": "README-ARC.md", "line": 108}] and body == "見 README-ARC.md:108。\n"
    s2, _ = pb.run(tmp_path, apply=False, **_fakes())
    assert s2["commit_candidates"] == 0 and s2["cites_candidates"] == 0
    first = p.read_bytes()
    pb.run(tmp_path, apply=True, **_fakes())
    assert p.read_bytes() == first


def test_known_commit_is_never_overwritten(tmp_path):
    a = _archive(tmp_path, "s1.json", {"cwd": "/repo", "ended_at": "2026-08-17T03:00:00+00:00"})
    p = _note(tmp_path, "sl-1", "2a655c3", a, "x\n")
    summary, _ = pb.run(tmp_path, apply=True, **_fakes())
    assert summary["commit_candidates"] == 0
    assert fio.read(p.read_text())[0]["provenance"]["commit"] == "2a655c3"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_backfill_provenance.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作**

`_git.py`：

```python
def git_rev_before(toplevel: str | Path | None, iso_ts: str | None) -> Optional[str]:
    """`rev-list -1 --before=<ts> HEAD`：近似值（分支可能不同），呼叫端須標 backfill-approx。"""
    if not toplevel or not iso_ts:
        return None
    return _run_git(["rev-list", "-1", f"--before={iso_ts}", "HEAD"], cwd=toplevel) or None
```

```python
# paulsha_hippo/provenance_backfill.py
"""一次性回填 provenance.commit（近似）與 cites（fix 1b/1c migration）。仿 tags_migration。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Callable

from paulsha_hippo.atomizer.slice_frontmatter import extract_cites
from paulsha_hippo.importer import _git
from paulsha_hippo.moc import frontmatter_io as fio

_UNKNOWN = ("", "_unknown", None)


def _archive_meta(path_value: object) -> tuple[dict | None, str]:
    if not isinstance(path_value, str) or not path_value:
        return None, "no-archive"
    p = Path(path_value)
    if not p.is_file():
        return None, "no-archive"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None, "no-archive"
    return (data if isinstance(data, dict) else None), ("no-archive" if not isinstance(data, dict) else "")


def run(memory_root: Path | str, *, apply: bool = False, project: str | None = None,
        rev_before: Callable = _git.git_rev_before, toplevel: Callable = _git.git_toplevel) -> tuple[dict, list[str]]:
    root = Path(memory_root)
    knowledge = root / "knowledge"
    summary = {"scanned": 0, "commit_candidates": 0, "commit_reasons": {}, "cites_candidates": 0,
               "updated": 0, "details": []}
    warnings: list[str] = []
    if not knowledge.is_dir():
        return summary, warnings
    for path in sorted(knowledge.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, body = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"Failed to read {path}: {exc}"); continue
        if fm.get("memory_layer") != "knowledge" or (project and fm.get("project") != project):
            continue
        summary["scanned"] += 1
        updates: dict = {}
        prov = dict(fm.get("provenance") or {}) if isinstance(fm.get("provenance"), dict) else {}
        if prov.get("commit") in _UNKNOWN:
            meta, why = _archive_meta(prov.get("path"))
            if meta is not None:
                cwd, ts = meta.get("cwd"), meta.get("ended_at") or meta.get("timestamp")
                top = toplevel(cwd) if cwd else None
                if not cwd:
                    why = "no-cwd"
                elif not ts:
                    why = "no-timestamp"
                elif not top:
                    why = "not-a-repo"
                else:
                    sha = rev_before(top, ts)
                    why = "" if sha else "no-commit-before-ts"
                    if sha:
                        updates["provenance"] = {**prov, "commit": sha, "commit_source": "backfill-approx"}
                        summary["commit_candidates"] += 1
            if why:
                summary["commit_reasons"][why] = summary["commit_reasons"].get(why, 0) + 1
        cites = extract_cites(body)
        if cites != (fm.get("cites") or []):
            updates["cites"] = cites
            summary["cites_candidates"] += 1
        if not updates:
            continue
        summary["details"].append({"path": str(path.relative_to(root)),
                                   "commit": updates.get("provenance", {}).get("commit"),
                                   "cites_count": len(updates.get("cites", []))})
        if apply:
            try:
                fio.update(path, updates); summary["updated"] += 1
            except Exception as exc:
                warnings.append(f"Failed to update {path}: {exc}")
    return summary, warnings
```

`cli.py`：仿 `normalize_tags_p` 加 `backfill-provenance`（`--memory-root` 必填、`--dry-run|--apply` 互斥、`--project` 可選）與 `_backfill_provenance`（呼叫 `provenance_backfill.run`，輸出格式同 `_normalize_tags`）。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_backfill_provenance.py tests/test_git_helper.py tests/test_cli.py -q`
Expected: all passed

- [ ] **Step 5: 對 live 記憶庫做 dry-run（唯讀）並把數字貼進 PR**

Run: `hippo knowledge backfill-provenance --memory-root ~/.agents/memory --dry-run | python3 -c "import json,sys; d=json.load(sys.stdin); print({k:v for k,v in d.items() if k!='details'})"`
Expected: `commit_candidates` ≈ 3,218 以下（依 archive 是否還在、cwd 是否存在而減）、`cites_candidates` ≈ 159；不寫任何檔。**`--apply` 只在使用者看過 dry-run 後執行。**

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/provenance_backfill.py paulsha_hippo/importer/_git.py paulsha_hippo/cli.py tests/test_backfill_provenance.py
git commit -m "feat(knowledge): backfill-provenance 一次性回填近似 commit 與 cites"
```

---

### Task 11: fix 5 — follow-up ledger（抽取／fold／verify）＋CLI

**Files:**
- Create: `paulsha_hippo/followups.py`
- Modify: `paulsha_hippo/cli.py`（`memory_subparsers` 加 `followups` 群）
- Test: `tests/test_followups_extract.py`、`tests/test_followups_verify.py`（新）

**Interfaces:**
- 常數 `ACTIONABLE_RE = re.compile(r"需要更新|需加|缺口|TODO|should be updated|尚未修|需修正|待補")`；`STALE_VALUE_RE = re.compile(r"`([^`]{1,64})`|(\d[\d,\.]{2,})")`（先反引號、後數字）。
- `extract_followups(*, slice_id: str, project: str, body: str, cites: list[dict]) -> list[dict]`：逐行；行含 actionable pattern → 在該行、前一行、後一行找第一個 `path:line`（用 `slice_frontmatter.CITE_RE`）；產 `{"id": "fu-"+sha256(f"{slice_id}|{path}:{line}|{claim}")[:16], "slice_id", "project", "target": {"path","line"} | None, "expected_stale": str | None, "claim": <該行 strip>, "source": "regex"}`；`cites` 只作 fallback（行內找不到時取 cites[0]，若有）。
- ledger `runtime/ledger/followups.jsonl`：事件 `{"ts", "id", "event": "opened"|"verified-open"|"resolved-in-source"|"closed-manual"|"unverifiable", ...首筆 opened 帶完整 followup 欄位, "detail": {...}}`。
- `append_event(root, event: dict, *, now: str) -> None`（append＋flush＋fsync，比照 `_append_offered_ledger`）；`fold(root) -> dict[id, dict]`（最後一筆事件為狀態，並保留 opened 時的欄位）。
- `extract_all(root, *, apply: bool, project: str | None = None, now: str) -> dict`：走訪 knowledge notes，對每個 followup，若 `fold` 中已有同 id → 跳過（冪等）；apply 時 append `opened`。summary `{"scanned","candidates","with_target","opened"}`。
- `verify(root, *, roots_by_project: dict[str, tuple[str, ...]], now: str, project: str | None = None, window: int = 2) -> dict`：對 fold 後狀態 ∈ {opened, verified-open} 且 `target` 非 None 者：路徑絕對 → 直接；相對 → 依序試 `roots_by_project[project]` 各 root；讀 `line±window` 行（1-based，越界裁切）；`expected_stale` 為 None → `unverifiable`；仍在 → `verified-open`；不在 → `resolved-in-source`；檔案不存在／無 root → `unverifiable`。**唯讀**：不寫 repo 檔案。summary `{"checked","verified_open","resolved","unverifiable"}`。
- `close(root, fid: str, *, reason: str, now: str) -> bool`（存在才 append `closed-manual`）。
- `open_count(root, project: str) -> int`：fold 後 state ∈ {opened, verified-open} 且 project 相符。
- CLI：`hippo followups list --memory-root R [--project P] [--status open|all] [--json]`；`hippo followups verify --memory-root R [--project P]`（roots 由 `load_projects_config(default_projects_path(R))`）；`hippo followups close <id> --memory-root R --reason TEXT`；`hippo followups extract --memory-root R [--dry-run|--apply] [--project P]`。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_followups_extract.py
from paulsha_hippo import followups as fu

BODY = ("## Flash\n表格略。\n\nREADME-ARC.md 記載的舊 FLASH 數字 `133,604 B` 已與 fresh build 的 `133,372 B` 不符，"
        "需要更新（`README-ARC.md:108`）。\n\nNVS 未顯式保留，未來需加 linker reservation。\n")


def test_extract_target_and_expected_stale():
    items = fu.extract_followups(slice_id="sl-1", project="ot-ti-mirror", body=BODY, cites=[])
    assert len(items) == 2
    a, b = items
    assert a["target"] == {"path": "README-ARC.md", "line": 108} and a["expected_stale"] == "133,604 B"
    assert b["target"] is None and b["claim"].startswith("NVS 未顯式保留")
    assert a["id"].startswith("fu-") and len(a["id"]) == 19


def test_extract_is_deterministic_and_uses_cites_fallback():
    x = fu.extract_followups(slice_id="sl-1", project="p", body="需加 guard。\n", cites=[{"path": "a.py", "line": 3}])
    y = fu.extract_followups(slice_id="sl-1", project="p", body="需加 guard。\n", cites=[{"path": "a.py", "line": 3}])
    assert x == y and x[0]["target"] == {"path": "a.py", "line": 3}


def test_extract_all_opens_once(tmp_path):
    k = tmp_path / "knowledge" / "ot-ti-mirror"; k.mkdir(parents=True)
    (k / "n--sl-1.md").write_text("---\nslice_id: sl-1\nmemory_layer: knowledge\nproject: ot-ti-mirror\n---\n" + BODY, encoding="utf-8")
    s1 = fu.extract_all(tmp_path, apply=True, now="2026-08-25T00:00:00Z")
    s2 = fu.extract_all(tmp_path, apply=True, now="2026-08-25T00:01:00Z")
    assert s1["opened"] == 2 and s2["opened"] == 0
    assert len((tmp_path / "runtime" / "ledger" / "followups.jsonl").read_text().splitlines()) == 2
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 2
```

```python
# tests/test_followups_verify.py
from pathlib import Path
from paulsha_hippo import followups as fu

NOW = "2026-08-25T00:00:00Z"


def _open(root: Path, fid: str, path: str, line: int, stale: str | None, project="ot-ti-mirror"):
    fu.append_event(root, {"id": fid, "event": "opened", "slice_id": "sl-1", "project": project,
                           "target": {"path": path, "line": line}, "expected_stale": stale, "claim": "c", "source": "regex"}, now=NOW)


def test_verify_state_transitions(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    readme = repo / "README-ARC.md"
    readme.write_text("\n".join(f"line {i}" for i in range(1, 108)) + "\nmemory report (FLASH 133,604 B)\nx\n", encoding="utf-8")
    _open(tmp_path, "fu-a", "README-ARC.md", 108, "133,604")   # 實際在第 108 行 → verified-open
    _open(tmp_path, "fu-b", "README-ARC.md", 110, "133,604")   # 漂移 ±2 內 → 仍找到
    _open(tmp_path, "fu-c", "README-ARC.md", 50, "133,604")    # 不在 → resolved
    _open(tmp_path, "fu-d", "nope.md", 1, "x")                 # 檔案不存在 → unverifiable
    _open(tmp_path, "fu-e", "README-ARC.md", 108, None)        # 無預期值 → unverifiable
    mtime = readme.stat().st_mtime_ns
    s = fu.verify(tmp_path, roots_by_project={"ot-ti-mirror": (str(repo),)}, now=NOW)
    assert s == {"checked": 5, "verified_open": 2, "resolved": 1, "unverifiable": 2}
    st = fu.fold(tmp_path)
    assert st["fu-a"]["state"] == "verified-open" and st["fu-c"]["state"] == "resolved-in-source"
    assert readme.stat().st_mtime_ns == mtime
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 2


def test_close_manual_and_missing_root(tmp_path):
    _open(tmp_path, "fu-a", "README-ARC.md", 1, "x", project="unknown-proj")
    s = fu.verify(tmp_path, roots_by_project={}, now=NOW)
    assert s["unverifiable"] == 1
    assert fu.close(tmp_path, "fu-a", reason="moved to generated", now=NOW) is True
    assert fu.close(tmp_path, "fu-zz", reason="x", now=NOW) is False
    assert fu.fold(tmp_path)["fu-a"]["state"] == "closed-manual"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_followups_extract.py tests/test_followups_verify.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 `paulsha_hippo/followups.py`**

```python
"""Follow-up ledger（fix 5）：可行動語句 → runtime/ledger/followups.jsonl；verify 唯讀重查 file:line。"""
from __future__ import annotations
import hashlib, json, os, re
from pathlib import Path
from typing import Iterable

from paulsha_hippo.atomizer.slice_frontmatter import CITE_RE
from paulsha_hippo.moc import frontmatter_io as fio

ACTIONABLE_RE = re.compile(r"需要更新|需加|缺口|TODO|should be updated|尚未修|需修正|待補")
STALE_VALUE_RE = re.compile(r"`([^`]{1,64})`|(\d[\d,\.]{2,})")
OPEN_STATES = ("opened", "verified-open")
VALID_EVENTS = ("opened", "verified-open", "resolved-in-source", "closed-manual", "unverifiable")


def ledger_path(root: Path) -> Path:
    return Path(root) / "runtime" / "ledger" / "followups.jsonl"


def _fid(slice_id: str, target: dict | None, claim: str) -> str:
    t = f"{target['path']}:{target['line']}" if target else "-"
    return "fu-" + hashlib.sha256(f"{slice_id}|{t}|{claim}".encode("utf-8")).hexdigest()[:16]


def _cite_in(lines: list[str], i: int) -> dict | None:
    for j in (i, i - 1, i + 1):
        if 0 <= j < len(lines):
            m = CITE_RE.search(lines[j])
            if m:
                return {"path": m.group(1), "line": int(m.group(2))}
    return None


def extract_followups(*, slice_id: str, project: str, body: str, cites: list[dict]) -> list[dict]:
    lines = (body or "").splitlines()
    out: list[dict] = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or not ACTIONABLE_RE.search(line):
            continue
        target = _cite_in(lines, i) or (dict(cites[0]) if cites else None)
        m = STALE_VALUE_RE.search(line)
        stale = (m.group(1) or m.group(2)) if m else None
        out.append({"id": _fid(slice_id, target, line), "slice_id": slice_id, "project": project,
                    "target": target, "expected_stale": stale, "claim": line, "source": "regex"})
    return out


def append_event(root: Path, event: dict, *, now: str) -> None:
    if event.get("event") not in VALID_EVENTS:
        raise ValueError(f"invalid followup event: {event.get('event')}")
    p = ledger_path(root); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now, **event}, ensure_ascii=False) + "\n"); fh.flush(); os.fsync(fh.fileno())


def fold(root: Path) -> dict[str, dict]:
    state: dict[str, dict] = {}
    try:
        raw = ledger_path(root).read_text(encoding="utf-8")
    except OSError:
        return state
    for line in raw.splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        fid = ev.get("id")
        if not fid:
            continue
        cur = state.setdefault(fid, {})
        if ev.get("event") == "opened":
            cur.update({k: v for k, v in ev.items() if k not in ("event", "ts")})
        cur["state"] = ev.get("event"); cur["updated_at"] = ev.get("ts")
    return state


def open_count(root: Path, project: str) -> int:
    return sum(1 for s in fold(root).values() if s.get("state") in OPEN_STATES and s.get("project") == project)


def extract_all(root: Path, *, apply: bool, now: str, project: str | None = None) -> dict:
    root = Path(root); known = fold(root)
    summary = {"scanned": 0, "candidates": 0, "with_target": 0, "opened": 0}
    knowledge = root / "knowledge"
    for path in sorted(knowledge.rglob("*.md")) if knowledge.is_dir() else []:
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, body = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if fm.get("memory_layer") != "knowledge" or (project and fm.get("project") != project):
            continue
        summary["scanned"] += 1
        cites = fm.get("cites") if isinstance(fm.get("cites"), list) else []
        for item in extract_followups(slice_id=str(fm.get("slice_id", "")), project=str(fm.get("project", "")),
                                      body=body, cites=[c for c in cites if isinstance(c, dict)]):
            summary["candidates"] += 1
            summary["with_target"] += int(item["target"] is not None)
            if item["id"] in known:
                continue
            if apply:
                append_event(root, {**item, "event": "opened"}, now=now); known[item["id"]] = item
                summary["opened"] += 1
    return summary


def _resolve(target: dict, project: str, roots_by_project: dict) -> Path | None:
    p = Path(target["path"])
    if p.is_absolute():
        return p if p.is_file() else None
    for r in roots_by_project.get(project, ()):
        cand = Path(r) / p
        if cand.is_file():
            return cand
    return None


def verify(root: Path, *, roots_by_project: dict, now: str, project: str | None = None, window: int = 2) -> dict:
    summary = {"checked": 0, "verified_open": 0, "resolved": 0, "unverifiable": 0}
    for fid, s in fold(root).items():
        if s.get("state") not in OPEN_STATES or not s.get("target") or (project and s.get("project") != project):
            continue
        summary["checked"] += 1
        f = _resolve(s["target"], str(s.get("project", "")), roots_by_project)
        stale = s.get("expected_stale")
        if f is None or not stale:
            ev = "unverifiable"
        else:
            try:
                lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                lines = None
            if lines is None:
                ev = "unverifiable"
            else:
                n = int(s["target"]["line"]); lo, hi = max(1, n - window), min(len(lines), n + window)
                ev = "verified-open" if any(stale in lines[i - 1] for i in range(lo, hi + 1)) else "resolved-in-source"
        append_event(root, {"id": fid, "event": ev, "detail": {"path": str(f) if f else None}}, now=now)
        summary[{"verified-open": "verified_open", "resolved-in-source": "resolved", "unverifiable": "unverifiable"}[ev]] += 1
    return summary


def close(root: Path, fid: str, *, reason: str, now: str) -> bool:
    if fid not in fold(root):
        return False
    append_event(root, {"id": fid, "event": "closed-manual", "detail": {"reason": reason}}, now=now)
    return True
```

`cli.py`：`followups_p = memory_subparsers.add_parser("followups", help="follow-up ledger：list/verify/close/extract")`，四個子命令依 Interfaces 接線；`verify` 的 roots：`{p.slug: p.roots for p in load_projects_config(default_projects_path(root)).projects}`；`now` 一律 `datetime.now(timezone.utc).isoformat().replace("+00:00","Z")`；`list` 預設印表格 `id  state  project  target  claim[:60]`，`--json` 印 fold 結果。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_followups_extract.py tests/test_followups_verify.py tests/test_cli.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/followups.py paulsha_hippo/cli.py tests/test_followups_extract.py tests/test_followups_verify.py
git commit -m "feat(followups): 可行動語句 ledger、唯讀 verify 與 CLI"
```

---
### Task 12: fix 5 — dream 階段、wakeup brief 一行、KPI 欄位

**Files:**
- Modify: `paulsha_hippo/dream/orchestrator.py:77-96`（`followups_fn` 可選階段）
- Modify: `paulsha_hippo/dream/cli.py:127-191`（定義 `followups_fn` 並傳入）
- Modify: `paulsha_hippo/wakeup/builder.py:69-100`、`:277-330`（brief 末尾一行）
- Modify: `custom-skills/hippo-memory-kpi/scripts/report.py:512-600`（`followups` 區塊）
- Test: `tests/test_dream_orchestrator.py`、`tests/test_dream_cli.py`、`tests/test_wakeup_builder.py`、`tests/test_hippo_memory_kpi_skill.py`（各追加）

**Interfaces:**
- `run_dream(..., followups_fn: Callable[[], dict] | None = None)`：在 janitor 之後、moc 之前 `_run_pass("followups", followups_fn, passes, errors)`；`followups_fn` 為 None 時不跑、`passes` 無該鍵。
- `dream/cli.py::followups_fn`：`flags.followups_enabled` 為 False 或 `args.dry_run` → `{"summary": {"skipped": "disabled"|"dry-run"}, "warnings": []}`；否則 `try: verify(...)` → `{"summary": s, "warnings": []}`；**任何例外** → `{"summary": {"error": processing.sanitize_error_text(str(exc))}, "warnings": []}`（不進 warnings、不 raise → dream 結果等級不受影響）。roots 同 Task 11 CLI。
- `build_brief(...)`：若 `load_flags().followups_enabled` 且 `followups.open_count(root, project) > 0`，在整段結果末尾附 `"\n## Follow-ups\n\n- open follow-ups：{n}（`hippo followups list --memory-root {root} --project {project}`）\n"`；該行長度先從 `char_budget` 扣除再進既有配置邏輯；任何例外 → 不附行。
- KPI `build_report` 每個 window payload 加 `"followups": {"open": int, "resolved_in_source": int}`（open＝fold 後 state ∈ OPEN_STATES；resolved_in_source＝window 內 `resolved-in-source` 事件數）；`render_markdown` 加一行。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_dream_orchestrator.py（追加）
    def test_followups_pass_runs_between_janitor_and_moc_and_is_optional(self):
        calls: list[str] = []
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            res = orchestrator.run_dream(
                root, atomize_fn=lambda: (calls.append("a") or {}), janitor_fn=lambda: (calls.append("j") or {}),
                followups_fn=lambda: (calls.append("f") or {"summary": {"checked": 0}, "warnings": []}),
                moc_fn=lambda: (calls.append("m") or {}), now="2026-08-25T00:00:00Z")
            self.assertEqual(calls, ["a", "j", "f", "m"])
            self.assertEqual(res["status"], "ok")
            self.assertIn("followups", res["passes"])
            res2 = orchestrator.run_dream(root, atomize_fn=lambda: {}, janitor_fn=lambda: {}, now="2026-08-25T00:00:01Z")
            self.assertNotIn("followups", res2["passes"])
```

```python
# tests/test_dream_cli.py（追加在 DreamCliTests）
    def test_followups_failure_does_not_downgrade_dream(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _seed(root)
            with patch("paulsha_hippo.dream.cli.followups.verify", side_effect=RuntimeError("boom")), \
                 patch("paulsha_hippo.dream.cli.load_flags", return_value=runtime_flags.HygieneFlags()):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli.main(["dream", "run", "--memory-root", str(root), "--promoter", "identity"])
            out = json.loads(buf.getvalue())
            self.assertEqual(rc, 0)
            self.assertIn(out["status"], ("ok", "partial"))
            self.assertIn("error", out["passes"]["followups"])
            self.assertNotIn("boom", json.dumps(out["passes"]["followups"]))   # sanitize
```

（`dream run` 的實際 argv 以檔內既有測試 `test_dry_run_writes_nothing` 為準；若既有測試用 `--dry-run`，本測試不能用 dry-run。`status` 可能因既有 warnings 為 partial，斷言重點是 followups 失敗不會變 `failed`。）

```python
# tests/test_wakeup_builder.py（追加）
    def test_brief_appends_followups_line_only_when_open(self):
        from paulsha_hippo import followups as fu
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "p", "alpha")
            out0 = build_brief(root, "p", now="2026-08-25T00:00:00Z")
            self.assertNotIn("Follow-ups", out0)
            fu.append_event(root, {"id": "fu-1", "event": "opened", "slice_id": "sl-1", "project": "p",
                                   "target": {"path": "a.md", "line": 1}, "expected_stale": "x", "claim": "c", "source": "regex"},
                            now="2026-08-25T00:00:00Z")
            out1 = build_brief(root, "p", now="2026-08-25T00:00:00Z")
            self.assertIn("## Follow-ups", out1)
            self.assertIn("open follow-ups：1", out1)
            self.assertIn(f"--project p", out1)
            self.assertLessEqual(len(build_brief(root, "p", now="2026-08-25T00:00:00Z", char_budget=300)), 300)
```

```python
# tests/test_hippo_memory_kpi_skill.py（追加：report 含 followups 區塊）
def test_report_includes_followups_counts(tmp_path):
    from paulsha_hippo import followups as fu
    fu.append_event(tmp_path, {"id": "fu-1", "event": "opened", "slice_id": "s", "project": "p", "target": None,
                               "expected_stale": None, "claim": "c", "source": "regex"}, now="2026-08-24T00:00:00Z")
    fu.append_event(tmp_path, {"id": "fu-1", "event": "resolved-in-source"}, now="2026-08-24T01:00:00Z")
    fu.append_event(tmp_path, {"id": "fu-2", "event": "opened", "slice_id": "s", "project": "p", "target": None,
                               "expected_stale": None, "claim": "d", "source": "regex"}, now="2026-08-24T02:00:00Z")
    report = build_report(tmp_path, now=_parse("2026-08-25T00:00:00Z"))   # 依檔內既有 helper 命名
    assert report["windows"]["7d"]["followups"] == {"open": 1, "resolved_in_source": 1}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_dream_orchestrator.py tests/test_dream_cli.py tests/test_wakeup_builder.py tests/test_hippo_memory_kpi_skill.py -q -k followups`
Expected: FAIL（`unexpected keyword argument 'followups_fn'`、`AttributeError: paulsha_hippo.dream.cli has no attribute followups`、brief 無 Follow-ups、report 無 followups 鍵）

- [ ] **Step 3: 實作**

`dream/orchestrator.py::run_dream`：簽名加 `followups_fn: Callable[[], dict[str, Any]] | None = None`；在 `janitor_clean = ...` 後：

```python
    followups_clean = True
    if followups_fn is not None:
        followups_clean = _run_pass("followups", followups_fn, passes, errors)
```

`status` 判斷加入 `followups_clean`（其回傳永遠無 warnings，因此不會拉低等級；保留一致性）。

`dream/cli.py`：頂部 `from .. import followups`、`from ..runtime_flags import load_flags`、`from ..importer.config import load_projects_config, default_projects_path`、`from ..ledger import processing`；在 `moc_fn` 之前：

```python
        def followups_fn() -> dict[str, object]:
            if args.dry_run:
                return {"summary": {"skipped": "dry-run"}, "warnings": []}
            if not load_flags().followups_enabled:
                return {"summary": {"skipped": "disabled"}, "warnings": []}
            try:
                cfg = load_projects_config(default_projects_path(memory_root))
                roots = {p.slug: p.roots for p in cfg.projects}
                return {"summary": followups.verify(memory_root, roots_by_project=roots, now=now), "warnings": []}
            except Exception as exc:  # noqa: BLE001 — followups 失敗不得改變 dream 等級
                return {"summary": {"error": processing.sanitize_error_text(str(exc))}, "warnings": []}
```

`run_dream(..., followups_fn=followups_fn, ...)`。

`wakeup/builder.py::build_brief`：開頭（`project` 正規化後）：

```python
    followups_block = ""
    try:
        from paulsha_hippo.runtime_flags import load_flags
        from paulsha_hippo import followups as _fu
        if load_flags().followups_enabled:
            n = _fu.open_count(memory_root, project)
            if n > 0:
                followups_block = (f"\n## Follow-ups\n\n- open follow-ups：{n}"
                                   f"（`hippo followups list --memory-root {memory_root} --project {project}`）\n")
    except Exception:
        followups_block = ""
    char_budget = max(0, char_budget - len(followups_block))
```

所有 `return` 點改為 `return <既有結果> + followups_block`（用一個內部 `_finish(text)` helper 包住四個 return，避免漏）。

`report.py::build_report`：每個 window 加

```python
            "followups": _followups_metrics(root, start=start, now=now),
```

```python
def _followups_metrics(root: Path, *, start: datetime, now: datetime) -> dict[str, int]:
    try:
        from paulsha_hippo import followups as fu
        state = fu.fold(root)
        resolved = 0
        for line in fu.ledger_path(root).read_text(encoding="utf-8").splitlines() if fu.ledger_path(root).exists() else []:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("event") == "resolved-in-source" and _in_window(_parse_time(ev.get("ts")), start, now):
                resolved += 1
        return {"open": sum(1 for s in state.values() if s.get("state") in fu.OPEN_STATES),
                "resolved_in_source": resolved}
    except Exception:
        return {"open": 0, "resolved_in_source": 0}
```

`render_markdown` 表格加一列 `follow-ups open / resolved(window)`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_dream_orchestrator.py tests/test_dream_cli.py tests/test_dream_e2e.py tests/test_wakeup_builder.py tests/test_wakeup_cli.py tests/test_session_start_hooks.py tests/test_hippo_memory_kpi_skill.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/dream paulsha_hippo/wakeup/builder.py custom-skills/hippo-memory-kpi/scripts/report.py tests/test_dream_orchestrator.py tests/test_dream_cli.py tests/test_wakeup_builder.py tests/test_hippo_memory_kpi_skill.py
git commit -m "feat(followups): dream verify 階段、wakeup brief 計數、KPI 欄位"
```

---

### Task 13: fix 4 — `episodic_reason` 與發布前降層

**Files:**
- Modify: `paulsha_hippo/noise.py`（加 `episodic_reason`）
- Modify: `paulsha_hippo/atomizer/slice_frontmatter.py:16-24`（`_SCALAR_ORDER` 加 `episodic_reason`）、`:167-178`（`validate` 放寬）
- Modify: `paulsha_hippo/atomizer/config.py:55-80`（`episodic_filter: bool = True`）、`:319-`（解析頂層 `episodic_filter`）
- Modify: `paulsha_hippo/atomizer/pipeline.py:1082`（supersedes 之後、validate 之前降層）
- Modify: `paulsha_hippo/atomizer/skills/atomize-knowledge-slice.md:49-54`
- Test: `tests/test_noise.py`、`tests/test_atomizer_pipeline.py`、`tests/test_atomizer_config.py`、`tests/test_atomize_skill.py`（追加）

**Interfaces:**
- `noise.SESSION_STATE_RE = re.compile(r"尚未 ?commit|尚未 ?push|待 ?push|session 結束|本次修改僅限|目前狀態|handoff|下一步|session-handoff", re.IGNORECASE)`
- `noise.episodic_reason(title: object, body: str) -> str | None`：標題匹配 `^session-handoff` / 含 `handoff` / 以「狀態」結尾 → `"title:session-state"`；否則 `_content_lines(body)` 中命中 `SESSION_STATE_RE` 的比例 ≥ 0.5（且至少 1 行）→ `"body:session-state:{hits}/{total}"`；否則 None。**不改 `classify_noise`。**
- `validate()`：`memory_layer` 必須 ∈ `{"knowledge", "episodic"}`；`episodic` 時必須有 `episodic_reason`。
- pipeline：`if config.episodic_filter:` 對每個 promoted slice 呼叫 `episodic_reason(frontmatter.get("title"), body)`，命中 → `frontmatter["memory_layer"]="episodic"`、`frontmatter["episodic_reason"]=reason`；照常 validate／publish 到同目錄（index／MOC／wakeup／janitor 已以 `memory_layer != "knowledge"` 排除）。
- `AtomizerConfig.episodic_filter: bool = True`，`load_config` 讀頂層 `episodic_filter`（非 bool → `AtomizerConfigError`）。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_noise.py（追加）
class EpisodicReasonTests(unittest.TestCase):
    def test_mostly_status_lines_is_episodic(self):
        body = "## 狀態\n本次修改僅限 README-ARC.md。\nsession 結束時尚未 commit。\n下一步：開 PR。\n"
        self.assertEqual(episodic_reason("ot-ti-mirror 本地建置環境重現與 SOP", body), "body:session-state:3/3")
        self.assertFalse(classify_noise({}, body).is_noise)   # 不是 deletion-grade

    def test_status_minority_with_real_steps_is_kept(self):
        body = ("用 docker create --rm 建容器。\ncmake 統一 3.31.6。\n~/bin 必須先存在 PATH 才會生效。\n"
                "session 結束時尚未 commit。\n")
        self.assertIsNone(episodic_reason("SOP", body))

    def test_handoff_title_is_episodic(self):
        self.assertEqual(episodic_reason("session-handoff-2026-08-12", "任何內容\n"), "title:session-state")
        self.assertIsNone(episodic_reason("Release Button 現有角色", "DIO24 用途分析。\n"))
```

```python
# tests/test_atomizer_pipeline.py（追加）
    def test_session_state_finding_publishes_as_episodic_and_stays_out_of_index(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            promoter = llm_promoter.LLMPromoter(FakeAgentClient(json.dumps([{
                "title": "session-handoff-2026-08-12", "artifact_kind": "report", "project": "paulshaclaw",
                "tags": [], "body": "本次修改僅限 README。\nsession 結束時尚未 commit。\n",
                "source_fragment_indices": [0], "relations": []}])), skill_text="SKILL", known_projects=["paulshaclaw"])
            pipeline.run(root, config=cfg, config_hash=h, now="2026-08-25T00:00:00Z", promoter=promoter)
            note = next((root / "knowledge" / "paulshaclaw").glob("*.md"))
            fm, _ = pipeline._parse_frontmatter(note.read_text(encoding="utf-8"))
            self.assertEqual(fm["memory_layer"], "episodic")
            self.assertEqual(fm["episodic_reason"], "title:session-state")
            from paulsha_hippo.moc import search as S
            cov = S.build_index(root, link_weights={})
            self.assertEqual(cov["pool_excluded"].get("non-knowledge-layer:episodic"), 1)

    def test_episodic_filter_flag_off_keeps_knowledge_layer(self):
        from dataclasses import replace as _replace
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cfg = _replace(cfg, episodic_filter=False)
            promoter = llm_promoter.LLMPromoter(FakeAgentClient(json.dumps([{
                "title": "session-handoff-2026-08-12", "artifact_kind": "report", "project": "paulshaclaw",
                "tags": [], "body": "本次修改僅限 README。\nsession 結束時尚未 commit。\n",
                "source_fragment_indices": [0], "relations": []}])), skill_text="SKILL", known_projects=["paulshaclaw"])
            pipeline.run(root, config=cfg, config_hash=h, now="2026-08-25T00:00:00Z", promoter=promoter)
            note = next((root / "knowledge" / "paulshaclaw").glob("*.md"))
            fm, _ = pipeline._parse_frontmatter(note.read_text(encoding="utf-8"))
            self.assertEqual(fm["memory_layer"], "knowledge")
            self.assertNotIn("episodic_reason", fm)
```

```python
# tests/test_atomizer_config.py（追加）
def test_episodic_filter_default_true_and_parsed(tmp_path):
    cfg, _ = atomizer_config.load_config(override_path=None)
    assert cfg.episodic_filter is True
    d = tmp_path / "cfgdir"; d.mkdir()
    src = Path(atomizer_config.__file__).parent / "atomizer.yaml"
    (d / "atomizer.yaml").write_text(src.read_text().replace("episodic_filter: true", "episodic_filter: false"))
    assert atomizer_config.load_config(default_dir=d, override_path=None)[0].episodic_filter is False
```

```python
# tests/test_atomize_skill.py（追加）
def test_skill_excludes_session_state_statements():
    text = (Path(atomizer_config.__file__).parent / "skills" / "atomize-knowledge-slice.md").read_text(encoding="utf-8")
    assert "尚未 commit" in text and "session 結束" in text and "handoff" in text
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_noise.py tests/test_atomizer_pipeline.py tests/test_atomizer_config.py tests/test_atomize_skill.py -q -k "episodic or session_state"`
Expected: FAIL（`ImportError: episodic_reason`；`memory_layer == "knowledge"`；`AttributeError: episodic_filter`）

- [ ] **Step 3: 實作**

`noise.py`：

```python
SESSION_STATE_RE = re.compile(r"尚未 ?commit|尚未 ?push|待 ?push|session 結束|本次修改僅限|目前狀態|handoff|下一步|session-handoff", re.IGNORECASE)
_STATE_TITLE_RE = re.compile(r"^session-handoff|handoff|狀態$", re.IGNORECASE)
EPISODIC_RATIO = 0.5


def episodic_reason(title: object, body: str) -> str | None:
    """session 狀態句偵測：非 deletion-grade——命中只降層 episodic，不刪。"""
    if _STATE_TITLE_RE.search(str(title or "").strip()):
        return "title:session-state"
    lines = _content_lines((body or "").strip())
    if not lines:
        return None
    hits = sum(1 for line in lines if SESSION_STATE_RE.search(line))
    if hits and hits / len(lines) >= EPISODIC_RATIO:
        return f"body:session-state:{hits}/{len(lines)}"
    return None
```

`slice_frontmatter.py`：`_SCALAR_ORDER` 在 `"memory_layer"` 之後加 `"episodic_reason"`；`validate()`：

```python
    layer = frontmatter.get("memory_layer")
    if layer not in ("knowledge", "episodic"):
        errors.append("memory_layer must be 'knowledge' or 'episodic'")
    if layer == "episodic" and not frontmatter.get("episodic_reason"):
        errors.append("episodic slice requires episodic_reason")
```

`atomizer/config.py`：dataclass 加 `episodic_filter: bool = True`；`load_config` 在 `default_phase` 之後：

```python
    episodic_filter = config_data.get("episodic_filter", True)
    if not isinstance(episodic_filter, bool):
        raise AtomizerConfigError("episodic_filter must be a bool")
```

並傳入 `AtomizerConfig(...)`。

`atomizer/pipeline.py`（`promoted = _attach_unambiguous_supersedes(...)` 之後）：

```python
        if config.episodic_filter:
            demoted = []
            for slice_ in promoted:
                reason = episodic_reason(slice_.frontmatter.get("title"), slice_.body)
                if reason:
                    fm = dict(slice_.frontmatter, memory_layer="episodic", episodic_reason=reason)
                    slice_ = replace(slice_, frontmatter=fm)
                    LOGGER.info("atomize: demoted slice %s to episodic (%s)", slice_.slice_id, reason)
                demoted.append(slice_)
            promoted = demoted
```

（`from .noise import episodic_reason` 依檔內既有 `classify_noise` import 位置。）

skill `atomize-knowledge-slice.md` §2 CONCEPT_ANALYSIS 末尾加：

```
- 排除 session 進度／狀態陳述（例：尚未 commit、已 push、session 結束時、handoff、下一步、目前狀態、本次修改僅限）；
  它們描述「當時的狀態」而非可重用知識。若整個候選只剩狀態陳述，不要產生該 slice。
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_noise.py tests/test_atomizer_pipeline.py tests/test_atomizer_config.py tests/test_atomize_skill.py tests/test_slice_frontmatter.py tests/test_moc_census.py tests/test_atomizer_e2e.py tests/test_prune_noise.py -q`
Expected: all passed（`prune-noise` 對 episodic 內容仍不刪）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/noise.py paulsha_hippo/atomizer tests/test_noise.py tests/test_atomizer_pipeline.py tests/test_atomizer_config.py tests/test_atomize_skill.py
git commit -m "feat(atomizer): session 狀態句 slice 降層 episodic，不進索引不刪檔"
```

---

### Task 14: fix 4 migration — `hippo knowledge mark-episodic`

**Files:**
- Modify: `paulsha_hippo/moc/frontmatter_io.py:141`（`update(path, updates, *, remove=())`）
- Create: `paulsha_hippo/episodic_migration.py`
- Modify: `paulsha_hippo/cli.py`
- Test: `tests/test_moc_frontmatter_io.py`（追加）、`tests/test_mark_episodic.py`（新）

**Interfaces:**
- `fio.update(path, updates, *, remove: Iterable[str] = ())`：先 `fm.pop(k, None)` 再 `fm.update(updates)`。
- `episodic_migration.run(memory_root, *, apply: bool, now: str, project: str | None = None) -> tuple[dict, list[str]]`：對 `memory_layer == "knowledge"` 的 note 跑 `episodic_reason(title, body)`；候選寫 `details: [{"path","title","reason"}]`；apply → `fio.update(path, {"memory_layer": "episodic", "episodic_reason": reason})` ＋ `lifecycle.append_event(path=root/"runtime"/"ledger"/"lifecycle.jsonl", record_id=slice_id, event_type="archived", source="mark-episodic", reason="episodic", actor="hippo", ts=now)`。
- `episodic_migration.revert(memory_root, slice_id: str, *, now: str) -> bool`：找 `memory_layer == "episodic"` 的該 slice → `fio.update(path, {"memory_layer": "knowledge"}, remove=("episodic_reason",))` ＋ lifecycle `restored`。
- CLI：`hippo knowledge mark-episodic --memory-root R [--dry-run|--apply] [--project P] [--revert SLICE_ID]`。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_moc_frontmatter_io.py（追加）
def test_update_can_remove_keys(tmp_path):
    p = tmp_path / "n.md"; p.write_text("---\nslice_id: sl-a\nepisodic_reason: x\n---\nbody\n", encoding="utf-8")
    frontmatter_io.update(p, {"memory_layer": "knowledge"}, remove=("episodic_reason",))
    fm, body = frontmatter_io.read(p.read_text(encoding="utf-8"))
    assert "episodic_reason" not in fm and fm["memory_layer"] == "knowledge" and body == "body\n"
```

```python
# tests/test_mark_episodic.py
from pathlib import Path
from paulsha_hippo import episodic_migration as em
from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.moc import frontmatter_io as fio

NOW = "2026-08-25T00:00:00Z"


def _note(mr: Path, sid: str, title: str, body: str) -> Path:
    p = mr / "knowledge" / "proj" / f"n--{sid}.md"; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: proj\ntitle: \"{title}\"\n---\n{body}", encoding="utf-8")
    return p


def test_dry_run_lists_without_writing(tmp_path):
    a = _note(tmp_path, "sl-1", "session-handoff-2026-08-12", "x\n")
    b = _note(tmp_path, "sl-2", "Real SOP", "docker create --rm 建容器。\n")
    before = (a.read_bytes(), b.read_bytes())
    s, w = em.run(tmp_path, apply=False, now=NOW)
    assert s["pending"] == 1 and s["details"][0]["reason"] == "title:session-state" and s["updated"] == 0
    assert (a.read_bytes(), b.read_bytes()) == before and w == []


def test_apply_idempotent_and_revert(tmp_path):
    a = _note(tmp_path, "sl-1", "session-handoff-2026-08-12", "x\n")
    s1, _ = em.run(tmp_path, apply=True, now=NOW)
    assert s1["updated"] == 1
    fm, body = fio.read(a.read_text()); assert fm["memory_layer"] == "episodic" and body == "x\n"
    events = lifecycle.read_events(tmp_path / "runtime" / "ledger" / "lifecycle.jsonl")
    assert events[-1]["event_type"] == "archived" and events[-1]["reason"] == "episodic"
    s2, _ = em.run(tmp_path, apply=True, now=NOW)
    assert s2["pending"] == 0 and s2["updated"] == 0
    assert em.revert(tmp_path, "sl-1", now=NOW) is True
    fm, _ = fio.read(a.read_text()); assert fm["memory_layer"] == "knowledge" and "episodic_reason" not in fm
    assert lifecycle.read_events(tmp_path / "runtime" / "ledger" / "lifecycle.jsonl")[-1]["event_type"] == "restored"
    assert em.revert(tmp_path, "sl-nope", now=NOW) is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_moc_frontmatter_io.py tests/test_mark_episodic.py -q -k "remove or episodic"`
Expected: FAIL（`unexpected keyword 'remove'`；`ModuleNotFoundError`）

- [ ] **Step 3: 實作**

`frontmatter_io.update`：簽名加 `*, remove: Iterable[str] = ()`；`fm, body = read(...)` 後 `for key in remove: fm.pop(key, None)`，再 `fm.update(updates)`。

```python
# paulsha_hippo/episodic_migration.py
"""既存 session 狀態 note 降層 episodic（fix 4 migration）；可 revert。仿 tags_migration。"""
from __future__ import annotations
from pathlib import Path

from paulsha_hippo.ledger import lifecycle
from paulsha_hippo.moc import frontmatter_io as fio
from paulsha_hippo.noise import episodic_reason


def _lifecycle(root: Path) -> Path:
    return root / "runtime" / "ledger" / "lifecycle.jsonl"


def _iter(root: Path):
    knowledge = root / "knowledge"
    for path in sorted(knowledge.rglob("*.md")) if knowledge.is_dir() else []:
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, body = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        yield path, fm, body


def run(memory_root: Path | str, *, apply: bool, now: str, project: str | None = None) -> tuple[dict, list[str]]:
    root = Path(memory_root)
    summary = {"scanned": 0, "pending": 0, "updated": 0, "details": []}
    warnings: list[str] = []
    for path, fm, body in _iter(root):
        if fm.get("memory_layer") != "knowledge" or (project and fm.get("project") != project):
            continue
        summary["scanned"] += 1
        reason = episodic_reason(fm.get("title"), body)
        if not reason:
            continue
        summary["pending"] += 1
        summary["details"].append({"path": str(path.relative_to(root)), "title": str(fm.get("title", "")), "reason": reason})
        if apply:
            try:
                fio.update(path, {"memory_layer": "episodic", "episodic_reason": reason})
                lifecycle.append_event(path=_lifecycle(root), record_id=str(fm.get("slice_id", "")), event_type="archived",
                                       source="mark-episodic", reason="episodic", actor="hippo", ts=now)
                summary["updated"] += 1
            except Exception as exc:
                warnings.append(f"Failed to update {path}: {exc}")
    return summary, warnings


def revert(memory_root: Path | str, slice_id: str, *, now: str) -> bool:
    root = Path(memory_root)
    for path, fm, _ in _iter(root):
        if fm.get("slice_id") == slice_id and fm.get("memory_layer") == "episodic":
            fio.update(path, {"memory_layer": "knowledge"}, remove=("episodic_reason",))
            lifecycle.append_event(path=_lifecycle(root), record_id=slice_id, event_type="restored",
                                   source="mark-episodic", reason="revert", actor="hippo", ts=now)
            return True
    return False
```

`cli.py`：仿 `normalize-tags` 加 `mark-episodic`（多 `--project`、`--revert`）；`--revert` 給了就只做 revert（exit 1 若找不到）。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_moc_frontmatter_io.py tests/test_mark_episodic.py tests/test_tags_migration.py tests/test_ledger_lifecycle.py tests/test_cli.py -q`
Expected: all passed

- [ ] **Step 5: live dry-run（唯讀）**

Run: `hippo knowledge mark-episodic --memory-root ~/.agents/memory --dry-run | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['scanned'], d['pending']); [print(' -', x['title'][:60], x['reason']) for x in d['details'][:20]]"`
Expected: pending ≈ 104（診斷值為 grep 估計；實際以 `episodic_reason` 為準）。`--apply` 只在使用者略讀清單後執行。

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/moc/frontmatter_io.py paulsha_hippo/episodic_migration.py paulsha_hippo/cli.py tests/test_moc_frontmatter_io.py tests/test_mark_episodic.py
git commit -m "feat(knowledge): mark-episodic 一次性降層既存 session 狀態 note（可 revert）"
```

---
### Task 15: fix 2b — `hippo knowledge link-supersedes` 與跨 session 即時 supersedes

**Files:**
- Create: `paulsha_hippo/supersedes_link.py`
- Modify: `paulsha_hippo/atomizer/pipeline.py:720-758`（`_attach_unambiguous_supersedes` 改用 `topic`、放寬跨 session）
- Modify: `paulsha_hippo/cli.py`
- Test: `tests/test_link_supersedes.py`（新）、`tests/test_atomizer_pipeline.py`（追加）

**Interfaces:**
- `supersedes_link.scan(memory_root, *, families=()) -> dict`：讀所有 `memory_layer == "knowledge"` note（`slice_id, project, title, aliases, captured_at, checksum, supersedes, path`）＋ `lifecycle.fold_lifecycle` 的 decayed 集合；輸出 `{"auto": [pair...], "review": [pair...]}`，pair = `{"new": sid, "old": sid, "new_title", "old_title", "reason": "exact-title"|"alias"|"jaccard:0.67"|"tags:2"|"related", "project_new", "project_old"}`。
  - **auto**：`family_key` 相等 ∧（canonical title 相等 ∨ alias 相等）∧ `captured_at` 嚴格較新 ∧ checksum 不同 ∧ 舊者未 decayed ∧ 該新者只對應**恰一**個較舊候選 ∧ 新者 `supersedes` 尚未含舊者。
  - **review**：其餘 `topic.is_same_topic` 為 True（Jaccard）或 `tags` 交集 ≥ 2 或 `related` 互指者。
- `supersedes_link.apply_pairs(memory_root, pairs, *, now: str) -> int`：對每 pair `fio.update(new_path, {"supersedes": sorted(set(old_list + [old]))})` ＋ `relations.append_edge(memory_root, type="supersedes", frm=f"slice:{new}", to=f"slice:{old}", now=now, config_hash="link-supersedes")`；已含者跳過；回傳寫入數。
- `supersedes_link.write_report(memory_root, review_pairs, *, now) -> Path`：寫 `runtime/reports/link-supersedes-<now>.jsonl`（每行 pair ＋ `"accept": false`）與同名 `.md`（人讀）。
- CLI：`hippo knowledge link-supersedes --memory-root R [--dry-run|--apply] [--tier auto|review] [--accept REPORT.jsonl]`；`--apply --tier auto` 套 auto；`--accept` 只套 `accept: true` 的行；預設 dry-run 印 `{"auto": n, "review": m, "report": path}`。
- `_attach_unambiguous_supersedes`：條件改為 `item.project == frontmatter.project ∧ topic.canonical_title 相等（title 或 aliases）∧ item.checksum != new.checksum ∧ item.captured_at <= new.captured_at`；不再要求 `distilled_from` 相等；仍要求恰一匹配。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_link_supersedes.py
from pathlib import Path
from paulsha_hippo import supersedes_link as sl
from paulsha_hippo.ledger import relations
from paulsha_hippo.moc import frontmatter_io as fio

NOW = "2026-08-25T00:00:00Z"


def _note(mr: Path, sid: str, project: str, title: str, at: str, checksum: str, tags=(), aliases=()):
    p = mr / "knowledge" / project / f"n--{sid}.md"; p.parent.mkdir(parents=True, exist_ok=True)
    tags_s = "".join(f"  - {t}\n" for t in tags); al = "".join(f"  - \"{a}\"\n" for a in aliases)
    p.write_text(f"---\nslice_id: {sid}\nmemory_layer: knowledge\nproject: {project}\ntitle: \"{title}\"\n"
                 f"captured_at: \"{at}\"\nchecksum: {checksum}\nsupersedes: []\ntags:\n{tags_s}aliases:\n{al}---\nb\n", encoding="utf-8")
    return p


def test_scan_splits_auto_and_review(tmp_path):
    _note(tmp_path, "sl-old", "p", "CC2674P10 Flash 配置", "2026-08-10T00:00:00Z", "c1")
    _note(tmp_path, "sl-new", "p", "cc2674p10 flash 配置", "2026-08-20T00:00:00Z", "c2")
    _note(tmp_path, "sl-a", "p", "dual profile build isolation contract", "2026-08-01T00:00:00Z", "c3", tags=("build", "dual"))
    _note(tmp_path, "sl-b", "p", "dual build profile isolation gate", "2026-08-02T00:00:00Z", "c4", tags=("build", "dual"))
    out = sl.scan(tmp_path)
    assert [(x["new"], x["old"], x["reason"]) for x in out["auto"]] == [("sl-new", "sl-old", "exact-title")]
    assert {(x["new"], x["old"]) for x in out["review"]} == {("sl-b", "sl-a")}


def test_multi_candidates_go_to_review_not_auto(tmp_path):
    _note(tmp_path, "sl-1", "p", "T", "2026-08-01T00:00:00Z", "c1")
    _note(tmp_path, "sl-2", "p", "T", "2026-08-02T00:00:00Z", "c2")
    _note(tmp_path, "sl-3", "p", "T", "2026-08-03T00:00:00Z", "c3")
    out = sl.scan(tmp_path)
    assert out["auto"] == [] and len(out["review"]) >= 1


def test_apply_writes_supersedes_edge_and_is_idempotent(tmp_path):
    _note(tmp_path, "sl-old", "p", "T", "2026-08-10T00:00:00Z", "c1")
    new = _note(tmp_path, "sl-new", "p", "T", "2026-08-20T00:00:00Z", "c2")
    n1 = sl.apply_pairs(tmp_path, sl.scan(tmp_path)["auto"], now=NOW)
    assert n1 == 1 and fio.read(new.read_text())[0]["supersedes"] == ["sl-old"]
    edges = [e for e in relations.read_edges(tmp_path) if e["type"] == "supersedes"]
    assert edges == [dict(edges[0], to="slice:sl-old")] and edges[0]["from"] == "slice:sl-new"
    assert sl.scan(tmp_path)["auto"] == []
    assert sl.apply_pairs(tmp_path, [{"new": "sl-new", "old": "sl-old"}], now=NOW) == 0
    assert len([e for e in relations.read_edges(tmp_path) if e["type"] == "supersedes"]) == 1


def test_flash_layout_pair_needs_family(tmp_path):
    _note(tmp_path, "sl-0817", "ot-ti-mirror", "ot-ti-mirror Flash Layout and Image Artifacts", "2026-08-17T03:32:41Z", "c1", tags=("flash-layout",))
    _note(tmp_path, "sl-0821", "MCU-Octopus", "雙目標 Flash Layout", "2026-08-21T10:27:49Z", "c2", tags=("flash-layout", "cc2755"))
    assert sl.scan(tmp_path) == {"auto": [], "review": []}
    out = sl.scan(tmp_path, families=(("MCU-Octopus", "ot-ti-mirror"),))
    assert out["auto"] == []
    # 同 family、tags 交集 1（flash-layout）、標題 token 交集 2（flash、layout）→ review tier「tags+title」
    assert [(x["new"], x["old"], x["reason"]) for x in out["review"]] == [("sl-0821", "sl-0817", "tags+title")]


def test_report_round_trip_accept(tmp_path):
    _note(tmp_path, "sl-a", "p", "dual profile build isolation contract", "2026-08-01T00:00:00Z", "c3")
    _note(tmp_path, "sl-b", "p", "dual build profile isolation gate", "2026-08-02T00:00:00Z", "c4")
    report = sl.write_report(tmp_path, sl.scan(tmp_path)["review"], now=NOW)
    lines = report.read_text().splitlines(); assert len(lines) == 1 and '"accept": false' in lines[0]
    report.write_text(lines[0].replace('"accept": false', '"accept": true') + "\n")
    assert sl.apply_accepted(tmp_path, report, now=NOW) == 1
```

```python
# tests/test_atomizer_pipeline.py（追加）
    def test_cross_session_same_title_supersedes_at_publish(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)

            def canned(body: str) -> str:
                return json.dumps([{"title": "stable canonical title", "artifact_kind": "report",
                                    "project": "paulshaclaw", "tags": [], "body": body,
                                    "source_fragment_indices": [0, 1], "relations": []}])

            pipeline.run(root, config=cfg, config_hash=h, now="2026-07-16T01:00:00Z",
                         promoter=llm_promoter.LLMPromoter(FakeAgentClient(canned("body version one")),
                                                           skill_text="SKILL", known_projects=["paulshaclaw"]))
            old_slice = next((root / "knowledge" / "paulshaclaw").glob("*.md"))
            old_id = old_slice.stem
            # 第二輪：不同 session（inbox 檔名 s2 → source_session s2 → distilled_from 不同）
            raw2 = root / "inbox" / "research" / "claude" / "2026-06-02" / "s2.md"
            raw2.write_text((root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md").read_text(encoding="utf-8")
                            .replace("source_session: s1", "source_session: s2"), encoding="utf-8") \
                if (root / "inbox" / "research" / "claude" / "2026-06-02" / "s1.md").exists() else _seed_raw(root, session="s2")
            pipeline.run(root, config=cfg, config_hash=h, now="2026-07-16T02:00:00Z",
                         promoter=llm_promoter.LLMPromoter(FakeAgentClient(canned("body version two")),
                                                           skill_text="SKILL", known_projects=["paulshaclaw"]))
            notes = sorted((root / "knowledge" / "paulshaclaw").glob("*.md"))
            self.assertEqual(len(notes), 2)
            new_note = next(path for path in notes if path != old_slice)
            frontmatter, _ = pipeline._parse_frontmatter(new_note.read_text(encoding="utf-8"))
            self.assertNotEqual(frontmatter["distilled_from"], "claude:s1")
            self.assertEqual(frontmatter["supersedes"], [old_id])
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_link_supersedes.py tests/test_atomizer_pipeline.py -q -k "supersedes"`
Expected: FAIL（`ModuleNotFoundError`；cross-session 案例 `supersedes == []`）

- [ ] **Step 3: 實作 `supersedes_link.py`**

```python
"""跨 session 同主題 supersedes backfill（fix 2b）。auto tier 自動、review tier 出報表。"""
from __future__ import annotations
import json
from pathlib import Path

from paulsha_hippo import topic
from paulsha_hippo.ledger import lifecycle, relations
from paulsha_hippo.moc import frontmatter_io as fio


def _load(root: Path) -> list[dict]:
    out = []
    knowledge = root / "knowledge"
    for path in sorted(knowledge.rglob("*.md")) if knowledge.is_dir() else []:
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, _ = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if fm.get("memory_layer") != "knowledge" or not fm.get("slice_id"):
            continue
        out.append({"slice_id": str(fm["slice_id"]), "project": str(fm.get("project", "")),
                    "title": str(fm.get("title") or fm.get("atom_title") or ""),
                    "aliases": [str(a) for a in (fm.get("aliases") or []) if isinstance(a, str)],
                    "tags": {str(t) for t in (fm.get("tags") or []) if isinstance(t, str)},
                    "related": {str(r).strip("[]") for r in (fm.get("related") or []) if isinstance(r, str)},
                    "captured_at": str(fm.get("captured_at", "")), "checksum": str(fm.get("checksum", "")),
                    "supersedes": [str(s) for s in (fm.get("supersedes") or [])], "path": path})
    return out


def _decayed(root: Path) -> set[str]:
    events = lifecycle.read_events(root / "runtime" / "ledger" / "lifecycle.jsonl")
    return {rid for rid, st in lifecycle.fold_lifecycle(events).items() if st.get("state") in ("decayed", "archived")}


def scan(memory_root: Path | str, *, families=()) -> dict:
    root = Path(memory_root); notes = _load(root); dead = _decayed(root)
    auto, review = [], []
    by_id = {n["slice_id"]: n for n in notes}
    for new in notes:
        exact, fuzzy = [], []
        for old in notes:
            if old is new or old["slice_id"] in dead or old["captured_at"] >= new["captured_at"] \
               or old["checksum"] == new["checksum"] or old["slice_id"] in new["supersedes"]:
                continue
            if topic.family_key(old["project"], families) != topic.family_key(new["project"], families):
                continue
            names_new = {topic.canonical_title(x) for x in [new["title"], *new["aliases"]]}
            names_old = {topic.canonical_title(x) for x in [old["title"], *old["aliases"]]}
            if names_new & names_old:
                exact.append((old, "exact-title" if topic.canonical_title(new["title"]) == topic.canonical_title(old["title"]) else "alias"))
            elif topic.is_same_topic(new, old, families=families):
                fuzzy.append((old, "jaccard"))
            elif len(new["tags"] & old["tags"]) >= 2 or (old["slice_id"] in new["related"] and new["slice_id"] in old["related"]):
                fuzzy.append((old, "tags:%d" % len(new["tags"] & old["tags"]) if len(new["tags"] & old["tags"]) >= 2 else "related"))
            elif new["tags"] & old["tags"] and len(topic.title_tokens(new["title"], new["project"]) & topic.title_tokens(old["title"], old["project"])) >= 2:
                fuzzy.append((old, "tags+title"))
        pair = lambda old, why: {"new": new["slice_id"], "old": old["slice_id"], "new_title": new["title"], "old_title": old["title"],
                                 "project_new": new["project"], "project_old": old["project"], "reason": why}
        if len(exact) == 1:
            auto.append(pair(*exact[0]))
        else:
            review.extend(pair(o, w) for o, w in exact)
        review.extend(pair(o, w) for o, w in fuzzy)
    return {"auto": auto, "review": review}


def apply_pairs(memory_root: Path | str, pairs: list[dict], *, now: str) -> int:
    root = Path(memory_root); by_id = {n["slice_id"]: n for n in _load(root)}
    written = 0
    for p in pairs:
        new = by_id.get(p["new"])
        if not new or p["old"] in new["supersedes"] or p["old"] not in by_id:
            continue
        fio.update(new["path"], {"supersedes": sorted(set(new["supersedes"] + [p["old"]]))})
        relations.append_edge(root, type="supersedes", frm=f"slice:{p['new']}", to=f"slice:{p['old']}", now=now, config_hash="link-supersedes")
        new["supersedes"].append(p["old"]); written += 1
    return written


def write_report(memory_root: Path | str, review_pairs: list[dict], *, now: str) -> Path:
    root = Path(memory_root); rep = root / "runtime" / "reports"; rep.mkdir(parents=True, exist_ok=True)
    stem = f"link-supersedes-{now.replace(':', '').replace('+', '')}"
    jsonl = rep / f"{stem}.jsonl"
    jsonl.write_text("".join(json.dumps({**p, "accept": False}, ensure_ascii=False) + "\n" for p in review_pairs), encoding="utf-8")
    (rep / f"{stem}.md").write_text("| new | old | reason | new_title | old_title |\n|---|---|---|---|---|\n" +
                                    "".join(f"| {p['new']} | {p['old']} | {p['reason']} | {p['new_title']} | {p['old_title']} |\n" for p in review_pairs),
                                    encoding="utf-8")
    return jsonl


def apply_accepted(memory_root: Path | str, report: Path, *, now: str) -> int:
    pairs = [json.loads(l) for l in Path(report).read_text(encoding="utf-8").splitlines() if l.strip()]
    return apply_pairs(memory_root, [p for p in pairs if p.get("accept") is True], now=now)
```

`_attach_unambiguous_supersedes`：把 matches 條件改為

```python
            if item.get("slice_id") != slice_.slice_id
            and item.get("project") == frontmatter.get("project")
            and title and (title in {_canonical_title(item.get("title")), _canonical_title(item.get("atom_title")),
                                     *(_canonical_title(a) for a in (item.get("aliases") or []) if isinstance(a, str))})
            and item.get("checksum") != frontmatter.get("checksum")
            and str(item.get("captured_at", "")) <= str(frontmatter.get("captured_at", ""))
```

CLI 接線依 Interfaces。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_link_supersedes.py tests/test_atomizer_pipeline.py tests/test_janitor_e2e.py tests/test_ledger_relations.py -q`
Expected: all passed；另加一個整合斷言（可放 `test_link_supersedes.py`）：apply 後跑 `janitor.rules.plan_scan` 對舊筆產生 `reason == "superseded"` 事件。

- [ ] **Step 5: live dry-run（唯讀）**

Run: `hippo knowledge link-supersedes --memory-root ~/.agents/memory --dry-run`
Expected: `auto` ≈ 32（MCU 兩專案同標題）＋其他專案；`review` 報表路徑印出。`--apply --tier auto` 與 `--accept` 都要使用者看過報表後才跑。

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/supersedes_link.py paulsha_hippo/atomizer/pipeline.py paulsha_hippo/cli.py tests/test_link_supersedes.py tests/test_atomizer_pipeline.py
git commit -m "feat(knowledge): link-supersedes 跨 session 同主題 backfill（auto/review 分層）"
```

---

### Task 16: fix 1d — janitor `check_provenance_commit` 語意

**Files:**
- Modify: `paulsha_hippo/importer/_git.py`（`git_commit_exists`）
- Modify: `paulsha_hippo/janitor/rules.py:87-140`、`:235-260`（`source_commit_exists` 參數）
- Modify: `paulsha_hippo/janitor/scanner.py:129-`（透傳）、`paulsha_hippo/dream/cli.py`（傳 `lambda r: None`）
- Test: `tests/test_janitor_rules.py`、`tests/test_git_helper.py`（追加）

**Interfaces:**
- `_git.git_commit_exists(toplevel, sha) -> bool | None`：`git cat-file -e <sha>^{commit}` → True；returncode≠0 → False；toplevel/sha 空或 git 不可用 → None。
- `rules.SourceCommitCheck = Callable[[KnowledgeRecord], bool | None]`；`_default_source_commit_exists(record)`：`commit in ("", "_unknown")` → None；`record.project` 在 `load_projects_config(default_projects_path(memory_root)).projects` 找 roots（rules 是純函式、無 memory_root → 由 scanner 建 closure 傳入）；逐 root `git_commit_exists`，任一 True → True，全 False → False，全 None → None。
- `_decide_decay(..., source_commit_exists=None)`：在 `check_provenance_path` 之後：`if config.check_provenance_commit and source_commit_exists is not None and source_commit_exists(record) is False: return {"reason": "source_invalid", "detail": {"check": "provenance_commit"}}`。
- `plan_scan(..., source_commit_exists: SourceCommitCheck | None = None)`；`run_scan(..., source_commit_exists=None)` 預設在 scanner 內建 closure（用 `memory_root`）；dream/cli 傳 `lambda record: None`（服務情境 repo 多半不在）。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_janitor_rules.py（追加）
CFG_COMMIT = JanitorConfig(schema_version="1", default_decay_age_days=90, by_artifact_kind={},
                           check_provenance_path=True, check_provenance_commit=True, decay_superseded=True)


class ProvenanceCommitTests(unittest.TestCase):
    def test_dangling_commit_decays_when_enabled(self):
        ev = rules.plan_scan([_rec(captured="2026-05-30T00:00:00Z")], {}, {}, CFG_COMMIT, NOW, HASH,
                             source_path_exists=_PATH_OK, source_commit_exists=lambda r: False)
        self.assertEqual(ev[0]["reason"], "source_invalid"); self.assertEqual(ev[0]["detail"]["check"], "provenance_commit")

    def test_unknown_or_disabled_never_decays(self):
        self.assertEqual(rules.plan_scan([_rec(captured="2026-05-30T00:00:00Z")], {}, {}, CFG_COMMIT, NOW, HASH,
                                         source_path_exists=_PATH_OK, source_commit_exists=lambda r: None), [])
        self.assertEqual(rules.plan_scan([_rec(captured="2026-05-30T00:00:00Z")], {}, {}, CFG, NOW, HASH,
                                         source_path_exists=_PATH_OK, source_commit_exists=lambda r: False), [])
```

```python
# tests/test_git_helper.py（追加）
    def test_git_commit_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "r"; repo.mkdir(); _init_repo(repo)
            subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-q", "--allow-empty", "-m", "x"], check=True)
            head = _git.git_head(str(repo))
            self.assertTrue(_git.git_commit_exists(str(repo), head))
            self.assertFalse(_git.git_commit_exists(str(repo), "0" * 40))
            self.assertIsNone(_git.git_commit_exists(None, head))
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_janitor_rules.py tests/test_git_helper.py -q -k "commit"`
Expected: FAIL（`unexpected keyword 'source_commit_exists'`；`AttributeError: git_commit_exists`）

- [ ] **Step 3: 實作**

`_git.py`：

```python
def git_commit_exists(toplevel: str | Path | None, sha: str | None) -> Optional[bool]:
    if not toplevel or not sha:
        return None
    try:
        proc = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=str(toplevel),
                              capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT)
    except Exception:
        return None
    return proc.returncode == 0
```

`rules.py`：加 `SourceCommitCheck` 型別與 `_decide_decay` / `plan_scan` 新參數（見 Interfaces）；`scanner.run_scan` 加 `source_commit_exists=None` 參數並建預設 closure：

```python
    if source_commit_exists is None:
        roots_by_project = _roots_by_project(memory_root)   # load_projects_config 失敗 → {}
        def source_commit_exists(record):
            sha = record.provenance.get("commit")
            if sha in (None, "", "_unknown"):
                return None
            results = [_git.git_commit_exists(r, sha) for r in roots_by_project.get(record.project, ())]
            if any(r is True for r in results): return True
            if results and all(r is False for r in results): return False
            return None
```

`dream/cli.py::janitor_fn` 加 `source_commit_exists=lambda record: None`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_janitor_rules.py tests/test_janitor_scanner.py tests/test_janitor_e2e.py tests/test_janitor_cli.py tests/test_git_helper.py tests/test_dream_cli.py -q`
Expected: all passed（預設 `check_provenance_commit: false`，live 行為不變）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/_git.py paulsha_hippo/janitor paulsha_hippo/dream/cli.py tests/test_janitor_rules.py tests/test_git_helper.py
git commit -m "feat(janitor): check_provenance_commit 生效——dangling commit 視為 source_invalid"
```

---

### Task 17: 交付治理 — changelog 碎片、README、全套驗證

**Files:**
- Create: `changelog.d/hygiene-provenance.md`（type: feat；Task 1–3、10、16）、`changelog.d/hygiene-show-agent.md`（Task 4–6）、`changelog.d/hygiene-supersedes.md`（Task 7、8、15）、`changelog.d/hygiene-episodic.md`（Task 13、14）、`changelog.d/hygiene-followups.md`（Task 9、11、12）
- Modify: `README.md:31`（日常命令加 `hippo show --agent`、`hippo followups`、`hippo knowledge backfill-provenance|link-supersedes|mark-episodic`）、`README.md:33`（KPI 段提 follow-ups 欄）
- （已於 2026-08-25 pre-implementation prep 完成：design §10 override 路徑改為 `~/.config/paulshaclaw/janitor.override.yaml`、首波日期改 2026-09-15、記錄 TTL 維持 90 的決定）

- [ ] **Step 1: 寫五個 changelog 碎片**（格式比照 `changelog.d/109-normalize-tags-migration.md`：frontmatter `type: feat`，條列每個新命令／欄位／flag 與 migration 契約）

- [ ] **Step 2: README 同步**

`README.md:31` 在 `hippo recall` 之後加：`／\`hippo show --agent <slice_id>\`（精簡 note 視圖，省 ~70% token）／\`hippo followups list|verify|close|extract\`（可行動語句 ledger）／\`hippo knowledge backfill-provenance|link-supersedes|mark-episodic\`（一次性 migration，先 dry-run）`。

- [ ] **Step 3: 全套驗證（在非巢狀 sibling worktree）**

Run:
```bash
rm -rf .psc_tmp
python3 -m pytest tests/ -q
python3 -m policy_check --repo .
openspec validate --all --strict
```
Expected: pytest 全綠（含新測試 ≥ 60 個）；policy_check 無 failure（R-09 本機不驗碎片，CI 才驗）；openspec 全 valid。

- [ ] **Step 4: Commit**

```bash
git add changelog.d/hygiene-*.md README.md docs/superpowers/specs/2026-08-25-issue-136-knowledge-hygiene-design.md
git commit -m "docs(hygiene): changelog 碎片、README 命令同步、spec 路徑更正"
```

- [ ] **Step 5: live migration 執行順序（使用者核可後，逐一、每步先 dry-run）**

1. Task 0 override（若尚未）→ 2. `backfill-provenance --apply` → 3. `mark-episodic --apply`（先看清單）→ 4. `link-supersedes --apply --tier auto` → 5. 看 review 報表、勾 accept → `--accept` → 6. `followups extract --apply` → 7. `hippo janitor scan --dry-run` 看 superseded 候選 → 8. `hippo dream run`。
部署：`hippo upgrade plan/prepare/apply`（wheel＋skill）＋ `hippo install hooks`（三支 session_end hook 改了）；`hippo doctor` 確認。

---

## Later（不在本 plan）

- **3a sidecar 拆檔**：`hippo knowledge split-distiller`——`show --agent` 已拿到主要收益；只在 Obsidian 體驗或 wakeup I/O 成問題時做。
- **fix 6 `kind` / prompt 型別過濾 / `verify:`**：需要 (i) prompt 分類器、(ii) 4,178 筆 `kind` 回填準確度；等 1–5 落地後用 KPI（看過率／採用率）決定。草案見 spec §6。
- **LLM follow-up 抽取**：skill output 加 optional `followups: []`（`llm_output` 對未知鍵 soft-drop，前向相容）。

## 依賴與順序

```
T0 (live) ─┐
T1 → T2 → T3 ──────────────┐
T4 → T5 → T6               ├→ T10 (需 T2/T3/T9) → T16
T4 → T7 → T8               │
T9 ────────────────────────┘
T9 → T11 → T12 (需 T4)
T4 → T13 → T14
T7 → T15
T17 最後
```
可並行：{T1–T3}、{T4–T6}、{T7–T8}、{T9} 四條線互不相依；T10／T11／T13 之後才會合流。

## Spec 覆蓋自檢

| Spec 節 | Task |
|---|---|
| §1 1a hook | T1 |
| §1 1a adapter/sanitizer/importer/atomizer 六鍵 | T2、T3 |
| §1 1b import-discovery fallback | T2 |
| §1 1c cites | T9 |
| §1 1d janitor | T16 |
| §1 migration backfill-provenance | T10 |
| §2 2a shortlist 折疊＋families | T7、T8 |
| §2 2b link-supersedes＋即時跨 session | T15 |
| §3 3b show --agent＋read 歸因＋hint | T5、T6 |
| §3 3a sidecar | Later |
| §4 skill 規則／post-filter／validate／migration | T13、T14 |
| §5 抽取／ledger／verify／dream／brief／KPI | T11、T12 |
| §6 kind／verify: | Later |
| §7 優先序 | 任務編號即順序（T4 為共用前置） |
| §8 cortex 相容 | 未動 PHASES／ARTIFACT_KINDS／REQUIRED 集合；`episodic` 為新 layer 值，cortex 不讀 |
| §9 open decisions（皆採建議值） | family=T7；sidecar=Later；episodic keep=T14；brief=T12；backfill-approx=T10 |
| §10 TTL | T0 |
| §11 部署 | T17 Step 5 |
