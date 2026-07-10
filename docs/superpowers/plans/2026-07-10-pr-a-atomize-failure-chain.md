# PR-A 原子化 P0 失敗鏈（#15 + #19 singleton + #10 最小修復）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓原子化管線的失敗顯性化且可恢復——promote 失敗三分類、`parked` 顯性終態＋毒快取淘汰＋證據落盤、`hippo requeue` 恢復路徑、dream run 全域 singleton lock、orchestrator bounded error+errno、backend argv 絕對路徑化（init／doctor probe／既有設定冪等 migration）。

**Architecture:** 三層改動：(1) 失敗分類鏈——`agent_exec` 例外典型化（unavailable/transient）→ `llm_promoter.PromoteError` 附 category → `pipeline` 依 category 決定 park／重試／淘汰快取並落證據；(2) ledger 狀態機——`processing.VALID_STATES` 擴為四值，`parked` 事件帶結構化欄位，`requeue` 模組＋CLI 把 parked 送回 split；(3) 運維面——`dream/lock.py` 全域 nonblocking flock（dream run 入口整輪持有）、orchestrator 保存 bounded 錯誤訊息與 errno、`ops.resolve_backend_argv`＋init 絕對路徑化＋doctor service-effective probe＋`--fix-backend` 冪等 migration。

**Tech Stack:** Python（repo 現行 toolchain）、純 stdlib（`fcntl`／`shutil`／`subprocess`／`json`／`argparse`／`re`）、`unittest` 測試風格＋pytest runner（CI `tests.yml` 自動跑）。

## Global Constraints

（自 spec `docs/superpowers/specs/2026-07-10-all-issues-resolution-design.md` §7／§3.3 逐字抄錄，每個 Task 隱含適用）

- 「stdlib-only、零新依賴」（spec §3.3.5；本批次一體適用——不得新增任何第三方套件）。
- 「分支一律 `feature/<issue>-<slug>`；禁 commit main。」→ 本批次分支：`feature/15-atomize-failure-chain`（spec §3.1）。
- 「每 code PR：changelog.d 碎片（repo 現行慣例）、PR checklist 全勾、`Closes #N`（R-17）、zh-tw（語言規範）、`policy_check` 零 failure。」
- 「`tier: shareable`（R-21）：所有新增文件（含本 spec、capability matrix、快照留言）不得含個人絕對路徑、機敏標記。」→ 測試一律用 `TemporaryDirectory`，不得寫死個人路徑。
- 「R-18/R-22：behavior 變更同步 README／docs 引用（`hippo recall`、`--backend` 選單、doctor 新輸出）。」→ 本批次涉及 `hippo requeue`、`hippo doctor --fix-backend`、dream singleton 行為。
- 「測試新增全部進 CI 覆蓋（R-19；`tests.yml` 已自動跑 pytest）。」→ 所有新測試放 `tests/`，pytest 自動收。
- commit message 一律 zh-tw conventional-commit（`feat(scope): ...`／`fix(scope): ...`／`test(scope): ...`）。
- 不 bump `VERSION`（spec §2 非目標）。
- TDD 順序不可顛倒：先寫失敗測試→跑確認 FAIL→最小實作→跑確認 PASS→commit。

## 跨批次共享介面契約（本 plan 實作／提供方）

以下契約由 workflow 主編排拍板，**逐字遵守，偏離即 bug**：

1. **processing 狀態機**（`paulsha_hippo/ledger/processing.py`）：`VALID_STATES` 擴為 `{"split","promoted","skipped","parked"}`（現為 line 14 三值集合）。`parked` 事件經 `append_state(**extra)` 附欄位：`failure_category`（`"backend_unavailable"|"transient"|"invalid_output"`）、`attempts:int`、`cache_key:str`、`error:str`（截斷≤500字元、去敏）。requeue 事件 = `state="split"` + extra `requeued_from="parked"`, `requeue_reason:str`。
2. **backend argv 解析**（PR-A 建立、PR-D 重用）：`paulsha_hippo/ops.py` 新增 `resolve_backend_argv(argv: list[str]) -> list[str]`：argv[0] 以 `shutil.which` 絕對路徑化，找不到 raise `BackendUnavailableError`（`ValueError` 子類）。
3. **global dream lock**：`<memory_root>/runtime/locks/dream.lock` 固定路徑；PR-A 於 dream run 入口 `fcntl.flock(LOCK_EX|LOCK_NB)` 整輪持有，取不到→log 後 exit 0；PR-C doctor 引用同一路徑報告持鎖狀態（本 plan 以 `paulsha_hippo/dream/lock.py:dream_lock_path()` 匯出該路徑供 PR-C import）。
4. **CLI 子命令**一律走 `paulsha_hippo/cli.py` 的 `memory_subparsers.add_parser` 既有模式：PR-A 加 `requeue`（`<session-key>|--all-parked`）。
5. 恢復序列／收口不在本 plan 內（workflow 主編排執行，spec §4/§5）。lock sharding（PR-C）、preset registry 全量（PR-D）不做。

## 既有行為反轉清單（spec §3.1.9「測試反轉」，實作者須知）

| 既有測試（`tests/test_atomizer_pipeline.py`） | 反轉後 |
|---|---|
| `test_exhausted_budget_retains_poisoned_cache_and_stops_llm_calls`（毒快取保留） | 超限即淘汰＋park（Task 4 改寫） |
| `test_transport_failures_do_not_consume_retry_budget`（transport 不耗預算、永留 split） | transient 耗預算、超限 park（Task 4 改寫） |
| `test_transport_recovery_chatter_starts_content_retry_budget_at_one`（雙軌預算） | 單一 attempts 預算（Task 4 改寫） |
| `tests/test_dream_orchestrator.py::test_failure_record_redacts_exception_message`（只存類別名） | 保存 bounded 訊息＋errno（Task 7 改寫） |
| `tests/test_ops.py::test_init_claude_headless_writes_config_and_override`（寫裸 `- claude`） | 寫絕對路徑（Task 8 改寫） |
| `tests/test_dream_orchestrator.py::test_janitor_runs_even_if_atomize_raises`（error dict 只有 error 鍵） | error dict 含 error_message/errno（Task 7 同步更新斷言） |

---

### Task 1: processing 狀態機——`parked` 終態 + `sanitize_error_text`

**Files:**
- Modify: `paulsha_hippo/ledger/processing.py:14`（`VALID_STATES`）、`paulsha_hippo/ledger/processing.py:50-51`（`append_state` 驗證區塊後插入 parked 欄位守衛）、檔頭 import 區（新增 helper 所需——`Path` 已有）
- Test: `tests/test_ledger_processing.py`

**Interfaces:**
- Consumes: 既有 `processing.append_state(memory_root, *, session_key, state, now, config_hash, **extra)`、`processing.state_of`、`processing.read_events`（`paulsha_hippo/ledger/processing.py:27/156/79`，簽名不變）。
- Produces（後續 Task 4/5/7/11 依賴）:
  - `processing.VALID_STATES == {"split", "promoted", "skipped", "parked"}`
  - `processing.PARKED_FAILURE_CATEGORIES == {"backend_unavailable", "transient", "invalid_output"}`
  - `processing.sanitize_error_text(text: str, limit: int = 500) -> str`（壓平 whitespace、home 目錄前綴遮蔽為 `~`、截斷 ≤ limit）
  - `append_state(state="parked", ...)` 若 `extra["failure_category"]` 不在 `PARKED_FAILURE_CATEGORIES` → raise `ValueError`

- [ ] **Step 0: 確認分支**

Run: `git -C /path/to/repo branch --show-current`
Expected: `feature/15-atomize-failure-chain`（若不是：`git switch -c feature/15-atomize-failure-chain`；禁止在 `main` 動工）

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_ledger_processing.py` 檔尾（`if __name__ == "__main__":` 之前）加入：

```python
class TestParkedState(unittest.TestCase):
    def test_parked_is_valid_state_with_required_fields(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            processing.append_state(
                root,
                session_key="claude:s5",
                state="parked",
                now="2026-07-10T00:00:00Z",
                config_hash="hash1",
                failure_category="invalid_output",
                attempts=6,
                cache_key="claude:s5__" + "a" * 64,
                error="llm promote failed: no JSON array found",
            )
            self.assertEqual(processing.state_of(root, "claude:s5"), "parked")
            event = processing.read_events(root)[-1]
            self.assertEqual(event["failure_category"], "invalid_output")
            self.assertEqual(event["attempts"], 6)
            self.assertEqual(event["cache_key"], "claude:s5__" + "a" * 64)

    def test_parked_requires_known_failure_category(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaises(ValueError):
                processing.append_state(
                    root, session_key="claude:s6", state="parked",
                    now="2026-07-10T00:00:00Z", config_hash="hash1",
                    failure_category="weird", attempts=1, cache_key="", error="x",
                )
            with self.assertRaises(ValueError):
                processing.append_state(
                    root, session_key="claude:s6", state="parked",
                    now="2026-07-10T00:00:00Z", config_hash="hash1",
                )

    def test_requeue_event_returns_session_to_split(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            processing.append_state(
                root, session_key="claude:s7", state="parked",
                now="2026-07-10T00:00:00Z", config_hash="hash1",
                failure_category="transient", attempts=6, cache_key="", error="t",
            )
            processing.append_state(
                root, session_key="claude:s7", state="split",
                now="2026-07-10T01:00:00Z", config_hash="hash1",
                requeued_from="parked", requeue_reason="backend fixed",
            )
            self.assertEqual(processing.state_of(root, "claude:s7"), "split")
            event = processing.read_events(root)[-1]
            self.assertEqual(event["requeued_from"], "parked")
            self.assertEqual(event["requeue_reason"], "backend fixed")


class TestSanitizeErrorText(unittest.TestCase):
    def test_truncates_to_limit(self):
        self.assertEqual(len(processing.sanitize_error_text("x" * 2000)), 500)
        self.assertEqual(processing.sanitize_error_text("x" * 2000, limit=10), "x" * 10)

    def test_collapses_whitespace_and_masks_home(self):
        home = str(Path.home())
        raw = f"boom\n  at {home}/secret\tplace"
        out = processing.sanitize_error_text(raw)
        self.assertNotIn(home, out)
        self.assertIn("~/secret", out)
        self.assertNotIn("\n", out)
        self.assertNotIn("\t", out)
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_ledger_processing.py -k "parked or sanitize or requeue" -v`
Expected: FAIL——`test_parked_is_valid_state_with_required_fields` 拋 `ValueError: invalid processing state: parked`；`TestSanitizeErrorText` 拋 `AttributeError: module ... has no attribute 'sanitize_error_text'`。

- [ ] **Step 3: 最小實作**

`paulsha_hippo/ledger/processing.py` line 14 改為：

```python
VALID_STATES = {"split", "promoted", "skipped", "parked"}
PARKED_FAILURE_CATEGORIES = {"backend_unavailable", "transient", "invalid_output"}
_ERROR_TEXT_MAX_CHARS = 500


def sanitize_error_text(text: str, limit: int = _ERROR_TEXT_MAX_CHARS) -> str:
    """Bounded、去敏的錯誤文字：壓平 whitespace、遮蔽 home 前綴、截斷。

    parked 事件與 dream orchestrator 的 error 欄位共用（契約：≤500 字元、去敏）。
    """
    collapsed = " ".join(str(text).split())
    home = str(Path.home())
    if home and home != "/":
        collapsed = collapsed.replace(home, "~")
    return collapsed[:limit]
```

`append_state` 內（現 line 50-51 `if state not in VALID_STATES: raise ...` 之後）插入：

```python
    if state == "parked" and extra.get("failure_category") not in PARKED_FAILURE_CATEGORIES:
        raise ValueError(
            f"parked event requires failure_category in {sorted(PARKED_FAILURE_CATEGORIES)}"
        )
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_ledger_processing.py -v`
Expected: 全部 PASS（含既有 6 個測試）。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ledger/processing.py tests/test_ledger_processing.py
git commit -m "feat(ledger): processing 狀態機新增 parked 終態與 sanitize_error_text（#15）"
```

---

### Task 2: agent_exec 例外典型化（unavailable／transient 子類）

**Files:**
- Modify: `paulsha_hippo/atomizer/agent_exec.py:10-11`（例外階層）、`paulsha_hippo/atomizer/agent_exec.py:26-49`（`AgentExecClient.run` raise 站點）、`paulsha_hippo/atomizer/agent_exec.py:67-99`（`HttpAgentClient.run` raise 站點）
- Test: `tests/test_agent_exec.py`

**Interfaces:**
- Consumes: 既有 `AgentExecError(Exception)`（`agent_exec.py:10`）。
- Produces（Task 3 依賴）:
  - `class AgentUnavailableError(AgentExecError)`——backend executable 不存在／未設定（FileNotFoundError、PermissionError、空 command）
  - `class AgentTransientError(AgentExecError)`——timeout／non-zero exit／空輸出／HTTP endpoint 不可達／回應缺 content
  - 既有 `except AgentExecError` 呼叫端不受影響（子類關係）

- [ ] **Step 1: 寫失敗測試**

`tests/test_agent_exec.py`：把既有三個測試（lines 43-70）的斷言例外改為子類，並新增兩個測試。完整改寫如下（取代 `test_exec_client_missing_command_raises`、`test_exec_client_nonzero_exit_raises`、`test_exec_client_timeout_raises`、`test_exec_client_empty_stdout_raises` 四個方法，並在 class 內新增三個方法）：

```python
    def test_exec_client_missing_command_raises_unavailable(self):
        client = agent_exec.AgentExecClient(["/nonexistent/bin/nope"], timeout=5)
        with self.assertRaises(agent_exec.AgentUnavailableError):
            client.run("x")

    def test_exec_client_not_configured_raises_unavailable(self):
        with self.assertRaises(agent_exec.AgentUnavailableError):
            agent_exec.AgentExecClient([], timeout=5).run("x")

    def test_exec_client_nonzero_exit_raises_transient(self):
        client = agent_exec.AgentExecClient(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            timeout=5,
        )
        with self.assertRaises(agent_exec.AgentTransientError):
            client.run("x")

    def test_exec_client_timeout_raises_transient(self):
        client = agent_exec.AgentExecClient(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=1,
        )
        with self.assertRaises(agent_exec.AgentTransientError):
            client.run("x")

    def test_exec_client_empty_stdout_raises_transient(self):
        client = agent_exec.AgentExecClient(
            [sys.executable, "-c", "import sys; sys.stdin.read(); print('', end='')"],
            timeout=5,
        )
        with self.assertRaises(agent_exec.AgentTransientError):
            client.run("x")

    def test_http_client_unreachable_raises_transient(self):
        client = agent_exec.HttpAgentClient("http://127.0.0.1:1", "m", timeout=2)
        with self.assertRaises(agent_exec.AgentTransientError):
            client.run("x")

    def test_typed_errors_remain_agent_exec_errors(self):
        self.assertTrue(issubclass(agent_exec.AgentUnavailableError, agent_exec.AgentExecError))
        self.assertTrue(issubclass(agent_exec.AgentTransientError, agent_exec.AgentExecError))
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_agent_exec.py -v`
Expected: FAIL——`AttributeError: module 'paulsha_hippo.atomizer.agent_exec' has no attribute 'AgentUnavailableError'`。

- [ ] **Step 3: 最小實作**

`paulsha_hippo/atomizer/agent_exec.py` line 10-11 的例外定義改為：

```python
class AgentExecError(Exception):
    """Raised when an agent subprocess cannot produce usable output."""


class AgentUnavailableError(AgentExecError):
    """backend executable 不存在／未設定（#15 分類：backend_unavailable，不重試）。"""


class AgentTransientError(AgentExecError):
    """timeout／non-zero exit／空輸出／端點不可達（#15 分類：transient，有限重試）。"""
```

`AgentExecClient.run`（lines 26-49）改為：

```python
    def run(self, prompt: str) -> str:
        if not self._command:
            raise AgentUnavailableError("agent command not configured")
        try:
            completed = subprocess.run(
                self._command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
                # #7：注入自捕捉標記——蒸餾子程序（claude -p 等）的 agent session
                # 其 hooks 讀到即跳過 queue write，斷開遞迴自捕捉。
                env={**os.environ, "HIPPO_SELF_SESSION": "1", **(self._env or {})},
            )
        except FileNotFoundError as exc:
            raise AgentUnavailableError(f"agent command not found: {self._command[0]}") from exc
        except PermissionError as exc:
            raise AgentUnavailableError(f"agent command not executable: {self._command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentTransientError(f"agent timed out after {self._timeout}s") from exc
        if completed.returncode != 0:
            raise AgentTransientError(f"agent exited with code {completed.returncode}")
        if not completed.stdout.strip():
            raise AgentTransientError("agent produced empty output")
        return completed.stdout
```

`HttpAgentClient.run` 內三個 raise 站點（lines 91-98）改為：

```python
        except urllib.error.URLError as exc:
            raise AgentTransientError(f"openai-compatible endpoint unreachable: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentTransientError("openai-compatible response missing choices[0].message.content") from exc
        if not str(content).strip():
            raise AgentTransientError("agent produced empty output")
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_agent_exec.py tests/test_ops.py -v`
Expected: 全 PASS（`test_ops.py::HttpAgentClientTests::test_unreachable_endpoint_raises_agent_exec_error` 因子類關係仍過）。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/atomizer/agent_exec.py tests/test_agent_exec.py
git commit -m "feat(atomizer): agent_exec 例外典型化——unavailable/transient 子類（#15 分類器地基）"
```

---

### Task 3: `PromoteError` 附 failure category

**Files:**
- Modify: `paulsha_hippo/atomizer/llm_promoter.py:9`（import）、`paulsha_hippo/atomizer/llm_promoter.py:18-19`（`PromoteError`）、`paulsha_hippo/atomizer/llm_promoter.py:106-116`（`promote` 例外包裝）
- Test: `tests/test_llm_promoter.py`

**Interfaces:**
- Consumes: Task 2 的 `agent_exec.AgentUnavailableError`、`agent_exec.AgentExecError`。
- Produces（Task 4 依賴）:
  - `PromoteError(message: str, *, category: str = "invalid_output")`，屬性 `.category ∈ {"backend_unavailable","transient","invalid_output"}`
  - 分類規則：`AgentUnavailableError → "backend_unavailable"`；其他 `AgentExecError → "transient"`；`LlmOutputError` 與 slice validation → `"invalid_output"`（預設值）

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_llm_promoter.py` 的 `LLMPromoterTests` class 內新增：

```python
    def test_agent_unavailable_maps_to_backend_unavailable_category(self):
        class Unavailable(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                raise agent_exec.AgentUnavailableError("agent command not found: claude")

        promoter = llm_promoter.LLMPromoter(
            Unavailable(), skill_text="SKILL", known_projects=["paulshaclaw"]
        )
        with self.assertRaises(llm_promoter.PromoteError) as ctx:
            promoter.promote([_frag(0)], CFG)
        self.assertEqual(ctx.exception.category, "backend_unavailable")

    def test_agent_timeout_maps_to_transient_category(self):
        class Timeout(agent_exec.AgentClient):
            def run(self, prompt: str) -> str:
                raise agent_exec.AgentTransientError("agent timed out after 600s")

        promoter = llm_promoter.LLMPromoter(
            Timeout(), skill_text="SKILL", known_projects=["paulshaclaw"]
        )
        with self.assertRaises(llm_promoter.PromoteError) as ctx:
            promoter.promote([_frag(0)], CFG)
        self.assertEqual(ctx.exception.category, "transient")

    def test_invalid_output_maps_to_invalid_output_category(self):
        with self.assertRaises(llm_promoter.PromoteError) as ctx:
            _promoter("garbage not json").promote([_frag(0)], CFG)
        self.assertEqual(ctx.exception.category, "invalid_output")
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_llm_promoter.py -k "category" -v`
Expected: FAIL——`AttributeError: 'PromoteError' object has no attribute 'category'`。

- [ ] **Step 3: 最小實作**

`paulsha_hippo/atomizer/llm_promoter.py` line 9 的 import 改為：

```python
from .agent_exec import AgentClient, AgentExecError, AgentUnavailableError, CachingAgentClient
```

lines 18-19 的 `PromoteError` 改為（並在其後加分類 helper）：

```python
class PromoteError(Exception):
    """Raised when session-level promotion cannot complete safely.

    category ∈ {"backend_unavailable", "transient", "invalid_output"}（#15 失敗分類）。
    """

    def __init__(self, message: str, *, category: str = "invalid_output") -> None:
        super().__init__(message)
        self.category = category


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, AgentUnavailableError):
        return "backend_unavailable"
    if isinstance(exc, AgentExecError):
        return "transient"
    return "invalid_output"
```

`promote` 內例外包裝（lines 115-116）改為：

```python
        except (AgentExecError, llm_output.LlmOutputError) as exc:
            raise PromoteError(
                f"llm promote failed: {exc}", category=_failure_category(exc)
            ) from exc
```

（`promote` 其餘 raise 站點——`slice validation failed`、`fragments must belong to one session` 等——不動，吃 `category="invalid_output"` 預設值。）

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_llm_promoter.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/atomizer/llm_promoter.py tests/test_llm_promoter.py
git commit -m "feat(atomizer): PromoteError 附 failure category 三分類（#15）"
```

---

### Task 4: pipeline 失敗處理——park／毒快取淘汰／證據落盤／反轉既有測試

**Files:**
- Modify: `paulsha_hippo/atomizer/pipeline.py:128-150`（`_record_promote_failure` 整段刪除，換成新常數＋三個函式）、`paulsha_hippo/atomizer/pipeline.py:264-274`（`_promote_fragments` 例外 category）、`paulsha_hippo/atomizer/pipeline.py:322`（split pass 跳過 parked）、`paulsha_hippo/atomizer/pipeline.py:462-471`（promote pass 失敗分支）
- Test: `tests/test_atomizer_pipeline.py`

**Interfaces:**
- Consumes:
  - Task 1：`processing.sanitize_error_text(text) -> str`、`append_state(state="parked", failure_category=..., attempts=..., cache_key=..., error=...)`
  - Task 3：`PromoteError.category`
  - 既有：`_LLM_PROMOTE_MAX_RETRIES = 5`（`pipeline.py:20`，沿用不改值）、`_cache_path`／`_clear_cache_key`／`_retry_counter_path`／`_clear_retry_counter`／`_atomic_write`（`pipeline.py:64-126`，簽名不變）、`LLMPromoter.cache_key_for_fragments`／`clear_cache_for_fragments`
- Produces（Task 11／PR 驗收依賴）:
  - `_handle_promote_failure(memory_root: Path, promoter: Promoter, fragments: list[Fragment], exc: PromoteError, *, session_key: str, now: str, config_hash: str) -> tuple[str, bool]`（回傳 `(警告註記, 是否已 park)`；非 LLMPromoter 回 `("", False)` 保持既有「left in split」語意）
  - 證據檔：`<memory_root>/runtime/queue/_failed/<agent>__<session>.json`，schema `{"session_key","failure_category","attempts","cache_key","error","ts","last_output_excerpt"}`（atomic write、同 session 覆寫）
  - park 時只刪 `{cache_key}.json` 與 `{cache_key}.retries`，**保留 split fragments**（`inbox/_slices/**`）
  - parked／requeue 前的 raw 重現不再自動 re-split（split pass 跳過 parked session）

- [ ] **Step 1: 改寫三個既有測試為新期望（反轉）＋新增 park 測試**

`tests/test_atomizer_pipeline.py`——**(a)** 整段替換 `test_transport_failures_do_not_consume_retry_budget`（lines 1172-1199）：

```python
    def test_transient_failures_consume_budget_and_park_after_limit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(
                root,
                [agent_exec.AgentTransientError("agent timed out after 600s")] * 7,
            )

            result = None
            for hour in range(6):
                result = pipeline.run(
                    root,
                    config=cfg,
                    config_hash=h,
                    now=f"2026-07-10T0{hour}:00:00Z",
                    promoter=promoter,
                )

            cache_dir = root / "runtime" / "cache" / "atomize"
            self.assertIsNotNone(result)
            # 第 6 次失敗（attempts=6 > 5）→ park；前 5 次留 split 續重試
            self.assertEqual(inner.calls, 6)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(list(cache_dir.glob("*.retries")), [])
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            self.assertTrue(any("parked" in warning for warning in result["warnings"]))
            evidence = root / "runtime" / "queue" / "_failed" / "claude__s1.json"
            self.assertTrue(evidence.exists())
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(payload["failure_category"], "transient")
            self.assertEqual(payload["attempts"], 6)
            # split fragments 保留（requeue 重試素材）
            self.assertEqual(len(list((root / "inbox" / "_slices").rglob("*.md"))), 2)

            # parked 不再佔下一輪預算：不重呼叫 LLM、無新警告
            result2 = pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T06:00:00Z", promoter=promoter,
            )
            self.assertEqual(inner.calls, 6)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertFalse(any("claude:s1" in w for w in result2["warnings"]))
```

**(b)** 整段替換 `test_transport_recovery_chatter_starts_content_retry_budget_at_one`（lines 1201-1248）：

```python
    def test_transient_then_invalid_then_valid_recovers_within_budget(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(
                root,
                [agent_exec.AgentTransientError("agent timed out after 600s")] * 2
                + ["chatter, not json", _VALID_ONE_SLICE],
            )

            for hour in range(2):
                pipeline.run(
                    root, config=cfg, config_hash=h,
                    now=f"2026-07-10T0{hour}:00:00Z", promoter=promoter,
                )

            cache_dir = root / "runtime" / "cache" / "atomize"
            result3 = pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T02:00:00Z", promoter=promoter,
            )
            # transient×2 + invalid_output×1 = attempts 3（單一預算），毒快取已淘汰
            retries = list(cache_dir.glob("*.retries"))
            self.assertEqual(inner.calls, 3)
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(len(retries), 1)
            self.assertEqual(retries[0].read_text(encoding="utf-8").strip(), "3")
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            self.assertTrue(any("retry 3/5" in warning for warning in result3["warnings"]))

            result4 = pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T03:00:00Z", promoter=promoter,
            )
            self.assertEqual(inner.calls, 4)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(result4["summary"]["slices"], 1)
            self.assertEqual(list(cache_dir.glob("*.retries")), [])
```

**(c)** 整段替換 `test_exhausted_budget_retains_poisoned_cache_and_stops_llm_calls`（lines 1272-1309）：

```python
    def test_exhausted_budget_evicts_cache_and_parks_session(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            cache_dir = root / "runtime" / "cache" / "atomize"
            cache_dir.mkdir(parents=True)
            cache_key = self._split_and_cache_key(root, cfg, h)
            (cache_dir / f"{cache_key}.retries").write_text("5", encoding="utf-8")
            inner, promoter = self._cached_llm_promoter(root, ["chatter, not json"])

            result = pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T04:00:00Z", promoter=promoter,
            )

            # 第 6 次 invalid_output → park：毒快取與 sidecar 一併淘汰
            self.assertEqual(inner.calls, 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            self.assertEqual(list(cache_dir.glob("*.retries")), [])
            self.assertTrue(any("parked" in warning for warning in result["warnings"]))
            evidence = root / "runtime" / "queue" / "_failed" / "claude__s1.json"
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(payload["failure_category"], "invalid_output")
            self.assertEqual(payload["attempts"], 6)
            self.assertEqual(payload["cache_key"], cache_key)
            # 證據保存最後一次原始輸出摘要
            self.assertIn("chatter, not json", payload["last_output_excerpt"])
            # split fragments 保留
            self.assertEqual(len(list((root / "inbox" / "_slices").rglob("*.md"))), 2)

            result2 = pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T05:00:00Z", promoter=promoter,
            )
            self.assertEqual(inner.calls, 1)
            self.assertFalse(any("claude:s1" in w for w in result2["warnings"]))
```

**(d)** 在 `PromoteFailureCacheRecoveryTests` class 內新增兩個測試：

```python
    def test_backend_unavailable_parks_immediately_without_retry(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(
                root,
                [agent_exec.AgentUnavailableError("agent command not found: claude")] * 2,
            )

            result = pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T00:00:00Z", promoter=promoter,
            )

            self.assertEqual(inner.calls, 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertTrue(any("parked" in warning for warning in result["warnings"]))
            event = processing.read_events(root)[-1]
            self.assertEqual(event["failure_category"], "backend_unavailable")
            evidence = root / "runtime" / "queue" / "_failed" / "claude__s1.json"
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(payload["failure_category"], "backend_unavailable")
            self.assertEqual(len(list((root / "inbox" / "_slices").rglob("*.md"))), 2)

            pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T01:00:00Z", promoter=promoter,
            )
            self.assertEqual(inner.calls, 1)  # parked 不佔下一輪預算

    def test_parked_session_raw_reappearance_is_not_resplit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            inner, promoter = self._cached_llm_promoter(
                root,
                [agent_exec.AgentUnavailableError("agent command not found: claude")],
            )
            pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T00:00:00Z", promoter=promoter,
            )
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")

            raw = _seed_raw(root)  # 模擬同 session 重複 import
            result = pipeline.run(
                root, config=cfg, config_hash=h,
                now="2026-07-10T01:00:00Z", promoter=promoter,
            )
            self.assertTrue(raw.exists())  # 未被 re-split／archive
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(result["summary"]["split_sessions"], 0)
```

**(e)** 檔頭 import 補 `import json`（現檔案未 import json；lines 1-16 的 import 區加一行 `import json`）。

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_atomizer_pipeline.py -k "transient or exhausted or backend_unavailable or reappearance" -v`
Expected: FAIL——park 相關斷言失敗（現行實作留在 `split`、快取保留、無 `_failed/` 證據）。

- [ ] **Step 3: 最小實作**

**(a)** `paulsha_hippo/atomizer/pipeline.py`：整段刪除 `_record_promote_failure`（lines 128-150），原位置改為：

```python
_FAILED_EVIDENCE_DIRNAME = "_failed"
_EVIDENCE_EXCERPT_MAX_CHARS = 2000


def _failed_evidence_path(memory_root: Path, session_key: str) -> Path:
    agent, _, session = session_key.partition(":")
    return (memory_root / "runtime" / "queue" / _FAILED_EVIDENCE_DIRNAME
            / f"{agent}__{session}.json")


def _read_attempts(counter: Path) -> int:
    try:
        return int(counter.read_text(encoding="utf-8").strip() or "0")
    except (FileNotFoundError, OSError, ValueError):
        return 0


def _park_session(memory_root: Path, *, session_key: str, category: str, attempts: int,
                  cache_key: str, error_text: str, now: str, config_hash: str) -> None:
    """parked 終態：證據落盤 → 淘汰毒快取＋sidecar（保留 split fragments）→ 記 ledger。

    ledger append 放最後當 commit point：中途 crash 只會多一次 bounded 重試（fail-open），
    不會留下「已 parked 但毒快取還在」的半套狀態。
    """
    cache_path = _cache_path(memory_root, cache_key)
    excerpt = ""
    if cache_path is not None and cache_path.exists():
        try:
            excerpt = cache_path.read_text(encoding="utf-8")[:_EVIDENCE_EXCERPT_MAX_CHARS]
        except (OSError, UnicodeError):
            excerpt = ""
    evidence = {
        "session_key": session_key,
        "failure_category": category,
        "attempts": attempts,
        "cache_key": cache_key,
        "error": error_text,
        "ts": now,
        "last_output_excerpt": excerpt,
    }
    _atomic_write(
        _failed_evidence_path(memory_root, session_key),
        json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n",
    )
    _clear_cache_key(memory_root, cache_key)
    _clear_retry_counter(memory_root, cache_key)
    processing.append_state(
        memory_root,
        session_key=session_key,
        state="parked",
        now=now,
        config_hash=config_hash,
        failure_category=category,
        attempts=attempts,
        cache_key=cache_key,
        error=error_text,
    )


def _handle_promote_failure(
    memory_root: Path,
    promoter: Promoter,
    fragments: list[Fragment],
    exc: "PromoteError",
    *,
    session_key: str,
    now: str,
    config_hash: str,
) -> tuple[str, bool]:
    """#15 失敗分類：backend_unavailable 立即 park；transient/invalid_output 記入單一
    attempts 預算（沿用 _LLM_PROMOTE_MAX_RETRIES），invalid_output 每次先淘汰毒快取，
    超限 park。回傳 (警告註記, 是否已 park)。非 LLM promoter 沿用既有 left-in-split。"""
    if not isinstance(promoter, LLMPromoter) or not fragments:
        return "", False
    category = getattr(exc, "category", "invalid_output")
    if category not in processing.PARKED_FAILURE_CATEGORIES:
        category = "invalid_output"
    error_text = processing.sanitize_error_text(str(exc))
    cache_key = promoter.cache_key_for_fragments(fragments)
    counter = _retry_counter_path(memory_root, cache_key)
    if counter is None:
        return "", False
    attempts = _read_attempts(counter)

    if category == "backend_unavailable":
        _park_session(
            memory_root, session_key=session_key, category=category,
            attempts=attempts, cache_key=cache_key, error_text=error_text,
            now=now, config_hash=config_hash,
        )
        return " (parked: backend_unavailable; 不重試，修復後 hippo requeue)", True

    attempts += 1
    if attempts > _LLM_PROMOTE_MAX_RETRIES:
        _park_session(
            memory_root, session_key=session_key, category=category,
            attempts=attempts, cache_key=cache_key, error_text=error_text,
            now=now, config_hash=config_hash,
        )
        return f" (parked: {category} after {attempts} attempts; cache evicted)", True

    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text(str(attempts), encoding="utf-8")
    if category == "invalid_output":
        promoter.clear_cache_for_fragments(fragments)
        return f" (cache cleared; retry {attempts}/{_LLM_PROMOTE_MAX_RETRIES})", False
    return f" (transient failure; retry {attempts}/{_LLM_PROMOTE_MAX_RETRIES})", False
```

**(b)** `_promote_fragments`（lines 264-274）的 fallback 包裝改為（unexpected → transient，保守可重試）：

```python
def _promote_fragments(
    promoter: Promoter,
    fragments: list[Fragment],
    config: AtomizerConfig,
) -> list[slice_frontmatter.Slice]:
    try:
        return promoter.promote(fragments, config)
    except PromoteError:
        raise
    except Exception as exc:
        raise PromoteError(f"unexpected promoter failure: {exc}", category="transient") from exc
```

**(c)** split pass 跳過 parked（line 322）改為：

```python
        if processing.state_of(memory_root, session_key) in {"split", "promoted", "parked"}:
            continue
```

**(d)** promote pass 失敗分支（lines 462-471）改為：

```python
        try:
            promoted = _promote_fragments(promoter, [fragment for _, fragment in fragments], config)
        except PromoteError as exc:
            note, parked = _handle_promote_failure(
                memory_root,
                promoter,
                [fragment for _, fragment in fragments],
                exc,
                session_key=session_key,
                now=now,
                config_hash=config_hash,
            )
            outcome = "parked" if parked else "left in split"
            warnings.append(f"{session_key}: {exc}; session {session_key} {outcome}{note}")
            continue
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_atomizer_pipeline.py -v`
Expected: 全 PASS（含未動的既有測試：`test_llm_garbage_leaves_session_split_without_knowledge_files`＝attempts 1 ≤ 5 留 split；`test_non_llm_promoter_failure_does_not_touch_cache_dir`＝非 LLM promoter 短路；`test_dry_run_existing_split_session_leaves_retry_budget_and_cache_untouched`＝dry-run 不進失敗處理）。

- [ ] **Step 5: 全套回歸**

Run: `python3 -m pytest tests/ -q`
Expected: 全 PASS（`test_atomizer_e2e.py`／`test_dream_e2e.py` 等不受影響）。

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/atomizer/pipeline.py tests/test_atomizer_pipeline.py
git commit -m "feat(atomizer): promote 失敗分類——parked 終態、毒快取淘汰、_failed 證據（#15）"
```

---

### Task 5: `hippo requeue`——parked 恢復路徑

**Files:**
- Create: `paulsha_hippo/requeue.py`
- Modify: `paulsha_hippo/cli.py:233-237` 之後（`usage_p` 區塊後新增 requeue subparser）、`paulsha_hippo/cli.py:794` 附近（新增 `_requeue` handler，放 `_dream_supervise` 之前）
- Test: `tests/test_requeue.py`（新檔）

**Interfaces:**
- Consumes: Task 1 的 requeue 事件契約（`state="split"` + `requeued_from="parked"` + `requeue_reason:str`）、既有 `processing.fold_events`／`append_state`。
- Produces（Task 11／使用者依賴）:
  - `requeue.requeue(memory_root: Path, *, session_key: str | None = None, all_parked: bool = False, now: str, reason: str = "") -> dict[str, Any]`，回傳 `{"requeued": [{"session_key","previous_failure_category","fragments"}], "skipped": [{"session_key","reason"}]}`
  - CLI：`hippo requeue <session-key> --memory-root <path> [--reason <text>] [--now <ts>]` 與 `hippo requeue --all-parked --memory-root <path>`；兩者擇一必填（都給或都不給 → exit 2）；有目標但全部 skip → exit 1；其餘 exit 0
  - `_failed/` 證據檔 requeue 後保留（歷史紀錄，不清除）

- [ ] **Step 1: 寫失敗測試（新檔完整內容）**

`tests/test_requeue.py`：

```python
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli, requeue
from paulsha_hippo.ledger import processing


def _park(root: Path, session_key: str, *, category: str = "invalid_output") -> None:
    processing.append_state(
        root, session_key=session_key, state="parked",
        now="2026-07-10T00:00:00Z", config_hash="cfg-hash",
        failure_category=category, attempts=6,
        cache_key=f"{session_key}__{'a' * 64}", error="boom",
    )


def _seed_fragment(root: Path, session_key: str) -> None:
    agent, _, session = session_key.partition(":")
    frag = root / "inbox" / "_slices" / "proj" / f"{agent}__{session}__000.md"
    frag.parent.mkdir(parents=True, exist_ok=True)
    frag.write_text("---\nfragment_index: 0\n---\nbody\n", encoding="utf-8")


class RequeueCoreTests(unittest.TestCase):
    def test_requeue_single_parked_session_returns_to_split(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            _seed_fragment(root, "claude:s1")

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
                reason="backend fixed",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            self.assertEqual(summary["skipped"], [])
            self.assertEqual(
                summary["requeued"],
                [{"session_key": "claude:s1",
                  "previous_failure_category": "invalid_output",
                  "fragments": 1}],
            )
            event = processing.read_events(root)[-1]
            self.assertEqual(event["state"], "split")
            self.assertEqual(event["requeued_from"], "parked")
            self.assertEqual(event["requeue_reason"], "backend fixed")
            self.assertEqual(event["atomizer_config_hash"], "cfg-hash")

    def test_requeue_non_parked_session_is_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            processing.append_state(
                root, session_key="claude:s2", state="split",
                now="2026-07-10T00:00:00Z", config_hash="h",
            )
            summary = requeue.requeue(
                root, session_key="claude:s2", now="2026-07-10T01:00:00Z",
            )
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"], [{"session_key": "claude:s2", "reason": "split"}]
            )

    def test_requeue_unknown_session_is_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = requeue.requeue(
                root, session_key="claude:ghost", now="2026-07-10T01:00:00Z",
            )
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:ghost", "reason": "unknown session"}],
            )

    def test_requeue_all_parked_targets_only_parked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:p1", category="transient")
            _park(root, "codex:p2", category="backend_unavailable")
            processing.append_state(
                root, session_key="claude:live", state="split",
                now="2026-07-10T00:00:00Z", config_hash="h",
            )

            summary = requeue.requeue(root, all_parked=True, now="2026-07-10T01:00:00Z")

            self.assertEqual(
                [entry["session_key"] for entry in summary["requeued"]],
                ["claude:p1", "codex:p2"],
            )
            self.assertEqual(processing.state_of(root, "claude:p1"), "split")
            self.assertEqual(processing.state_of(root, "codex:p2"), "split")
            self.assertEqual(summary["skipped"], [])


class RequeueCliTests(unittest.TestCase):
    def _run_cli(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(argv)
        return rc, buf.getvalue()

    def test_cli_requeue_single(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            rc, out = self._run_cli(
                ["requeue", "claude:s1", "--memory-root", str(root),
                 "--now", "2026-07-10T01:00:00Z", "--reason", "backend fixed"]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertEqual(payload["requeued"][0]["session_key"], "claude:s1")
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")

    def test_cli_requires_exactly_one_selector(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, _ = self._run_cli(["requeue", "--memory-root", str(root)])
            self.assertEqual(rc, 2)
            rc2, _ = self._run_cli(
                ["requeue", "claude:s1", "--all-parked", "--memory-root", str(root)]
            )
            self.assertEqual(rc2, 2)

    def test_cli_exit_1_when_target_not_requeued(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out = self._run_cli(
                ["requeue", "claude:ghost", "--memory-root", str(root)]
            )
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(out)["requeued"], [])

    def test_cli_all_parked_with_zero_parked_is_ok(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out = self._run_cli(
                ["requeue", "--all-parked", "--memory-root", str(root)]
            )
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"requeued": [], "skipped": []})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_requeue.py -v`
Expected: FAIL——`ImportError: cannot import name 'requeue' from 'paulsha_hippo'`。

- [ ] **Step 3: 實作 `paulsha_hippo/requeue.py`（新檔完整內容）**

```python
"""Requeue parked sessions back to split（#15 恢復路徑）。

parked 是顯性終態而非死路：backend 修復後由本模組把 session 送回 split，
讓下一輪 promote 重走。事件契約（跨批次共享）：
state="split" + requeued_from="parked" + requeue_reason。
`runtime/queue/_failed/` 證據檔保留（歷史紀錄，不清除）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .ledger import processing


def _fragment_count(memory_root: Path, session_key: str) -> int:
    agent, _, session = session_key.partition(":")
    slices_dir = memory_root / "inbox" / "_slices"
    return len(list(slices_dir.rglob(f"{agent}__{session}__*.md")))


def requeue(
    memory_root: Path,
    *,
    session_key: str | None = None,
    all_parked: bool = False,
    now: str,
    reason: str = "",
) -> dict[str, Any]:
    events = processing.fold_events(memory_root)
    if all_parked:
        targets = sorted(
            key for key, event in events.items() if event.get("state") == "parked"
        )
    else:
        targets = [session_key] if session_key else []

    requeued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for key in targets:
        event = events.get(key)
        state = str(event.get("state", "")) if event else ""
        if state != "parked":
            skipped.append({"session_key": key, "reason": state or "unknown session"})
            continue
        processing.append_state(
            memory_root,
            session_key=key,
            state="split",
            now=now,
            config_hash=str(event.get("atomizer_config_hash", "")),
            requeued_from="parked",
            requeue_reason=reason,
        )
        requeued.append(
            {
                "session_key": key,
                "previous_failure_category": str(event.get("failure_category", "")),
                "fragments": _fragment_count(memory_root, key),
            }
        )
    return {"requeued": requeued, "skipped": skipped}
```

- [ ] **Step 4: 接 CLI**

`paulsha_hippo/cli.py`——在 `usage_p.set_defaults(func=_memory_usage)`（line 237）之後、`return parser` 之前插入：

```python
    requeue_p = memory_subparsers.add_parser(
        "requeue", help="把 parked session 送回 split 重走 promote（#15 恢復路徑）"
    )
    requeue_p.add_argument("session_key", nargs="?", default=None,
                           help="session key（如 claude:s1）；與 --all-parked 擇一")
    requeue_p.add_argument("--all-parked", action="store_true",
                           help="requeue 全部 parked sessions")
    requeue_p.add_argument("--memory-root", required=True)
    requeue_p.add_argument("--reason", default="",
                           help="requeue 原因（記入 ledger requeue_reason）")
    requeue_p.add_argument("--now", default=None)
    requeue_p.set_defaults(func=_requeue)
```

在 `_dream_supervise`（line 794）之前插入 handler：

```python
def _requeue(args: argparse.Namespace) -> int:
    from . import requeue as requeue_mod

    if bool(args.session_key) == bool(args.all_parked):
        print("error: 需指定 <session-key> 或 --all-parked（擇一）", file=sys.stderr)
        return 2
    root = Path(args.memory_root)
    now = (args.now or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")
    summary = requeue_mod.requeue(
        root,
        session_key=args.session_key,
        all_parked=args.all_parked,
        now=now,
        reason=args.reason,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if not summary["requeued"] and summary["skipped"]:
        return 1
    return 0
```

（`datetime`／`timezone`／`json`／`sys`／`Path` 皆已在 `cli.py` 檔頭 import，無需新增。）

- [ ] **Step 5: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_requeue.py tests/test_cli.py -v`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/requeue.py paulsha_hippo/cli.py tests/test_requeue.py
git commit -m "feat(cli): hippo requeue——parked session 回 split 恢復路徑（#15）"
```

---

### Task 6: global dream lock——單一 dream writer

**Files:**
- Create: `paulsha_hippo/dream/lock.py`
- Modify: `paulsha_hippo/dream/cli.py:1-16`（import）、`paulsha_hippo/dream/cli.py:19-87`（`_run` 整輪持鎖）
- Test: `tests/test_dream_lock.py`（新檔）、`tests/test_dream_cli.py`（加 2 測試）

**Interfaces:**
- Consumes: 既有 `runtime/locks/` 目錄慣例（`paulsha_hippo/importer/pipeline.py:367`）、`fcntl.flock`。
- Produces（PR-C doctor 依賴——共享契約 3）:
  - `paulsha_hippo.dream.lock.dream_lock_path(memory_root: Path) -> Path`——固定回傳 `<memory_root>/runtime/locks/dream.lock`
  - `paulsha_hippo.dream.lock.acquire_dream_lock(memory_root: Path) -> IO[str] | None`——`LOCK_EX|LOCK_NB`；成功回傳持鎖 handle（caller `close()` 即釋放），已被持有回傳 `None`
  - `dream run` 取不到鎖時 stdout 輸出 `{"skipped": "dream lock held by another process", "lock_path": ...}` 且 exit 0；`dream status` 不取鎖

- [ ] **Step 1: 寫失敗測試**

**(a)** `tests/test_dream_lock.py`（新檔完整內容）：

```python
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.dream import lock


class DreamLockTests(unittest.TestCase):
    def test_lock_path_is_fixed_contract_path(self):
        root = Path("/tmp-any")
        self.assertEqual(
            lock.dream_lock_path(root), root / "runtime" / "locks" / "dream.lock"
        )

    def test_acquire_creates_lock_file_and_returns_handle(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handle = lock.acquire_dream_lock(root)
            self.assertIsNotNone(handle)
            self.assertTrue(lock.dream_lock_path(root).exists())
            handle.close()

    def test_second_acquire_fails_while_held_then_succeeds_after_release(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = lock.acquire_dream_lock(root)
            self.assertIsNotNone(first)
            self.assertIsNone(lock.acquire_dream_lock(root))
            first.close()
            second = lock.acquire_dream_lock(root)
            self.assertIsNotNone(second)
            second.close()

    def test_lock_file_is_never_unlinked_on_release(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handle = lock.acquire_dream_lock(root)
            handle.close()
            # flock rendezvous inode 永不 unlink（#19：執行中 unlink 會破壞互斥）
            self.assertTrue(lock.dream_lock_path(root).exists())


if __name__ == "__main__":
    unittest.main()
```

**(b)** `tests/test_dream_cli.py`——檔頭 import 區加 `from paulsha_hippo.dream import lock as dream_lock`，`DreamCliTests` class 內新增：

```python
    def test_dream_run_skips_when_lock_held(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            holder = dream_lock.acquire_dream_lock(root)
            self.assertIsNotNone(holder)
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    rc = cli.main(["dream", "run", "--memory-root", str(root),
                                   "--now", "2026-07-10T00:00:00Z"])
            finally:
                holder.close()
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload.get("skipped"), "dream lock held by another process")
            self.assertIsNone(dream.last_run(root))

    def test_dream_run_releases_lock_after_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            patches = dict(
                atomizer=patch(
                    "paulsha_hippo.dream.cli.atomizer_config.load_config",
                    return_value=(SimpleNamespace(default_promoter="identity"),
                                  "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                ),
                janitor=patch(
                    "paulsha_hippo.dream.cli.janitor_config.load_config",
                    return_value=(SimpleNamespace(),
                                  "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
                ),
            )
            for round_now in ("2026-07-10T00:00:00Z", "2026-07-10T01:00:00Z"):
                buf = io.StringIO()
                with patches["atomizer"], patches["janitor"], redirect_stdout(buf):
                    rc = cli.main(["dream", "run", "--memory-root", str(root),
                                   "--now", round_now, "--dry-run"])
                self.assertEqual(rc, 0)
                payload = json.loads(buf.getvalue())
                self.assertNotIn("skipped", payload)  # 第二輪未被殘留鎖擋住
                self.assertIn("passes", payload)
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_dream_lock.py tests/test_dream_cli.py -v`
Expected: FAIL——`ModuleNotFoundError: No module named 'paulsha_hippo.dream.lock'`。

- [ ] **Step 3: 實作 `paulsha_hippo/dream/lock.py`（新檔完整內容）**

```python
"""Global dream singleton lock（#19／#15 失敗鏈——單一 dream writer）。

固定路徑 <memory_root>/runtime/locks/dream.lock（跨批次共享契約：PR-C doctor
引用同一路徑報告持鎖狀態）。dream run 入口以 flock(LOCK_EX|LOCK_NB) 整輪持有；
lock 檔是 flock rendezvous inode，永不 unlink（unlink 會破壞互斥）。
"""
from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO


def dream_lock_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "locks" / "dream.lock"


def acquire_dream_lock(memory_root: Path) -> IO[str] | None:
    """Non-blocking 全域 dream lock。

    成功回傳持鎖 handle（caller close() 即釋放）；已被他人持有回傳 None。
    """
    path = dream_lock_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle
```

- [ ] **Step 4: `dream/cli.py` 接鎖——`_run` 完整改寫**

`paulsha_hippo/dream/cli.py` line 16 的 `from . import orchestrator` 後加 `from . import lock as dream_lock`；`_run`（lines 19-87）整段改為：

```python
def _run(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root)

    # #19/#15：global dream singleton——整輪持有 nonblocking flock；
    # 取不到鎖代表另一個 dream run 進行中，記 log 後 skip（exit 0），不得競寫。
    lock_handle = dream_lock.acquire_dream_lock(memory_root)
    if lock_handle is None:
        print(
            json.dumps(
                {
                    "skipped": "dream lock held by another process",
                    "lock_path": str(dream_lock.dream_lock_path(memory_root)),
                },
                sort_keys=True,
            )
        )
        return 0
    try:
        if args.require_idle and not idle.is_idle(max_load=args.max_load):
            print(
                json.dumps(
                    {
                        "skipped": "system busy",
                        "backlog_depth": dream_ledger.backlog_depth(memory_root),
                    },
                    sort_keys=True,
                )
            )
            return 0

        atom_cfg, atom_hash = atomizer_config.load_config()
        jan_cfg, jan_hash = janitor_config.load_config()
        promoter = atomizer_cli._build_promoter(args, atom_cfg, memory_root)
        now = args.now
        doc_corpus = corpus_for_roots(getattr(args, "instruction_root", None))

        def atomize_fn() -> dict[str, object]:
            return atomizer_pipeline.run(
                memory_root,
                config=atom_cfg,
                config_hash=atom_hash,
                now=now,
                dry_run=args.dry_run,
                promoter=promoter,
                doc_corpus=doc_corpus,
            )

        def janitor_fn() -> dict[str, object]:
            # In the dream/service context the provenance source repos are usually
            # not checked out at the run CWD, so a CWD-relative path probe gives
            # false negatives and would spuriously decay freshly atomized knowledge.
            # Return None (cannot determine) so source_invalid decay is disabled here;
            # TTL and supersede decay still apply.
            return janitor_scanner.run_scan(
                memory_root=memory_root,
                knowledge_root=memory_root / "knowledge",
                config=jan_cfg,
                config_hash=jan_hash,
                now=now,
                dry_run=args.dry_run,
                source_path_exists=lambda record: None,
            )

        def moc_fn() -> dict[str, object]:
            if args.dry_run:
                return {"summary": {"skipped": "dry-run"}, "warnings": []}
            result = moc_runner.run_moc(memory_root, now)
            warnings = result.pop("warnings", [])
            return {
                "summary": result,
                "warnings": warnings,
            }

        result = orchestrator.run_dream(
            memory_root,
            atomize_fn=atomize_fn,
            janitor_fn=janitor_fn,
            moc_fn=moc_fn,
            now=now,
            config_hash=f"{atom_hash[:8]}:{jan_hash[:8]}",
            dry_run=args.dry_run,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    finally:
        lock_handle.close()
```

- [ ] **Step 5: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_dream_lock.py tests/test_dream_cli.py tests/test_dream_e2e.py -v`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/dream/lock.py paulsha_hippo/dream/cli.py tests/test_dream_lock.py tests/test_dream_cli.py
git commit -m "feat(dream): dream run 全域 singleton flock——單一 dream writer（#19）"
```

---

### Task 7: orchestrator bounded error + errno（反轉 redaction 測試）

**Files:**
- Modify: `paulsha_hippo/dream/orchestrator.py:15`（import）、`paulsha_hippo/dream/orchestrator.py:21-37`（`_error_category` → `_error_entry`；`_run_pass` except 分支）
- Test: `tests/test_dream_orchestrator.py`

**Interfaces:**
- Consumes: Task 1 的 `processing.sanitize_error_text(text) -> str`。
- Produces（dream ledger 消費者／收口批次依賴）:
  - pass 失敗紀錄 schema：`record["passes"][name] == {"error": <ExceptionClassName:str>, "error_message": <≤500字元去敏:str>, "errno": <int|None>}`（errno 取自例外本身，缺則沿 `__cause__` 鏈取一層）
  - `record["errors"]` 條目格式維持 `f"{name}:{ExceptionClassName}"` 不變

- [ ] **Step 1: 改寫既有測試＋新增**

`tests/test_dream_orchestrator.py`——**(a)** `test_janitor_runs_even_if_atomize_raises`（lines 46-70）內兩行斷言改為：

```python
            self.assertEqual(
                record["passes"]["atomize"],
                {"error": "RuntimeError", "error_message": "boom", "errno": None},
            )
            self.assertEqual(record["errors"], ["atomize:RuntimeError"])
```

**(b)** 整段替換 `test_failure_record_redacts_exception_message`（lines 178-197）：

```python
    def test_failure_record_keeps_bounded_error_message_and_errno(self):
        def atomize_fn():
            raise OSError(36, "File name too long: " + "x" * 600)

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            record = orchestrator.run_dream(
                Path(tmpdir),
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-07-10T00:00:00Z",
                config_hash="cfg",
            )

            atomize = record["passes"]["atomize"]
            self.assertEqual(atomize["error"], "OSError")
            self.assertEqual(atomize["errno"], 36)
            self.assertIn("File name too long", atomize["error_message"])
            self.assertLessEqual(len(atomize["error_message"]), 500)
            self.assertEqual(record["errors"], ["atomize:OSError"])
```

**(c)** class 內新增：

```python
    def test_errno_extracted_from_cause_chain(self):
        def atomize_fn():
            try:
                raise OSError(2, "No such file or directory")
            except OSError as exc:
                raise RuntimeError("wrapper") from exc

        def janitor_fn():
            return {"summary": {"skipped": 0}, "warnings": []}

        with TemporaryDirectory() as tmpdir:
            record = orchestrator.run_dream(
                Path(tmpdir),
                atomize_fn=atomize_fn,
                janitor_fn=janitor_fn,
                now="2026-07-10T00:00:00Z",
                config_hash="cfg",
            )

            atomize = record["passes"]["atomize"]
            self.assertEqual(atomize["error"], "RuntimeError")
            self.assertEqual(atomize["errno"], 2)
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_dream_orchestrator.py -v`
Expected: FAIL——`passes["atomize"]` 現值只有 `{"error": "RuntimeError"}`，缺 `error_message`／`errno`。

- [ ] **Step 3: 最小實作**

`paulsha_hippo/dream/orchestrator.py` line 15 後加 import：

```python
from paulsha_hippo.ledger import processing as processing_ledger
```

`_error_category`（lines 21-22）整段換成：

```python
def _error_entry(exc: Exception) -> dict[str, Any]:
    """#19 錯誤可見性：bounded 訊息（≤500、去敏）＋ errno，不再只存類別名。"""
    errno_value = getattr(exc, "errno", None)
    if errno_value is None and exc.__cause__ is not None:
        errno_value = getattr(exc.__cause__, "errno", None)
    return {
        "error": type(exc).__name__,
        "error_message": processing_ledger.sanitize_error_text(str(exc)),
        "errno": errno_value if isinstance(errno_value, int) else None,
    }
```

`_run_pass` 的 except 分支（lines 33-37）改為：

```python
    except Exception as exc:  # noqa: BLE001 - orchestration boundary
        entry = _error_entry(exc)
        passes[name] = entry
        errors.append(f"{name}:{entry['error']}")
        return False
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_dream_orchestrator.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/dream/orchestrator.py tests/test_dream_orchestrator.py
git commit -m "feat(dream): orchestrator 保存 bounded 錯誤訊息與 errno（#19 錯誤可見性）"
```

---

### Task 8: `ops.resolve_backend_argv` + `hippo init` argv[0] 絕對路徑化

**Files:**
- Modify: `paulsha_hippo/ops.py:6-20`（import 區——`os`/`shutil`/`sys` 已有，無需新增；`_BACKENDS` 下方加例外與函式）、`paulsha_hippo/ops.py:56-81`（`run_init` 的 claude-headless override 分支）
- Test: `tests/test_ops.py`

**Interfaces:**
- Consumes: stdlib `shutil.which`、`os.path.abspath`。
- Produces（**跨批次共享契約 2——PR-D 重用**；Task 9/10 依賴）:
  - `class BackendUnavailableError(ValueError)`（`paulsha_hippo/ops.py`）
  - `resolve_backend_argv(argv: list[str]) -> list[str]`——argv[0] 以 `shutil.which` 絕對路徑化（`os.path.abspath` 正規化、不 resolve symlink——nvm shim 需保留）；空 argv 或找不到 executable → raise `BackendUnavailableError`
  - `run_init(backend="claude-headless")` 寫入 override 的 command 首項為絕對路徑；backend 不可解析 → stderr 訊息 + exit 2、不寫 override

- [ ] **Step 1: 改寫既有 init 測試＋新增 resolve 測試**

`tests/test_ops.py`——**(a)** `test_init_claude_headless_writes_config_and_override`（lines 18-36）整段替換：

```python
    def test_init_claude_headless_writes_config_and_override(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops.shutil, "which", return_value="/fake/bin/claude"):
                rc = ops.run_init(
                    memory_root=None, backend="claude-headless",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 0)
            cfg = Path(tmp) / "hippo-cfg" / "config.yaml"
            self.assertIn("backend: claude-headless", cfg.read_text(encoding="utf-8"))
            override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
            body = override.read_text(encoding="utf-8")
            # #15：argv[0] 絕對路徑化——systemd 環境沒有 NVM PATH，裸命令找不到
            self.assertIn("- /fake/bin/claude", body)
            self.assertIn("- -p", body)
            self.assertNotIn("\n    - claude\n", body)

    def test_init_claude_headless_fails_when_backend_missing(self):
        with TemporaryDirectory() as tmp:
            env = {
                "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
                "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
                "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            }
            with mock.patch.dict("os.environ", env), \
                 mock.patch.object(ops.shutil, "which", return_value=None):
                rc = ops.run_init(
                    memory_root=None, backend="claude-headless",
                    base_url=None, api_key_env=None, model=None, assume_yes=True,
                )
            self.assertEqual(rc, 2)
            override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
            self.assertFalse(override.exists())
```

**(b)** 新增 class：

```python
class ResolveBackendArgvTests(unittest.TestCase):
    def test_resolves_bare_command_to_absolute(self):
        with mock.patch.object(ops.shutil, "which", return_value="/usr/bin/claude"):
            self.assertEqual(
                ops.resolve_backend_argv(["claude", "-p"]), ["/usr/bin/claude", "-p"]
            )

    def test_missing_command_raises_backend_unavailable(self):
        with mock.patch.object(ops.shutil, "which", return_value=None):
            with self.assertRaises(ops.BackendUnavailableError):
                ops.resolve_backend_argv(["nope-cmd"])

    def test_error_is_value_error_subclass(self):
        self.assertTrue(issubclass(ops.BackendUnavailableError, ValueError))

    def test_empty_argv_raises(self):
        with self.assertRaises(ops.BackendUnavailableError):
            ops.resolve_backend_argv([])
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_ops.py -k "resolve or init_claude" -v`
Expected: FAIL——`AttributeError: module 'paulsha_hippo.ops' has no attribute 'resolve_backend_argv'`；init 測試斷言 `- /fake/bin/claude` 失敗（現行寫裸 `- claude`）。

- [ ] **Step 3: 最小實作**

`paulsha_hippo/ops.py`：`_BACKENDS`（line 20）之後加：

```python
class BackendUnavailableError(ValueError):
    """backend argv[0] 在目前環境解析不到可執行檔（#15；PR-D preset registry 重用）。"""


def resolve_backend_argv(argv: list[str]) -> list[str]:
    """argv[0] 以 shutil.which 絕對路徑化（跨批次共享契約：PR-A 建立、PR-D 重用）。

    - 以 os.path.abspath 正規化、不 resolve symlink（nvm shim 需保留原 symlink 層）。
    - 空 argv 或 which 找不到 → BackendUnavailableError（ValueError 子類）。
    """
    if not argv or not argv[0]:
        raise BackendUnavailableError("backend argv 不可為空")
    resolved = shutil.which(argv[0])
    if resolved is None:
        raise BackendUnavailableError(f"backend executable 找不到：{argv[0]}")
    return [os.path.abspath(resolved), *argv[1:]]
```

`run_init` 的 claude-headless 分支（lines 58-64）改為：

```python
    if backend == "claude-headless":
        try:
            argv = resolve_backend_argv(["claude", "-p"])
        except BackendUnavailableError as exc:
            print(f"init: {exc}（請先安裝 claude CLI，或改用 --backend openai-compatible/custom-argv）",
                  file=sys.stderr)
            return 2
        override_body = (
            "schema_version: \"1\"\n"
            "agent_exec:\n"
            "  command:\n"
            + "".join(f"    - {token}\n" for token in argv)
            + (f"  model: {model}\n" if model else "")
        )
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_ops.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ops.py tests/test_ops.py
git commit -m "feat(ops): resolve_backend_argv + init 寫入絕對路徑 backend argv（#15/#10）"
```

---

### Task 9: `hippo doctor` service-effective backend probe

**Files:**
- Modify: `paulsha_hippo/ops.py:94-121`（`run_doctor`——claude CLI 行之後、return 之前插入 probe；新增兩個 helper 放 `run_doctor` 之後）
- Test: `tests/test_ops.py`

**Interfaces:**
- Consumes: Task 8 的 `resolve_backend_argv` 慣例（本 task 只讀不解析寫回）、既有 `atomizer_config.load_config()`／`atomizer_config.resolve_command_argv()`（`paulsha_hippo/atomizer/config.py:237/194`）。
- Produces（Task 10／恢復序列 gate 依賴）:
  - `_service_effective_path_env() -> str`——`systemctl --user show-environment` 的 `PATH=` 值；取不到退 `"/usr/local/bin:/usr/bin:/bin"`
  - `_probe_backend_service_effective() -> tuple[str, bool]`——回傳 `(報告行, is_failure)`；argv backend 在 service-effective PATH 解析不到→ FAIL（dream service template 固定 `--promoter llm`，故不因 config promoter 軟化）；`openai-compatible` → 非 argv backend，回報「probe 由 PR-D preset 接手」不 FAIL
  - `run_doctor()` 输出新增一行 `- distiller backend：...`；probe FAIL 時 exit 1

- [ ] **Step 1: 寫失敗測試＋隔離既有 DoctorTests**

`tests/test_ops.py` 檔頭 import 區補 `from types import SimpleNamespace`。

**(a)** 既有 `DoctorTests`（lines 54-63）只驗雙 root 一致性，但 doctor 新增 probe 後會走真實 `load_config()`（可能吃到執行機器上的真實 `~/.config/paulshaclaw/atomizer.override.yaml`，結果不確定）。兩個既有測試改為 patch 掉 probe，維持原測試意圖：

```python
class DoctorTests(unittest.TestCase):
    _PROBE_OK = ("- distiller backend：✓ mocked", False)

    def test_conflicting_roots_fail(self):
        env = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/b"}
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(ops, "_probe_backend_service_effective",
                               return_value=self._PROBE_OK):
            self.assertEqual(ops.run_doctor(), 1)

    def test_consistent_roots_pass(self):
        env = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a"}
        with mock.patch.dict("os.environ", env), \
             mock.patch.object(ops, "_probe_backend_service_effective",
                               return_value=self._PROBE_OK):
            self.assertEqual(ops.run_doctor(), 0)
```

**(b)** 新增 class：

```python
class DoctorBackendProbeTests(unittest.TestCase):
    _ENV = {"HIPPO_MEMORY_ROOT": "/a", "PSC_MEMORY_ROOT": "/a"}

    def _fake_cfg(self, **overrides):
        base = dict(
            agent_exec_backend="custom-argv",
            agent_exec_command=("claude", "-p"),
            agent_exec_base_url="",
            default_promoter="llm",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_probe_fails_when_bare_command_unresolvable_in_service_path(self):
        with mock.patch.dict("os.environ", self._ENV), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(self._fake_cfg(), "h")), \
             mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                        side_effect=lambda command: tuple(command)), \
             mock.patch.object(ops, "_service_effective_path_env",
                               return_value="/usr/bin:/bin"), \
             mock.patch.object(ops.shutil, "which", return_value=None):
            self.assertEqual(ops.run_doctor(), 1)

    def test_probe_passes_with_absolute_executable(self):
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "claude"
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
            exe.chmod(0o755)
            cfg = self._fake_cfg(agent_exec_command=(str(exe), "-p"))
            with mock.patch.dict("os.environ", self._ENV), \
                 mock.patch("paulsha_hippo.atomizer.config.load_config",
                            return_value=(cfg, "h")), \
                 mock.patch("paulsha_hippo.atomizer.config.resolve_command_argv",
                            side_effect=lambda command: tuple(command)), \
                 mock.patch.object(ops, "_service_effective_path_env",
                                   return_value="/usr/bin:/bin"):
                self.assertEqual(ops.run_doctor(), 0)

    def test_probe_reports_openai_compatible_as_delegated(self):
        cfg = self._fake_cfg(agent_exec_backend="openai-compatible",
                             agent_exec_base_url="http://127.0.0.1:11434")
        with mock.patch.dict("os.environ", self._ENV), \
             mock.patch("paulsha_hippo.atomizer.config.load_config",
                        return_value=(cfg, "h")):
            self.assertEqual(ops.run_doctor(), 0)

    def test_service_effective_path_falls_back_without_systemd(self):
        with mock.patch.object(ops.subprocess, "run", side_effect=OSError("no systemctl")):
            self.assertEqual(ops._service_effective_path_env(), "/usr/local/bin:/usr/bin:/bin")
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_ops.py -k "DoctorBackendProbe" -v`
Expected: FAIL——`AttributeError: ... has no attribute '_service_effective_path_env'`；且 `test_probe_fails...` 得到 0（現行 doctor 無 probe）。

- [ ] **Step 3: 最小實作**

`paulsha_hippo/ops.py`：`run_doctor` 之後新增：

```python
def _service_effective_path_env() -> str:
    """systemd --user 服務實際看到的 PATH（非互動 shell；#15 根因是 NVM PATH 不在其中）。

    取不到（無 systemd／指令失敗）退保守預設。"""
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True, text=True,
        )
    except OSError:
        completed = None
    if completed is not None and completed.returncode == 0:
        for line in completed.stdout.splitlines():
            if line.startswith("PATH="):
                return line[len("PATH="):]
    return "/usr/local/bin:/usr/bin:/bin"


def _probe_backend_service_effective() -> tuple[str, bool]:
    """以 service-effective 環境驗證 atomizer backend 可執行。回傳 (報告行, is_failure)。

    dream service template 固定 --promoter llm，故 argv backend 解析不到一律 FAIL，
    不因 config 的 default promoter 軟化。openai-compatible 非 argv backend，
    probe 由 PR-D preset registry 接手。"""
    from paulsha_hippo.atomizer import config as atomizer_config

    try:
        cfg, _ = atomizer_config.load_config()
    except Exception as exc:  # config 壞掉本身就是 backend 不可用級的問題
        return f"FAIL distiller backend config 無法載入：{exc}", True
    if cfg.agent_exec_backend == "openai-compatible":
        return (
            f"- distiller backend：openai-compatible（{cfg.agent_exec_base_url}；"
            "probe 由 PR-D preset 接手）",
            False,
        )
    command = atomizer_config.resolve_command_argv(cfg.agent_exec_command)
    argv0 = command[0]
    if Path(argv0).is_absolute():
        ok = Path(argv0).is_file() and os.access(argv0, os.X_OK)
    else:
        ok = shutil.which(argv0, path=_service_effective_path_env()) is not None
    if ok:
        return f"- distiller backend：✓ {argv0}（service-effective 可執行）", False
    return (
        f"FAIL distiller backend：{argv0} 在 service-effective 環境解析不到"
        "（hippo doctor --fix-backend 可嘗試自動遷移）",
        True,
    )
```

`run_doctor`（lines 94-121）在 `agent = shutil.which("claude")` 兩行之後、`return 1 if failed else 0` 之前插入：

```python
    probe_line, probe_failed = _probe_backend_service_effective()
    if probe_failed:
        print(probe_line, file=sys.stderr)
        failed = True
    else:
        print(probe_line)
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_ops.py -v`
Expected: 全 PASS（既有 `DoctorTests` 已在 Step 1(a) 隔離 probe，不受執行機器的真實 override 影響）。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ops.py tests/test_ops.py
git commit -m "feat(ops): doctor 以 service-effective PATH probe 蒸餾 backend（#15/#10）"
```

---

### Task 10: `hippo doctor --fix-backend`——既有設定冪等 migration

**Files:**
- Modify: `paulsha_hippo/ops.py`（`import re` 加入檔頭 import 區；`run_doctor` 簽名改 `run_doctor(*, fix_backend: bool = False)`；`_probe_backend_service_effective` 之後新增 migration 函式）、`paulsha_hippo/cli.py:58-59`（doctor parser 加旗標）、`paulsha_hippo/cli.py:776-779`（`_ops_doctor` 傳遞旗標）
- Test: `tests/test_ops.py`

**Interfaces:**
- Consumes: Task 8 `resolve_backend_argv`／`BackendUnavailableError`、Task 9 `_service_effective_path_env`、既有 `paths.config_path("atomizer.override.yaml")`（`paulsha_hippo/paths.py:126`）。
- Produces（恢復序列 §4.1 前置 gate 依賴）:
  - `run_doctor(*, fix_backend: bool = False) -> int`——`fix_backend=True` 先跑 migration 再照常 doctor（probe 驗證遷移結果）
  - `_fix_backend_override() -> tuple[int, str]`——冪等：override 不存在／argv[0] 已絕對／service-effective 已可解析 → `(0, 訊息)` 不動檔；裸命令 service-effective 解析不到但互動環境可解析 → 備份 `atomizer.override.yaml.bak` 後單點改寫 command 首項 → `(0, 訊息)`；互動環境也解析不到／override 結構無法自動改寫 → `(1, 訊息)`
  - CLI：`hippo doctor --fix-backend`

- [ ] **Step 1: 寫失敗測試**

`tests/test_ops.py` 新增 class：

```python
class FixBackendMigrationTests(unittest.TestCase):
    _OVERRIDE = (
        'schema_version: "1"\n'
        "agent_exec:\n"
        "  command:\n"
        "    - claude\n"
        "    - -p\n"
    )

    def _env(self, tmp: str) -> dict[str, str]:
        return {
            "HIPPO_CONFIG_ROOT": f"{tmp}/hippo-cfg",
            "PSC_CONFIG_ROOT": f"{tmp}/legacy/.config/paulshaclaw",
            "HIPPO_MEMORY_ROOT": f"{tmp}/memory",
            "PSC_MEMORY_ROOT": f"{tmp}/memory",
        }

    def _write_override(self, tmp: str) -> Path:
        override = Path(tmp) / "legacy" / ".config" / "paulshaclaw" / "atomizer.override.yaml"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(self._OVERRIDE, encoding="utf-8")
        return override

    def _real_exe(self, tmp: str) -> Path:
        exe = Path(tmp) / "bin" / "claude"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        return exe

    def test_fix_backend_rewrites_bare_command_and_backs_up(self):
        with TemporaryDirectory() as tmp:
            override = self._write_override(tmp)
            exe = self._real_exe(tmp)

            def fake_which(cmd, path=None):
                # 只攔 claude：service-effective PATH（path 給定）→ 解析不到；互動環境 → 找得到。
                # systemctl 等其他查詢一律 None，讓 doctor 走無 systemd fallback（確定性）。
                if cmd != "claude":
                    return None
                return None if path is not None else str(exe)

            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops, "_service_effective_path_env",
                                   return_value="/usr/bin:/bin"), \
                 mock.patch.object(ops.shutil, "which", side_effect=fake_which):
                rc = ops.run_doctor(fix_backend=True)

            self.assertEqual(rc, 0)
            body = override.read_text(encoding="utf-8")
            self.assertIn(f"    - {exe}\n", body)
            self.assertNotIn("\n    - claude\n", body)
            backup = override.with_name(override.name + ".bak")
            self.assertIn("    - claude\n", backup.read_text(encoding="utf-8"))

    def test_fix_backend_is_idempotent_on_second_run(self):
        with TemporaryDirectory() as tmp:
            override = self._write_override(tmp)
            exe = self._real_exe(tmp)

            def fake_which(cmd, path=None):
                if cmd != "claude":
                    return None
                return None if path is not None else str(exe)

            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops, "_service_effective_path_env",
                                   return_value="/usr/bin:/bin"), \
                 mock.patch.object(ops.shutil, "which", side_effect=fake_which):
                self.assertEqual(ops.run_doctor(fix_backend=True), 0)
                first_body = override.read_text(encoding="utf-8")
                self.assertEqual(ops.run_doctor(fix_backend=True), 0)
                second_body = override.read_text(encoding="utf-8")

            self.assertEqual(first_body, second_body)
            backup = override.with_name(override.name + ".bak")
            # 第二輪 no-op：備份仍是第一輪存下的原始裸命令版
            self.assertIn("    - claude\n", backup.read_text(encoding="utf-8"))

    def test_fix_backend_without_override_is_noop(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops, "_probe_backend_service_effective",
                                   return_value=("- distiller backend：✓ mocked", False)):
                self.assertEqual(ops.run_doctor(fix_backend=True), 0)

    def test_fix_backend_unresolvable_everywhere_fails(self):
        with TemporaryDirectory() as tmp:
            self._write_override(tmp)
            with mock.patch.dict("os.environ", self._env(tmp)), \
                 mock.patch.object(ops, "_service_effective_path_env",
                                   return_value="/usr/bin:/bin"), \
                 mock.patch.object(ops.shutil, "which", return_value=None):
                self.assertEqual(ops.run_doctor(fix_backend=True), 1)
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_ops.py -k "FixBackend" -v`
Expected: FAIL——`TypeError: run_doctor() got an unexpected keyword argument 'fix_backend'`。

- [ ] **Step 3: 最小實作**

**(a)** `paulsha_hippo/ops.py` 檔頭 import 區加 `import re`。

**(b)** `run_doctor` 簽名與開頭改為：

```python
def run_doctor(*, fix_backend: bool = False) -> int:
    if fix_backend:
        code, message = _fix_backend_override()
        print(message, file=sys.stderr if code else sys.stdout)
        if code:
            return code
    report = paths.resolution_report()
```

（函式其餘部分維持 Task 9 完成後的樣子。）

**(c)** `_probe_backend_service_effective` 之後新增：

```python
_COMMAND_LIST_HEAD_RE = re.compile(r"^(\s*-\s+)(\S+)\s*$")


def _rewrite_override_command_head(text: str, bare: str, resolved: str) -> tuple[str, bool]:
    """單點改寫：agent_exec.command 清單第一項 == bare 的 token 換成 resolved。

    只處理 init 產生的 override 結構（`command:` 下第一個 `- <token>`）；
    結構對不上回 (原文, False)，交上層報「需人工」。"""
    lines = text.splitlines(keepends=True)
    in_command = False
    for index, line in enumerate(lines):
        if line.strip() == "command:":
            in_command = True
            continue
        if in_command:
            match = _COMMAND_LIST_HEAD_RE.match(line.rstrip("\n"))
            if match and match.group(2) == bare:
                newline = "\n" if line.endswith("\n") else ""
                lines[index] = f"{match.group(1)}{resolved}{newline}"
                return "".join(lines), True
            return "".join(lines), False
    return "".join(lines), False


def _fix_backend_override() -> tuple[int, str]:
    """冪等 migration（#15 既有部署救援）：override 內 service-effective 解析不到的
    裸 backend 命令 → 備份原檔 → 改寫為絕對路徑。回傳 (exit_code, 訊息)。"""
    from paulsha_hippo.atomizer import config as atomizer_config

    override = paths.config_path("atomizer.override.yaml")
    if not override.is_file():
        return 0, f"fix-backend: override 不存在（{override}），無可遷移"
    try:
        cfg, _ = atomizer_config.load_config()
    except Exception as exc:
        return 1, f"fix-backend: config 無法載入：{exc}"
    if cfg.agent_exec_backend == "openai-compatible":
        return 0, "fix-backend: openai-compatible backend 無 argv，無可遷移"
    command = atomizer_config.resolve_command_argv(cfg.agent_exec_command)
    argv0 = command[0]
    if Path(argv0).is_absolute():
        return 0, f"fix-backend: argv[0] 已是絕對路徑（{argv0}），無可遷移"
    if shutil.which(argv0, path=_service_effective_path_env()) is not None:
        return 0, f"fix-backend: {argv0} 在 service-effective PATH 已可解析，無可遷移"
    try:
        resolved = resolve_backend_argv([argv0])[0]
    except BackendUnavailableError as exc:
        return 1, f"fix-backend: {exc}（互動環境也解析不到，請先安裝 backend）"
    text = override.read_text(encoding="utf-8")
    new_text, replaced = _rewrite_override_command_head(text, argv0, resolved)
    if not replaced:
        return 1, (f"fix-backend: override 未含 agent_exec.command 首項 {argv0!r}，"
                   "無法自動改寫（請手動修改或重跑 hippo init）")
    backup = override.with_name(override.name + ".bak")
    shutil.copy2(override, backup)
    override.write_text(new_text, encoding="utf-8")
    return 0, f"fix-backend: {argv0} → {resolved}（備份：{backup}）"
```

**(d)** `paulsha_hippo/cli.py` doctor parser（lines 58-59）改為：

```python
    doctor_p = memory_subparsers.add_parser("doctor", help="健檢：路徑契約/hooks/服務/backend")
    doctor_p.add_argument(
        "--fix-backend", action="store_true",
        help="冪等遷移：override 中 service-effective 解析不到的裸 backend 命令改寫為絕對路徑（先備份）")
    doctor_p.set_defaults(func=_ops_doctor)
```

`_ops_doctor`（lines 776-779）改為：

```python
def _ops_doctor(args) -> int:
    from paulsha_hippo import ops

    return ops.run_doctor(fix_backend=getattr(args, "fix_backend", False))
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_ops.py tests/test_cli.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ops.py paulsha_hippo/cli.py tests/test_ops.py
git commit -m "feat(ops): hippo doctor --fix-backend——既有設定冪等遷移為絕對路徑（#15）"
```

---

### Task 11: E2E——backend 故障 → park（含證據）→ 修復 → requeue → 成功 promote

**Files:**
- Create: `tests/test_park_requeue_e2e.py`
- Test: `tests/test_park_requeue_e2e.py`

**Interfaces:**
- Consumes（前面 Task 的全部產出）: `processing.state_of`／parked 事件欄位（Task 1）、`agent_exec.AgentUnavailableError`（Task 2）、pipeline park＋證據＋fragments 保留（Task 4）、CLI `hippo requeue <session-key>`（Task 5）。
- Produces: spec §3.1「E2E 必測」的驗收證據測試（backend 故障→park→修復→requeue→promote 完整循環）。

- [ ] **Step 1: 寫 E2E 測試（新檔完整內容）**

```python
"""#15 E2E：backend 故障 → park（含證據）→ 修復 backend → requeue → 成功 promote。

spec §3.1「E2E 必測」的完整循環驗收。
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli
from paulsha_hippo.atomizer import agent_exec, llm_promoter, pipeline
from paulsha_hippo.atomizer import config as atomizer_config
from paulsha_hippo.ledger import processing

_RAW = """---
memory_layer: inbox
project: paulshaclaw
source_agent: claude
source_session: s1
source_artifact: research
captured_at: "2026-07-10T00:00:00Z"
provenance:
  repo: paulshaclaw
  commit: c
  path: docs/x.md
---
# Topic A
alpha body
# Topic B
beta body
"""

_VALID_ONE_SLICE = (
    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw","tags":[],'
    '"body":"body a","source_fragment_indices":[0,1],"relations":[]}]'
)


def _seed_raw(root: Path) -> Path:
    raw = root / "inbox" / "research" / "claude" / "2026-07-10" / "s1.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_RAW, encoding="utf-8")
    return raw


class UnavailableThenFixedClient(agent_exec.AgentClient):
    """模擬 backend 先壞後修：前 fail_times 次 raise AgentUnavailableError，之後回有效輸出。"""

    def __init__(self, fail_times: int, output: str) -> None:
        self._fail_times = fail_times
        self._output = output
        self.calls = 0

    def run(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise agent_exec.AgentUnavailableError("agent command not found: claude")
        return self._output


class ParkRequeuePromoteE2ETests(unittest.TestCase):
    def test_backend_failure_park_fix_requeue_promote_cycle(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_raw(root)
            cfg, h = atomizer_config.load_config(override_path=None)
            client = UnavailableThenFixedClient(1, _VALID_ONE_SLICE)
            cached = agent_exec.CachingAgentClient(
                client, root / "runtime" / "cache" / "atomize"
            )
            promoter = llm_promoter.LLMPromoter(
                cached, skill_text="E2E-SKILL",
                known_projects=["paulshaclaw"], model="fake-llm",
            )

            # 1) backend 故障 → park（含證據；backend_unavailable 不重試）
            pipeline.run(root, config=cfg, config_hash=h,
                         now="2026-07-10T01:00:00Z", promoter=promoter)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            parked_event = processing.read_events(root)[-1]
            self.assertEqual(parked_event["failure_category"], "backend_unavailable")
            evidence = root / "runtime" / "queue" / "_failed" / "claude__s1.json"
            self.assertTrue(evidence.exists())
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(payload["session_key"], "claude:s1")
            self.assertEqual(payload["failure_category"], "backend_unavailable")
            # split fragments 保留、cache/sidecar 已清
            self.assertEqual(len(list((root / "inbox" / "_slices").rglob("*.md"))), 2)
            cache_dir = root / "runtime" / "cache" / "atomize"
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            self.assertEqual(list(cache_dir.glob("*.retries")), [])

            # 2) parked 不佔下一輪 atomize 預算（backend 未修也不再呼叫）
            calls_before = client.calls
            pipeline.run(root, config=cfg, config_hash=h,
                         now="2026-07-10T02:00:00Z", promoter=promoter)
            self.assertEqual(client.calls, calls_before)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")

            # 3) backend 已修復（client 下次成功）→ hippo requeue
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["requeue", "claude:s1", "--memory-root", str(root),
                               "--now", "2026-07-10T03:00:00Z",
                               "--reason", "backend fixed"])
            self.assertEqual(rc, 0)
            summary = json.loads(buf.getvalue())
            self.assertEqual(summary["requeued"][0]["session_key"], "claude:s1")
            self.assertEqual(summary["requeued"][0]["fragments"], 2)
            self.assertEqual(processing.state_of(root, "claude:s1"), "split")
            requeue_event = processing.read_events(root)[-1]
            self.assertEqual(requeue_event["requeued_from"], "parked")
            self.assertEqual(requeue_event["requeue_reason"], "backend fixed")

            # 4) 重走 promote → promoted、knowledge 落地、快取全清
            result = pipeline.run(root, config=cfg, config_hash=h,
                                  now="2026-07-10T04:00:00Z", promoter=promoter)
            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(result["summary"]["slices"], 1)
            self.assertEqual(len(list((root / "knowledge").rglob("*.md"))), 1)
            self.assertEqual(list((root / "inbox" / "_slices").rglob("*.md")), [])
            self.assertEqual(list(cache_dir.glob("*.json")), [])
            # 證據檔保留為歷史紀錄
            self.assertTrue(evidence.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 PASS（整合驗收——若 FAIL 即前面 Task 有缺陷，回頭修）**

Run: `python3 -m pytest tests/test_park_requeue_e2e.py -v`
Expected: PASS。若 FAIL：依斷言定位是哪個 Task 的產出不符合契約，修復該 Task 的實作（不得改弱 E2E 斷言）。

- [ ] **Step 3: 全套回歸**

Run: `python3 -m pytest tests/ -q`
Expected: 全 PASS。

- [ ] **Step 4: Commit**

```bash
git add tests/test_park_requeue_e2e.py
git commit -m "test(atomizer): park→修復→requeue→promote 完整循環 E2E（#15 驗收）"
```

---

### Task 12: changelog.d 碎片 + CHANGELOG `[Unreleased]` + README docs 同步 + 收尾驗證

**Files:**
- Create: `changelog.d/fix-15-atomize-failure-chain.md`
- Modify: `CHANGELOG.md:8-11`（`[Unreleased]` 段——R-09 以此檔為準，policy_check 的 `_unreleased_has_bullet_entry` 只檢查 `## [Unreleased]` 下有 bullet，與 changelog.d 完全無關）
- Modify: `README.md`（Quickstart `hippo doctor` 註解行、Usage「日常命令：」行——跨批次共用錨行，一律以行內容定位、不依賴行號；rebase 後不得整行覆蓋，見 Step 3 合併規則）
- Test: 全套 pytest + `python3 -m policy_check --repo .`

**Interfaces:**
- Consumes: Tasks 1-11 全部落地。
- Produces: R-18 docs 同步證據＋R-09 CHANGELOG `[Unreleased]` entry＋changelog.d 碎片（repo 慣例兩者並存——碎片供 release 彙整，`[Unreleased]` 供 R-09 gate）；PR 可開（`Closes #15`）。

- [ ] **Step 1: 新增 changelog 碎片**

`changelog.d/fix-15-atomize-failure-chain.md`（新檔完整內容；格式沿 `changelog.d/fix-dream-service-interpreter.md` 慣例）：

```markdown
### Fixed
- 原子化失敗鏈（#15）：promote 失敗三分類（`backend_unavailable`／`transient`／`invalid_output`），session 進 `parked` 顯性終態（記 category／attempts／cache_key／bounded error）；重試超限即淘汰毒快取與 retry sidecar（保留 split fragments），失敗證據落 `runtime/queue/_failed/`；反轉「毒快取保留」既有測試為「超限即淘汰」。
- dream singleton（#19）：`dream run` 入口以 `<memory_root>/runtime/locks/dream.lock` 全域 nonblocking flock 整輪持有，取不到鎖記 log 後 skip（exit 0），杜絕並發競寫。
- dream orchestrator 錯誤可見性（#19）：pass 失敗保存 bounded 錯誤訊息（≤500 字元、去敏）與 errno，不再只存 exception 類別名。
- backend 絕對路徑（#10 最小修復）：`hippo init` 產生 atomizer override 時 argv[0] 經 `resolve_backend_argv` 絕對路徑化（systemd 環境無 NVM PATH 也找得到）；`hippo doctor` 以 service-effective PATH probe 蒸餾 backend。

### Added
- `hippo requeue <session-key>|--all-parked`：parked session 回 `split` 重走 promote（ledger 記 `requeued_from`／`requeue_reason`）。
- `hippo doctor --fix-backend`：冪等遷移既有 atomizer override 的裸 backend 命令為絕對路徑（先備份 `.bak`）。
- `paulsha_hippo/dream/lock.py`：global dream lock（`dream_lock_path`／`acquire_dream_lock`），PR-C doctor 引用同一路徑。
- `paulsha_hippo/ops.py`：`resolve_backend_argv` + `BackendUnavailableError`（PR-D preset registry 重用）。
```

- [ ] **Step 2: 更新 `CHANGELOG.md` `[Unreleased]` 段（R-09 gate 以此檔為準；比照 PR-C Task 7 Step 4）**

把 Step 1 碎片同內容的 bullet 併入 `CHANGELOG.md` 的 `## [Unreleased]`——`### Fixed` 四條 bullet 併入**既有** `### Fixed` 標題下（現行 line 10-11，`install service` interpreter bullet 之後；標題已存在，不重複標題——R-04 格式）：

```markdown
- 原子化失敗鏈（#15）：promote 失敗三分類（`backend_unavailable`／`transient`／`invalid_output`），session 進 `parked` 顯性終態（記 category／attempts／cache_key／bounded error）；重試超限即淘汰毒快取與 retry sidecar（保留 split fragments），失敗證據落 `runtime/queue/_failed/`；反轉「毒快取保留」既有測試為「超限即淘汰」。
- dream singleton（#19）：`dream run` 入口以 `<memory_root>/runtime/locks/dream.lock` 全域 nonblocking flock 整輪持有，取不到鎖記 log 後 skip（exit 0），杜絕並發競寫。
- dream orchestrator 錯誤可見性（#19）：pass 失敗保存 bounded 錯誤訊息（≤500 字元、去敏）與 errno，不再只存 exception 類別名。
- backend 絕對路徑（#10 最小修復）：`hippo init` 產生 atomizer override 時 argv[0] 經 `resolve_backend_argv` 絕對路徑化（systemd 環境無 NVM PATH 也找得到）；`hippo doctor` 以 service-effective PATH probe 蒸餾 backend。
```

`### Added` 四條以新標題插入 `### Fixed` 段之後、`## [0.1.0]` 之前：

```markdown
### Added
- `hippo requeue <session-key>|--all-parked`：parked session 回 `split` 重走 promote（ledger 記 `requeued_from`／`requeue_reason`）。
- `hippo doctor --fix-backend`：冪等遷移既有 atomizer override 的裸 backend 命令為絕對路徑（先備份 `.bak`）。
- `paulsha_hippo/dream/lock.py`：global dream lock（`dream_lock_path`／`acquire_dream_lock`），PR-C doctor 引用同一路徑。
- `paulsha_hippo/ops.py`：`resolve_backend_argv` + `BackendUnavailableError`（PR-D preset registry 重用）。
```

（rebase 後若 `[Unreleased]` 已有其他批次的相同 `### Fixed`／`### Added` 標題，同樣把 bullet 併入既有標題下，不重複標題——R-04 格式。R-09 的 `_unreleased_has_bullet_entry` 只認 `CHANGELOG.md`，changelog.d 碎片本身**不**滿足 R-09。）

- [ ] **Step 3: README 同步（R-18）**

`README.md` Quickstart 段 `hippo doctor` 註解行（以「`hippo doctor`」＋「# 健檢：」註解文字定位，不以行號；行號 15 僅為原始 main 快照）整行：

```
    hippo doctor                        # 健檢：路徑契約/hooks/服務/backend
```

改為：

```
    hippo doctor                        # 健檢：路徑契約/hooks/服務/backend（--fix-backend 冪等遷移裸命令為絕對路徑）
```

`README.md` Usage 段「日常命令：」行（以行首「日常命令：」文字定位，不以行號；行號 27 僅為原始 main 快照）整行：

```
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo replay`／`hippo bundle`。
```

改為：

```
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo replay`／`hippo bundle`／`hippo requeue <session-key>|--all-parked`（parked session 修復後重排）。
蒸餾失敗顯性化：backend 不可用／重試超限的 session 進 `parked`（證據在 `runtime/queue/_failed/`），修復後 `hippo requeue` 恢復；`dream run` 以 global lock 保證單一 writer，並發第二實例記 log 後跳過。
```

**合併規則（README 跨批次共用錨行；PR-B Task 6 Step 7／PR-C Task 7 Steps 1-2／PR-F Task 7 Step 3 帶同一條規則）**：若該行已被 sibling 批次改寫（rebase 後與引用的 old 內容不符），不得整行覆蓋——以當前行內容為基底，僅追加本批新增片段（日常命令清單插入本批命令、doctor 註解串接本批說明），保留 sibling 已 merge 的全部新增。本批新增片段：doctor 註解行尾端串接「（--fix-backend 冪等遷移裸命令為絕對路徑）」；「日常命令：」清單尾端（`hippo bundle` 之後）插入「／`hippo requeue <session-key>|--all-parked`（parked session 修復後重排）」；該行之後另新增補充行「蒸餾失敗顯性化：…」（獨立新行，依落地順序緊隨「日常命令」行及既有補充行之後）。sibling 已 merge 的片段——doctor 註解的「/runtime 進程與 lock」（PR-C）、命令清單的 `hippo recall`（PR-F）／`hippo index verify`（PR-B）／`hippo usage`（PR-F）、其後續補充行（PR-C 維運／PR-F 跨 CLI 消費能力）——一律原樣保留，不得覆蓋或刪除。

- [ ] **Step 4: 全套驗證**

Run: `python3 -m pytest tests/ -q`
Expected: 全 PASS，0 failed。

Run: `python3 -m policy_check --repo .`
Expected: 無任何 failure（R-09 由 Step 2 的 `[Unreleased]` bullet 滿足；碎片供 release 彙整，不滿足 R-09；R-20/R-14 未動不受影響）。

- [ ] **Step 5: Commit**

```bash
git add changelog.d/fix-15-atomize-failure-chain.md CHANGELOG.md README.md
git commit -m "docs: #15 失敗鏈——changelog 碎片、CHANGELOG [Unreleased] 與 README 同步（requeue/doctor --fix-backend/dream singleton）"
```

- [ ] **Step 6: 驗收核對（開 PR 前自查，對照 spec §3.1 驗收）**

- 新增／修改單元測試全過；全套 pytest 過（Step 4 證據）。
- 模擬 non-JSON backend 的 session 在 retry 超限後：狀態 `parked`、快取已刪、`_failed/` 有證據、下輪不再重試（`test_exhausted_budget_evicts_cache_and_parks_session`）。
- 兩個並發 `dream run` 只有一個實際執行（`test_dream_run_skips_when_lock_held` + `test_second_acquire_fails_while_held_then_succeeds_after_release`）。
- E2E：park→修復→requeue→promote（`test_backend_failure_park_fix_requeue_promote_cycle`）。
- PR：title conventional-commit zh-tw、body `Closes #15`、checklist 全勾（PR 建立由 workflow 主編排執行，spec §6.7）。
