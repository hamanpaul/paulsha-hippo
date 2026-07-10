# PR-C Runtime 衛生（#19 lock sharding）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 importer 的無界 per-session lock namespace 收斂為固定 64 個 hash-sharded locks，提供 legacy lock 檔一次性清理命令（僅維護窗口），並讓 `hippo doctor` 產出 runtime 進程健康報告（PID/start/cmdline、非 canonical 標記、dream lock 持鎖狀態，只報不殺）。

**Architecture:** 三塊獨立面：(1) `paulsha_hippo/importer/pipeline.py` 的 lock 命名改為契約 4 的 `lock_shard_{h:02x}.lock`（`h = crc32(safe_key(key)) % 64`），碰撞只降低並行度、互斥語義不變；(2) `paulsha_hippo/ops.py` 新增純 stdlib 的 `/proc` 掃描（可注入 `proc_root` 供 fixture 測試）＋ doctor 報告段落＋契約 3 dream lock 狀態探測；(3) 新 CLI `hippo locks cleanup-legacy`，雙層安全閘（無其他 hippo 進程 + 逐檔 flock 探測）後才刪 legacy lock 檔。

**Tech Stack:** Python 3.10+ stdlib（`zlib.crc32`、`fcntl.flock`、`/proc` 檔案系統、`argparse`）、pytest/unittest（沿 `tests/test_idempotency.py`、`tests/test_ops.py` 既有風格）。

**前置依賴：** PR-A（`feature/15-atomize-failure-chain`）已 merge——global dream lock `<memory_root>/runtime/locks/dream.lock` 已存在（契約 3），且 PR-A 可能已改過 `ops.run_doctor()`（backend probe 段落）。本 plan 對 `ops.py` 的行號以「當前 main（PR-A 前）」為準，實作時若 PR-A 已位移行號，以「錨定敘述」（如「`run_doctor` 最終 `return` 前」）為準，**保留 PR-A 的所有段落**。

**分支／PR：** branch `feature/19-lock-sharding`（自最新 main 開出）；PR title conventional-commit、body `Closes #19`、checklist 全勾、zh-tw。

## Global Constraints

（自 master spec §7 逐字抄錄，每個 Task 的要求隱含包含本節）

- 分支一律 `feature/<issue>-<slug>`；禁 commit main。
- 每 code PR：changelog.d 碎片（repo 現行慣例）、PR checklist 全勾、`Closes #N`（R-17）、zh-tw（語言規範）、`policy_check` 零 failure。
- `tier: shareable`（R-21）：所有新增文件（含本 spec、capability matrix、快照留言）不得含個人絕對路徑、機敏標記。
- R-18/R-22：behavior 變更同步 README／docs 引用（`hippo recall`、`--backend` 選單、doctor 新輸出）。
- 測試新增全部進 CI 覆蓋（R-19；`tests.yml` 已自動跑 pytest）。
- stdlib-only 零新依賴（pyproject 依賴不得增加；本批全部用 `zlib`/`fcntl`/`os`/`tempfile`/`datetime` 等 stdlib）。
- commit message 一律 zh-tw conventional-commit。

## 跨批次共享介面契約（本 plan 涉及者，必須逐字一致）

- **契約 3（consume）**：global dream lock 固定路徑 `<memory_root>/runtime/locks/dream.lock`。PR-A 於 dream run 入口 `fcntl.flock(LOCK_EX|LOCK_NB)` 整輪持有；**PR-C doctor 引用同一路徑報告持鎖狀態**（只讀探測，不長持）。
- **契約 4（produce，本 PR 實作方）**：importer shard lock 檔名 `lock_shard_{h:02x}.lock`，`h = crc32(safe_key(key)) % 64`；取代 per-key 無界 lock 檔。
- **契約 5（遵循）**：CLI 子命令一律走 `cli.py` 的 `memory_subparsers.add_parser` 既有模式。
- 恢復序列（spec §4）不在本 plan 內：legacy locks 實際清理動作由 workflow 主編排在 PR-C merge 後的維護窗口執行；本 plan 只交付清理**命令**與其安全閘。

---

### Task 1: shard lock 路徑函式（契約 4）

**Files:**
- Modify: `paulsha_hippo/importer/pipeline.py:5-14`（import 區）與 `:121-126` 之後（`safe_key` 定義後插入新函式）
- Test: `tests/test_importer_lock_sharding.py`（新檔）

**Interfaces:**
- Consumes: `paulsha_hippo.importer.pipeline.safe_key(key: str) -> str`（既有，`pipeline.py:125`）
- Produces:
  - `paulsha_hippo.importer.pipeline.shard_lock_path(memory_root: Path, key: str) -> Path` —— 回傳 `<memory_root>/runtime/locks/lock_shard_{h:02x}.lock`，`h = zlib.crc32(safe_key(key).encode("utf-8")) % 64`（Task 2、Task 3 及既有測試改寫都靠它）
  - `paulsha_hippo.importer.pipeline.is_shard_lock_name(name: str) -> bool` —— 檔名是否為 shard lock（`lock_shard_00.lock` ～ `lock_shard_3f.lock`；Task 6 清理 keep-set 靠它）
  - `paulsha_hippo.importer.pipeline._LOCK_SHARD_COUNT: int = 64`

- [ ] **Step 1: 寫失敗測試（新檔 `tests/test_importer_lock_sharding.py`）**

```python
"""#19 PR-C：importer lock sharding（契約 4）與並發互斥。"""
import unittest
from pathlib import Path

from paulsha_hippo.importer import pipeline
from paulsha_hippo.importer.pipeline import is_shard_lock_name, shard_lock_path


class ShardLockPathTest(unittest.TestCase):
    """契約 4：lock_shard_{h:02x}.lock，h = crc32(safe_key(key)) % 64。"""

    def test_shard_path_is_deterministic_and_in_locks_dir(self):
        root = Path("/mem")
        first = shard_lock_path(root, "copilot-cli:sid-001")
        second = shard_lock_path(root, "copilot-cli:sid-001")
        self.assertEqual(first, second)
        self.assertEqual(first.parent, root / "runtime" / "locks")

    def test_known_keys_map_to_expected_shards(self):
        # 常數以 zlib.crc32(safe_key(key).encode("utf-8")) % 64 事先計算並鎖定，
        # 防止實作偷換 hash 或編碼（契約 4 逐字遵循）。
        root = Path("/mem")
        self.assertEqual(shard_lock_path(root, "copilot-cli:sid-001").name,
                         "lock_shard_34.lock")
        self.assertEqual(shard_lock_path(root, "copilot-cli:sid-stress-000").name,
                         "lock_shard_08.lock")
        # 已知碰撞對：不同 key、同 shard（碰撞只降低並行度，不影響正確性）
        self.assertEqual(shard_lock_path(root, "copilot-cli:sid-stress-126").name,
                         "lock_shard_08.lock")
        # 已知相異：不同 key、不同 shard
        self.assertEqual(shard_lock_path(root, "copilot-cli:sid-stress-001").name,
                         "lock_shard_1e.lock")

    def test_shard_universe_is_bounded_to_64_names(self):
        root = Path("/mem")
        names = {shard_lock_path(root, f"claude:s{i}").name for i in range(1000)}
        universe = {f"lock_shard_{h:02x}.lock" for h in range(pipeline._LOCK_SHARD_COUNT)}
        self.assertEqual(pipeline._LOCK_SHARD_COUNT, 64)
        self.assertLessEqual(names, universe)

    def test_is_shard_lock_name_accepts_only_shard_names(self):
        for h in range(64):
            self.assertTrue(is_shard_lock_name(f"lock_shard_{h:02x}.lock"))
        self.assertFalse(is_shard_lock_name("lock_shard_40.lock"))   # 超出 00..3f
        self.assertFalse(is_shard_lock_name("lock_shard_3g.lock"))   # 非 hex
        self.assertFalse(is_shard_lock_name("copilot-cli__sid-001.lock"))  # legacy 命名
        self.assertFalse(is_shard_lock_name("import-ledger.lock"))
        self.assertFalse(is_shard_lock_name("dream.lock"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_importer_lock_sharding.py -v`
Expected: 收集階段即 ERROR，訊息含 `ImportError: cannot import name 'shard_lock_path' from 'paulsha_hippo.importer.pipeline'`

- [ ] **Step 3: 最小實作**

`paulsha_hippo/importer/pipeline.py` import 區（現行第 5-14 行），在 `import threading`（現行第 9 行）後加一行：

```python
import zlib
```

（加完後 import 區依序為 `fcntl`、`json`、`re`、`shutil`、`threading`、`zlib`。）

在 `safe_key` 函式（現行第 125-126 行）之後、`_date_parts`（現行第 129 行）之前插入：

```python
_LOCK_SHARD_COUNT = 64
_SHARD_LOCK_NAME_RE = re.compile(r"^lock_shard_[0-3][0-9a-f]\.lock$")


def shard_lock_path(memory_root: Path, key: str) -> Path:
    """契約 4：importer per-key lock → 固定 64 個 hash shard。

    檔名 ``lock_shard_{h:02x}.lock``，``h = crc32(safe_key(key)) % 64``。
    取代 per-key 無界 lock 檔（#19）：碰撞只降低並行度，不影響互斥正確性。
    """
    h = zlib.crc32(safe_key(key).encode("utf-8")) % _LOCK_SHARD_COUNT
    return Path(memory_root) / "runtime" / "locks" / f"lock_shard_{h:02x}.lock"


def is_shard_lock_name(name: str) -> bool:
    """檔名是否為現行 shard lock（lock_shard_00.lock ～ lock_shard_3f.lock）。"""
    return bool(_SHARD_LOCK_NAME_RE.fullmatch(name))
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_importer_lock_sharding.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/importer/pipeline.py tests/test_importer_lock_sharding.py
git commit -m "feat(importer): 新增 64-shard lock 路徑函式（跨批次契約 4）"
```

---

### Task 2: ingest 切換 shard lock，停產 legacy per-session lock 檔

**Files:**
- Modify: `paulsha_hippo/importer/pipeline.py:366-370`（`ingest_queue_item` 的 lock 取得段）
- Modify: `tests/test_idempotency.py:318`（既有 lock 等待測試的 lock 路徑）
- Test: `tests/test_importer_lock_sharding.py`（追加 `LegacyLockRetirementTest`）

**Interfaces:**
- Consumes: `shard_lock_path(memory_root: Path, key: str) -> Path`（Task 1）
- Produces: `ingest_queue_item()` 行為變更——同一 `memory_root` 下 `runtime/locks/` 只會出現 `lock_shard_XX.lock` 與 `import-ledger.lock`（加上 PR-A 的 `dream.lock`），**不再產生** `{safe_key(key)}.lock`

- [ ] **Step 1: 改寫既有 lock 等待測試指向 shard 路徑**

`tests/test_idempotency.py` 現行第 318 行：

```python
        lock_path = self.root / "runtime" / "locks" / "copilot-cli__sid-001.lock"
```

改為：

```python
        lock_path = pipeline.shard_lock_path(self.root, "copilot-cli:sid-001")
```

（該測試 `test_second_same_session_ingest_waits_for_lock_and_finishes_normally` 其餘不動：`instrumented_flock` 以 `Path(lock_handle.name) == lock_path` 比對，改指 shard 路徑後即驗證「同 key 第二個 ingest 在 shard lock 上等待」。）

- [ ] **Step 2: 追加失敗測試（`tests/test_importer_lock_sharding.py` 檔尾、`if __name__` 前）**

```python
import json
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]


def _stress_payload(*, session_id: str, cwd: str, turns: int = 1) -> dict:
    return {
        "tool": "copilot-cli",
        "session_id": session_id,
        "capture_scope": "turn",
        "ended_at": "2026-07-10T10:00:00+00:00",
        "cwd": cwd,
        "repo": "hamanpaul/paulsha-hippo",
        "commit": "0000000",
        "turn_count": turns,
        "user_prompts": ["stress"],
        "assistant_summary": "summary",
        "touched_files": ["a.py"],
        "referenced_artifacts": [],
    }


class _ScratchCase(unittest.TestCase):
    """沿 tests/test_idempotency.py 慣例：scratch 開在 repo 內 .test-work。"""

    def setUp(self):
        self.scratch = REPO_ROOT / ".test-work"
        self.scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.tmp.name) / "memory"
        self.queue = self.root / "runtime" / "queue"
        self.queue.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            self.scratch.rmdir()
        except OSError:
            pass

    def write_queue_item(self, name: str, payload: dict) -> Path:
        path = self.queue / f"{name}.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    def lock_names(self) -> set[str]:
        locks_dir = self.root / "runtime" / "locks"
        if not locks_dir.is_dir():
            return set()
        return {p.name for p in locks_dir.iterdir() if p.is_file()}


class LegacyLockRetirementTest(_ScratchCase):
    def test_ingest_creates_shard_lock_and_no_legacy_per_session_lock(self):
        payload = _stress_payload(session_id="sid-001", cwd=str(self.root))
        queue_item = self.write_queue_item("one", payload)

        decision = pipeline.ingest_queue_item(queue_item, memory_root=self.root)

        self.assertEqual(decision["status"], "written")
        names = self.lock_names()
        # 契約 4：sid-001 落在 shard 0x34（Task 1 鎖定的常數）
        self.assertIn("lock_shard_34.lock", names)
        # 停產 legacy 命名：不得再出現 {safe_key(key)}.lock
        self.assertNotIn("copilot-cli__sid-001.lock", names)
        for name in names:
            self.assertTrue(
                is_shard_lock_name(name) or name == "import-ledger.lock",
                f"unexpected lock file: {name}",
            )
```

- [ ] **Step 3: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_importer_lock_sharding.py::LegacyLockRetirementTest tests/test_idempotency.py::IdempotencyPipelineTest::test_second_same_session_ingest_waits_for_lock_and_finishes_normally -v`
Expected: `LegacyLockRetirementTest` FAIL（`lock_shard_34.lock` 不在 `names`，legacy 檔存在）；`test_second_same_session_ingest_waits_for_lock_and_finishes_normally` FAIL（`second_attempted_lock.wait(timeout=2)` 為 False——實作仍鎖 legacy 路徑）

- [ ] **Step 4: 最小實作**

`paulsha_hippo/importer/pipeline.py` `ingest_queue_item` 內（現行第 366-370 行）：

```python
    key = decision["idempotency_key"]
    lock_dir = root / "runtime" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{safe_key(key)}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
```

改為：

```python
    key = decision["idempotency_key"]
    lock_path = shard_lock_path(root, key)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
```

（同檔案內直接呼叫，無需 import。）

- [ ] **Step 5: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_importer_lock_sharding.py tests/test_idempotency.py -v`
Expected: 全數 passed（含既有 idempotency 全套——sharding 不得破壞任何既有語義）

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/importer/pipeline.py tests/test_idempotency.py tests/test_importer_lock_sharding.py
git commit -m "feat(importer): ingest 改用 64-shard lock，停產 per-session lock 檔（#19）"
```

---

### Task 3: 並發互斥壓力測試

**Files:**
- Test: `tests/test_importer_lock_sharding.py`（追加 `ConcurrentShardStressTest`）

**Interfaces:**
- Consumes: `pipeline.ingest_queue_item(queue_item, *, memory_root, dry_run=False) -> dict`、`shard_lock_path`（Task 1/2）、Task 2 的 `_ScratchCase`/`_stress_payload`/`lock_names` helper（同檔案）
- Produces: 驗收證據——「並發 importer 壓力測試互斥正確；locks 目錄檔案數恆為常數」（spec §3.4 驗收第 1、2 項）

- [ ] **Step 1: 寫壓力測試（追加到 `tests/test_importer_lock_sharding.py`，`LegacyLockRetirementTest` 之後）**

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import mock


class ConcurrentShardStressTest(_ScratchCase):
    def ledger_statuses(self) -> list[str]:
        ledger = self.root / "runtime" / "ledger" / "import.jsonl"
        if not ledger.exists():
            return []
        return [json.loads(line)["status"]
                for line in ledger.read_text(encoding="utf-8").splitlines() if line]

    def test_concurrent_same_key_duplicates_yield_single_written_per_key(self):
        """互斥正確性：8 個 key 各 6 份相同 payload 併發 → 每 key 恰一 written。"""
        items: list[tuple[str, Path]] = []
        for k in range(8):
            payload = _stress_payload(session_id=f"sid-dup-{k:02d}", cwd=str(self.root))
            for d in range(6):
                items.append((f"sid-dup-{k:02d}",
                              self.write_queue_item(f"dup-{k:02d}-{d}", payload)))

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [(key, executor.submit(pipeline.ingest_queue_item, path,
                                             memory_root=self.root))
                       for key, path in items]
            results = [(key, future.result(timeout=60)) for key, future in futures]

        by_key: dict[str, list[str]] = {}
        for key, decision in results:
            by_key.setdefault(key, []).append(decision["status"])
        for key, statuses in by_key.items():
            self.assertEqual(sorted(statuses),
                             ["hash-duplicate"] * 5 + ["written"],
                             f"key {key} 併發去重錯誤: {statuses}")
        # 佇列全數消化、ledger 每行皆完整 JSON（flock 互斥未被 sharding 破壞）
        self.assertFalse(any(path.exists() for _, path in items))
        statuses = self.ledger_statuses()
        self.assertEqual(len(statuses), 48)
        self.assertEqual(statuses.count("written"), 8)
        self.assertEqual(statuses.count("hash-duplicate"), 40)

    def test_lock_dir_file_count_stays_bounded_across_waves(self):
        """locks 目錄檔案數恆為常數上界：96 個相異 key（> 64 shard，鴿籠必碰撞）
        分兩波併發 ingest，lock 檔集合始終 ⊆ 64 shard 名 ∪ {import-ledger.lock}。"""
        universe = {f"lock_shard_{h:02x}.lock" for h in range(64)} | {"import-ledger.lock"}

        def run_wave(start: int, count: int) -> None:
            paths = [
                self.write_queue_item(
                    f"wave-{i:03d}",
                    _stress_payload(session_id=f"sid-stress-{i:03d}",
                                    cwd=str(self.root), turns=1 + i % 3),
                )
                for i in range(start, start + count)
            ]
            with ThreadPoolExecutor(max_workers=16) as executor:
                decisions = list(executor.map(
                    lambda p: pipeline.ingest_queue_item(p, memory_root=self.root),
                    paths))
            self.assertEqual({d["status"] for d in decisions}, {"written"})

        run_wave(0, 48)
        after_wave1 = self.lock_names()
        self.assertLessEqual(after_wave1, universe)
        self.assertLessEqual(len(after_wave1), 65)

        run_wave(48, 48)
        after_wave2 = self.lock_names()
        self.assertLessEqual(after_wave2, universe)
        self.assertLessEqual(len(after_wave2), 65)
        # 全部 96 筆都成功寫入（吞吐不因 sharding 丟事件）
        self.assertEqual(self.ledger_statuses().count("written"), 96)

    def test_colliding_keys_serialize_on_same_shard_without_error(self):
        """已知碰撞對 sid-stress-000 / sid-stress-126（同 shard 0x08，Task 1 鎖定）：
        第二個 key 必須在同一 shard lock 上等待，釋放後兩者都成功 written。"""
        import fcntl as _fcntl

        first_queue = self.write_queue_item(
            "collide-a", _stress_payload(session_id="sid-stress-000", cwd=str(self.root)))
        second_queue = self.write_queue_item(
            "collide-b", _stress_payload(session_id="sid-stress-126", cwd=str(self.root)))
        lock_path = pipeline.shard_lock_path(self.root, "copilot-cli:sid-stress-000")
        self.assertEqual(lock_path,
                         pipeline.shard_lock_path(self.root, "copilot-cli:sid-stress-126"))

        first_holding = Event()
        second_attempted = Event()
        release_first = Event()
        real_archive_queue = pipeline._archive_queue
        real_flock = pipeline.fcntl.flock

        # 關鍵：外層 preview 改走無鎖變體（沿 test_idempotency 既有測試同一手法）。
        # 否則 first 卡在 _archive_queue 時仍持有 ledger lock，second 會先卡在
        # 外層 preview 的 ledger lock、永遠到不了 shard flock → 測試假死。
        def unlocked_preview(queue_item, *, memory_root):
            return pipeline._preview_queue_item_unlocked(queue_item, memory_root=memory_root)

        def blocking_archive(queue_path, archive_path):
            if Path(queue_path) == first_queue and not first_holding.is_set():
                first_holding.set()
                assert release_first.wait(timeout=10)
            return real_archive_queue(queue_path, archive_path)

        def instrumented_flock(lock_handle, operation):
            if (Path(lock_handle.name) == lock_path
                    and operation & _fcntl.LOCK_EX
                    and first_holding.is_set()):
                second_attempted.set()
            return real_flock(lock_handle, operation)

        with mock.patch("paulsha_hippo.importer.pipeline.preview_queue_item",
                        side_effect=unlocked_preview), \
             mock.patch("paulsha_hippo.importer.pipeline._archive_queue",
                        side_effect=blocking_archive), \
             mock.patch("paulsha_hippo.importer.pipeline.fcntl.flock",
                        side_effect=instrumented_flock):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    pipeline.ingest_queue_item, first_queue, memory_root=self.root)
                self.assertTrue(first_holding.wait(timeout=10))
                second_future = executor.submit(
                    pipeline.ingest_queue_item, second_queue, memory_root=self.root)
                self.assertTrue(second_attempted.wait(timeout=10))
                self.assertFalse(second_future.done())  # 碰撞 → 序列化等待
                release_first.set()
                first_decision = first_future.result(timeout=10)
                second_decision = second_future.result(timeout=10)

        self.assertEqual(first_decision["status"], "written")
        self.assertEqual(second_decision["status"], "written")  # 相異 key，各自寫入
        self.assertEqual(sorted(self.ledger_statuses()), ["written", "written"])
```

- [ ] **Step 2: 跑測試確認 PASS（本 task 為純測試強化——Task 2 的實作應直接使其綠燈；若有紅燈即為 sharding 實作 bug，回 Task 2 修）**

Run: `python3 -m pytest tests/test_importer_lock_sharding.py -v`
Expected: `8 passed`（4 舊 + 1 retirement + 3 stress；壓力測試單檔耗時可能達 10-30 秒，屬預期）

- [ ] **Step 3: Commit**

```bash
git add tests/test_importer_lock_sharding.py
git commit -m "test(importer): 併發互斥壓力測試——同 key 去重、lock 檔數上界、碰撞序列化（#19）"
```

---

### Task 4: `/proc` 進程掃描與 dream 進程健康報告素材（ops.py）

**Files:**
- Modify: `paulsha_hippo/ops.py:6-15`（import 區）與檔尾（`run_dream_supervise` 之後追加新 section）
- Test: `tests/test_runtime_health.py`（新檔）

**Interfaces:**
- Consumes: 無（純 stdlib `/proc` 讀取；`proc_root` 參數可注入 fixture 目錄）
- Produces:
  - `paulsha_hippo.ops.scan_hippo_processes(*, proc_root: str | Path = "/proc") -> list[dict[str, object]]` —— 每筆 `{"pid": int, "argv": list[str], "cmdline": str, "started_at": str, "cwd": str | None}`；涵蓋 cmdline 含 `paulsha_hippo` 或 argv[0] basename 為 `hippo` 的進程；排除自身 PID；只讀不 signal（Task 6 清理閘也用它）
  - `paulsha_hippo.ops.dream_process_report(*, proc_root: str | Path = "/proc", canonical_interpreter: str | None = None) -> list[dict[str, object]]` —— 過濾出 argv 含 token `dream` 的進程，每筆追加 `{"non_canonical": bool, "reasons": list[str]}`，reason token ∈ `{"interpreter-mismatch", "cwd-missing", "cwd-temp-worktree"}`（Task 5 doctor 用）

- [ ] **Step 1: 寫失敗測試（新檔 `tests/test_runtime_health.py`）**

```python
"""#19 PR-C：doctor runtime 健康報告——/proc 掃描、非 canonical 標記、dream lock 狀態。"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo import ops

_BTIME = 1751900000
_STARTTIME_TICKS = 5_000_000


def make_fake_proc(base: Path) -> Path:
    """建立可注入 proc_root 的假 /proc：頂層 stat 供 btime。"""
    proc = base / "proc"
    proc.mkdir()
    (proc / "stat").write_text(
        "cpu  0 0 0 0\n"
        f"btime {_BTIME}\n",
        encoding="ascii",
    )
    return proc


def add_fake_process(proc: Path, pid: int, argv: list[str], *,
                     cwd_target: Path | str | None,
                     starttime_ticks: int = _STARTTIME_TICKS) -> None:
    """寫入 /proc/<pid>/{cmdline,stat,cwd}。cwd_target 可為不存在路徑（dangling symlink）。"""
    pdir = proc / str(pid)
    pdir.mkdir()
    (pdir / "cmdline").write_bytes(b"\x00".join(a.encode("utf-8") for a in argv) + b"\x00")
    # /proc/<pid>/stat：')' 之後第 20 個 token（整行第 22 欄）= starttime
    after_paren = ["S"] + ["0"] * 18 + [str(starttime_ticks)] + ["0"] * 10
    (pdir / "stat").write_text(f"{pid} (python3) " + " ".join(after_paren) + "\n",
                               encoding="ascii")
    if cwd_target is not None:
        os.symlink(str(cwd_target), pdir / "cwd")


def expected_started_at() -> str:
    ticks = os.sysconf("SC_CLK_TCK")
    return datetime.fromtimestamp(
        _BTIME + _STARTTIME_TICKS // ticks, tz=timezone.utc).isoformat()


class ScanHippoProcessesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = make_fake_proc(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_finds_hippo_processes_with_pid_start_cmdline_cwd(self):
        home = Path.home()
        add_fake_process(
            self.proc, 4242,
            [sys.executable, "-m", "paulsha_hippo.cli", "dream", "run",
             "--memory-root", "/mem"],
            cwd_target=home)
        add_fake_process(self.proc, 4245, ["/usr/bin/sleep", "100"], cwd_target=home)

        records = ops.scan_hippo_processes(proc_root=self.proc)

        self.assertEqual([r["pid"] for r in records], [4242])
        record = records[0]
        self.assertEqual(record["argv"][1:4], ["-m", "paulsha_hippo.cli", "dream"])
        self.assertIn("paulsha_hippo.cli dream run", record["cmdline"])
        self.assertEqual(record["started_at"], expected_started_at())
        self.assertEqual(record["cwd"], str(home))

    def test_scan_matches_hippo_console_script_and_excludes_self(self):
        home = Path.home()
        add_fake_process(self.proc, 4243, ["/usr/local/bin/hippo", "dream", "supervise"],
                         cwd_target=home)
        # 自身 PID 必須被排除（doctor 不把自己當孤兒）
        add_fake_process(self.proc, os.getpid(),
                         [sys.executable, "-m", "paulsha_hippo.cli", "doctor"],
                         cwd_target=home)

        records = ops.scan_hippo_processes(proc_root=self.proc)

        self.assertEqual([r["pid"] for r in records], [4243])

    def test_malformed_stat_yields_unknown_started_at(self):
        home = Path.home()
        add_fake_process(self.proc, 4244,
                         [sys.executable, "-m", "paulsha_hippo.cli", "dream", "run"],
                         cwd_target=home)
        (self.proc / "4244" / "stat").write_text("garbage\n", encoding="ascii")

        records = ops.scan_hippo_processes(proc_root=self.proc)

        self.assertEqual(records[0]["started_at"], "unknown")


class DreamProcessReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = make_fake_proc(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_orphan_with_foreign_interpreter_and_dead_cwd_is_non_canonical(self):
        add_fake_process(
            self.proc, 4242,
            ["/fake-venv/bin/python3", "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=self.base / "gone-worktree")  # dangling symlink
        add_fake_process(
            self.proc, 4243,
            [sys.executable, "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=Path.home())
        # 非 dream 的 hippo 進程（importer）不進 dream 報告
        add_fake_process(
            self.proc, 4244,
            [sys.executable, "-m", "paulsha_hippo.importer.cli", "ingest",
             "--queue-item", "/q.json", "--memory-root", "/mem"],
            cwd_target=Path.home())

        with mock.patch.object(ops.os, "kill",
                               side_effect=AssertionError("報告面不得發 signal")):
            reports = ops.dream_process_report(
                proc_root=self.proc, canonical_interpreter=sys.executable)

        by_pid = {r["pid"]: r for r in reports}
        self.assertEqual(set(by_pid), {4242, 4243})
        self.assertTrue(by_pid[4242]["non_canonical"])
        self.assertEqual(by_pid[4242]["reasons"], ["interpreter-mismatch", "cwd-missing"])
        self.assertFalse(by_pid[4243]["non_canonical"])
        self.assertEqual(by_pid[4243]["reasons"], [])

    def test_temp_worktree_cwd_is_flagged(self):
        worktree = self.base / ".psc_tmp" / "moc-abc123"
        worktree.mkdir(parents=True)
        add_fake_process(
            self.proc, 4242,
            [sys.executable, "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=worktree)

        reports = ops.dream_process_report(
            proc_root=self.proc, canonical_interpreter=sys.executable)

        self.assertTrue(reports[0]["non_canonical"])
        self.assertIn("cwd-temp-worktree", reports[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_runtime_health.py -v`
Expected: 全數 ERROR/FAIL，訊息含 `AttributeError: module 'paulsha_hippo.ops' has no attribute 'scan_hippo_processes'`

- [ ] **Step 3: 最小實作**

`paulsha_hippo/ops.py` import 區（現行第 6-15 行）補兩行（維持字母序；`fcntl` 留到 Task 5 首次使用時才加）：

```python
import tempfile
from datetime import datetime, timezone
```

（加完後 stdlib import 依序為 `os`、`shutil`、`subprocess`、`sys`、`tempfile`、`time`，再 `from datetime import datetime, timezone`、`from pathlib import Path`。）

檔尾（`run_dream_supervise` 之後）追加整個新 section：

```python
# ---------------------------------------------------------------- runtime hygiene (#19)

_TEMP_WORKTREE_SEGMENTS = {".psc_tmp", ".test-work"}


def _iter_pids(proc_root: Path) -> list[int]:
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return []
    return sorted(int(name) for name in entries if name.isdigit())


def _read_cmdline(proc_root: Path, pid: int) -> list[str]:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\x00") if part]


def _read_started_at(proc_root: Path, pid: int) -> str:
    """btime + starttime/SC_CLK_TCK → ISO UTC；任一環節失敗回 'unknown'（診斷 fail-open）。"""
    try:
        btime: int | None = None
        for line in (proc_root / "stat").read_text(
                encoding="ascii", errors="replace").splitlines():
            if line.startswith("btime "):
                btime = int(line.split()[1])
                break
        if btime is None:
            return "unknown"
        stat = (proc_root / str(pid) / "stat").read_text(
            encoding="ascii", errors="replace")
        fields = stat.rpartition(")")[2].split()
        starttime = int(fields[19])  # 整行第 22 欄（')' 後第 20 個 token）
        ticks = os.sysconf("SC_CLK_TCK")
        return datetime.fromtimestamp(btime + starttime // ticks,
                                      tz=timezone.utc).isoformat()
    except (OSError, ValueError, IndexError):
        return "unknown"


def _read_cwd(proc_root: Path, pid: int) -> str | None:
    try:
        return os.readlink(proc_root / str(pid) / "cwd")
    except OSError:
        return None


def scan_hippo_processes(*, proc_root: str | Path = "/proc") -> list[dict[str, object]]:
    """列出 cmdline 涉及 paulsha_hippo（或 argv[0] 為 hippo）的其他進程。

    只讀 /proc、不發任何 signal；排除自身 PID。proc_root 可注入假目錄供測試。
    """
    root = Path(proc_root)
    records: list[dict[str, object]] = []
    for pid in _iter_pids(root):
        if pid == os.getpid():
            continue
        argv = _read_cmdline(root, pid)
        if not argv:
            continue
        is_hippo = any("paulsha_hippo" in token for token in argv) \
            or Path(argv[0]).name == "hippo"
        if not is_hippo:
            continue
        records.append({
            "pid": pid,
            "argv": argv,
            "cmdline": " ".join(argv),
            "started_at": _read_started_at(root, pid),
            "cwd": _read_cwd(root, pid),
        })
    return records


def dream_process_report(*, proc_root: str | Path = "/proc",
                         canonical_interpreter: str | None = None
                         ) -> list[dict[str, object]]:
    """dream/supervise 進程健康報告素材：附 non_canonical 標記與 reasons。

    只報告，不自動 kill（#19）。reason tokens：
      interpreter-mismatch —— argv[0]（絕對路徑時）不在本安裝環境的 interpreter 目錄
      cwd-missing          —— 進程 cwd 已不存在（多半是被清掉的暫存 worktree）
      cwd-temp-worktree    —— 進程 cwd 位於暫存區（.psc_tmp / .test-work / tempdir）
    """
    canonical = Path(canonical_interpreter or sys.executable).resolve(strict=False)
    reports: list[dict[str, object]] = []
    for record in scan_hippo_processes(proc_root=proc_root):
        argv = [str(token) for token in record["argv"]]  # type: ignore[union-attr]
        if "dream" not in argv:
            continue
        reasons: list[str] = []
        if argv[0].startswith("/"):
            argv0 = Path(argv[0]).resolve(strict=False)
            if argv0.parent != canonical.parent:
                reasons.append("interpreter-mismatch")
        cwd = record["cwd"]
        if isinstance(cwd, str):
            cwd_path = Path(cwd)
            if not cwd_path.exists():
                reasons.append("cwd-missing")
            elif any(part in _TEMP_WORKTREE_SEGMENTS for part in cwd_path.parts) \
                    or str(cwd_path).startswith(tempfile.gettempdir() + os.sep):
                reasons.append("cwd-temp-worktree")
        report = dict(record)
        report["non_canonical"] = bool(reasons)
        report["reasons"] = reasons
        reports.append(report)
    return reports
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_runtime_health.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ops.py tests/test_runtime_health.py
git commit -m "feat(ops): /proc 進程掃描與 dream 進程健康報告素材（#19，只報不殺）"
```

---

### Task 5: dream lock 持鎖狀態（契約 3）＋ doctor runtime 健康報告段落

**Files:**
- Modify: `paulsha_hippo/ops.py:94-121`（`run_doctor`——注意 PR-A 可能已改此函式：錨定「最終 `return 1 if failed else 0` 之前」插入，保留 PR-A 段落）與檔尾 runtime hygiene section（追加兩個函式）
- Test: `tests/test_runtime_health.py`（追加 `DreamLockStatusTest`、`DoctorRuntimeHealthTest`）

**Interfaces:**
- Consumes:
  - 契約 3 固定路徑 `<memory_root>/runtime/locks/dream.lock`（PR-A 建立；本 task 只讀探測，路徑逐字一致）
  - `dream_process_report`（Task 4）
  - 既有 `paths.memory_root() -> Path`（`run_doctor` 內現行第 106 行已解析為區域變數 `memory_root`）
- Produces:
  - `paulsha_hippo.ops.dream_lock_status(memory_root: Path) -> str` —— 回傳 `"absent" | "free" | "held" | "unknown"`（LOCK_EX|LOCK_NB 點時探測、立即釋放，不長持）
  - `paulsha_hippo.ops._print_runtime_health(memory_root: Path, *, proc_root: str | Path = "/proc") -> None` —— doctor 報告段落（stdout）
  - `ops.run_doctor` 新增 keyword-only 參數 `proc_root: str | Path = "/proc"`（預設不變，report-only、不影響 exit code）

- [ ] **Step 1: 寫失敗測試（追加到 `tests/test_runtime_health.py`，`if __name__` 前）**

```python
class DreamLockStatusTest(unittest.TestCase):
    def test_absent_free_held(self):
        import fcntl as _fcntl

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # absent：契約 3 路徑 <memory_root>/runtime/locks/dream.lock 尚未存在
            self.assertEqual(ops.dream_lock_status(root), "absent")

            lock_path = root / "runtime" / "locks" / "dream.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.touch()
            self.assertEqual(ops.dream_lock_status(root), "free")

            with lock_path.open("a+", encoding="utf-8") as holder:
                _fcntl.flock(holder, _fcntl.LOCK_EX)  # 模擬 PR-A dream run 整輪持鎖
                self.assertEqual(ops.dream_lock_status(root), "held")
            self.assertEqual(ops.dream_lock_status(root), "free")


class DoctorRuntimeHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = make_fake_proc(self.base)
        self.memory_root = self.base / "memory"
        (self.memory_root / "runtime" / "locks").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_doctor_reports_dream_lock_and_identifies_fake_orphan(self):
        """驗收（spec §3.4）：doctor 能識別偽造的孤兒進程 fixture——只報告，不 kill。

        偽造 pid 4242 非真實進程；若實作誤發 signal 會 ProcessLookupError 直接紅燈。
        """
        add_fake_process(
            self.proc, 4242,
            ["/fake-venv/bin/python3", "-m", "paulsha_hippo.cli", "dream", "run"],
            cwd_target=self.base / "gone-worktree")
        (self.memory_root / "runtime" / "locks" / "dream.lock").touch()

        env = {"HIPPO_MEMORY_ROOT": str(self.memory_root),
               "PSC_MEMORY_ROOT": str(self.memory_root)}
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", env), redirect_stdout(buffer):
            # 不斷言 return code：PR-A 的 backend probe 段落可能因環境無 backend 而非零；
            # 本 task 的段落是 report-only、不改 exit code。
            ops.run_doctor(proc_root=self.proc)
        out = buffer.getvalue()

        self.assertIn("dream lock（runtime/locks/dream.lock）：free", out)
        self.assertIn("dream/supervise 進程：1 個（只報告，不自動 kill）", out)
        self.assertIn("pid=4242", out)
        self.assertIn("non-canonical[interpreter-mismatch,cwd-missing]", out)

    def test_doctor_reports_no_processes_when_proc_is_quiet(self):
        env = {"HIPPO_MEMORY_ROOT": str(self.memory_root),
               "PSC_MEMORY_ROOT": str(self.memory_root)}
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", env), redirect_stdout(buffer):
            ops.run_doctor(proc_root=self.proc)
        out = buffer.getvalue()

        self.assertIn("dream lock（runtime/locks/dream.lock）：absent", out)
        self.assertIn("dream/supervise 進程：無", out)
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_runtime_health.py -v -k "DreamLockStatus or DoctorRuntimeHealth"`
Expected: FAIL/ERROR，訊息含 `AttributeError: module 'paulsha_hippo.ops' has no attribute 'dream_lock_status'` 與 `run_doctor() got an unexpected keyword argument 'proc_root'`

- [ ] **Step 3: 最小實作**

`paulsha_hippo/ops.py` import 區補一行（維持字母序，置於 `import os` 之前）：

```python
import fcntl
```

檔尾 runtime hygiene section（Task 4 之後）追加：

```python
def _dream_lock_path(memory_root: Path) -> Path:
    """契約 3：global dream lock 固定路徑（PR-A 於 dream run 入口整輪持有）。"""
    return Path(memory_root) / "runtime" / "locks" / "dream.lock"


def dream_lock_status(memory_root: Path) -> str:
    """點時探測 dream lock：absent / free / held / unknown。

    以 LOCK_EX|LOCK_NB 探測並立即釋放，不長持；探測瞬間與同時啟動的
    dream run 存在極小視窗（對方 LOCK_NB 會失敗跳過一輪），屬診斷面可接受成本。
    """
    lock_path = _dream_lock_path(memory_root)
    if not lock_path.exists():
        return "absent"
    try:
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return "held"
            fcntl.flock(handle, fcntl.LOCK_UN)
            return "free"
    except OSError:
        return "unknown"


def _print_runtime_health(memory_root: Path, *,
                          proc_root: str | Path = "/proc") -> None:
    """doctor 的 runtime 健康報告段落（#19）：只報告，不自動 kill、不影響 exit code。"""
    print(f"- dream lock（runtime/locks/dream.lock）：{dream_lock_status(memory_root)}")
    reports = dream_process_report(proc_root=proc_root)
    if not reports:
        print("- dream/supervise 進程：無")
        return
    print(f"- dream/supervise 進程：{len(reports)} 個（只報告，不自動 kill）")
    for report in reports:
        if report["non_canonical"]:
            mark = "non-canonical[" + ",".join(report["reasons"]) + "]"  # type: ignore[arg-type]
        else:
            mark = "canonical"
        print(f"  - pid={report['pid']} start={report['started_at']} {mark} "
              f"cwd={report['cwd']} cmdline={report['cmdline']}")
```

`run_doctor` 修改（現行 `ops.py:94`；若 PR-A 已改簽名則在其現有參數後追加本參數）：

```python
def run_doctor() -> int:
```

改為：

```python
def run_doctor(*, proc_root: str | Path = "/proc") -> int:
```

並在函式**最終 `return 1 if failed else 0`（現行第 121 行）之前**插入（`memory_root` 為函式內既有區域變數，現行第 106 行）：

```python
    _print_runtime_health(memory_root, proc_root=proc_root)
```

（PR-A 若已在 doctor 內加入 backend probe 段落，本行放在該段落之後、最終 return 之前；不動 PR-A 的任何行為與 `failed` 判定。）

- [ ] **Step 4: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_runtime_health.py tests/test_ops.py -v`
Expected: 全數 passed（`test_ops.py` 的既有 DoctorTests 不得受影響——新段落 report-only，不改 exit code）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ops.py tests/test_runtime_health.py
git commit -m "feat(ops): doctor 新增 runtime 健康報告——dream lock 狀態＋進程清單（#19，只報不殺）"
```

---

### Task 6: legacy locks 一次性清理命令（雙層安全閘）

**Files:**
- Modify: `paulsha_hippo/ops.py`（檔尾 runtime hygiene section 追加 `cleanup_legacy_locks`）
- Modify: `paulsha_hippo/cli.py:233-239`（`usage_p` 區塊之後、`return parser` 之前插入 `locks` 子命令）與檔尾 handler 區（`_dream_supervise` 之後）
- Test: `tests/test_locks_cleanup.py`（新檔）

**Interfaces:**
- Consumes:
  - `scan_hippo_processes(*, proc_root)`（Task 4——進程閘）
  - `paulsha_hippo.importer.pipeline.is_shard_lock_name(name: str) -> bool`（Task 1——keep-set）
  - 契約 5：`memory_subparsers.add_parser` 既有模式
- Produces:
  - `paulsha_hippo.ops.cleanup_legacy_locks(memory_root: Path, *, apply: bool = False, proc_root: str | Path = "/proc") -> dict[str, object]` —— 回傳 keys：`locks_dir: str`、`legacy: list[str]`、`kept: list[str]`、`other_processes: list[{"pid","cmdline"}]`、`applied: bool`、`deleted: list[str]`、`busy: list[str]`、（apply 被進程閘擋下時）`blocked: str`
  - CLI `hippo locks cleanup-legacy --memory-root <root> [--apply]` —— 預設 dry-run；stdout 輸出 JSON 摘要；`blocked` 或 `busy` 非空時 exit 1，否則 0
  - keep-set 定義：`{"import-ledger.lock", "dream.lock"}` ∪ shard 命名（`is_shard_lock_name`）；其餘 `*.lock` 視為 legacy；非 `.lock` 檔一律不碰

- [ ] **Step 1: 寫失敗測試（新檔 `tests/test_locks_cleanup.py`）**

```python
"""#19 PR-C：legacy per-session lock 一次性清理（僅維護窗口；雙層安全閘）。"""
from __future__ import annotations

import fcntl
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli, ops


def _make_proc(base: Path) -> Path:
    """本檔自足的最小假 /proc（不跨測試模組 import——tests/ 非 package）。"""
    proc = base / "proc"
    proc.mkdir()
    (proc / "stat").write_text("btime 1751900000\n", encoding="ascii")
    return proc


def _add_proc(proc: Path, pid: int, argv: list[str]) -> None:
    """清理閘只需要 pid + cmdline；不寫 stat/cwd（started_at 走 'unknown' fail-open）。"""
    pdir = proc / str(pid)
    pdir.mkdir()
    (pdir / "cmdline").write_bytes(b"\x00".join(a.encode("utf-8") for a in argv) + b"\x00")


class CleanupLegacyLocksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.proc = _make_proc(self.base)  # 空 fake /proc = 維護窗口（無其他進程）
        self.memory_root = self.base / "memory"
        self.locks_dir = self.memory_root / "runtime" / "locks"
        self.locks_dir.mkdir(parents=True)
        # 現行命名（必須保留）
        (self.locks_dir / "import-ledger.lock").touch()
        (self.locks_dir / "dream.lock").touch()          # 契約 3（PR-A）
        (self.locks_dir / "lock_shard_08.lock").touch()  # 契約 4（本 PR）
        # legacy per-session 命名（清理對象）
        (self.locks_dir / "copilot-cli__sid-001.lock").touch()
        (self.locks_dir / "claude-code__abc123.lock").touch()
        # 非 .lock 檔一律不碰
        (self.locks_dir / "README.txt").touch()

    def tearDown(self):
        self.tmp.cleanup()

    def all_names(self) -> set[str]:
        return {p.name for p in self.locks_dir.iterdir()}

    def test_dry_run_lists_legacy_without_deleting(self):
        result = ops.cleanup_legacy_locks(self.memory_root, apply=False,
                                          proc_root=self.proc)

        self.assertEqual(result["legacy"],
                         ["claude-code__abc123.lock", "copilot-cli__sid-001.lock"])
        self.assertEqual(result["kept"],
                         ["dream.lock", "import-ledger.lock", "lock_shard_08.lock"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["deleted"], [])
        # dry-run 完全不動檔案
        self.assertIn("copilot-cli__sid-001.lock", self.all_names())
        self.assertIn("README.txt", self.all_names())

    def test_apply_deletes_only_legacy_and_keeps_current_names(self):
        result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                          proc_root=self.proc)

        self.assertTrue(result["applied"])
        self.assertEqual(result["deleted"],
                         ["claude-code__abc123.lock", "copilot-cli__sid-001.lock"])
        self.assertEqual(result["busy"], [])
        self.assertEqual(
            self.all_names(),
            {"import-ledger.lock", "dream.lock", "lock_shard_08.lock", "README.txt"})

    def test_apply_is_blocked_when_other_hippo_process_running(self):
        """安全閘第 1 層：偵測到其他 hippo 進程（舊 importer 可能執行中）→ 拒絕清理。"""
        _add_proc(
            self.proc, 5151,
            [sys.executable, "-m", "paulsha_hippo.importer.cli", "ingest",
             "--queue-item", "/q.json", "--memory-root", "/mem"])

        result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                          proc_root=self.proc)

        self.assertIn("blocked", result)
        self.assertFalse(result["applied"])
        self.assertEqual([p["pid"] for p in result["other_processes"]], [5151])
        self.assertIn("copilot-cli__sid-001.lock", self.all_names())  # 未刪任何檔

    def test_apply_skips_actively_flocked_legacy_lock(self):
        """安全閘第 2 層：逐檔 flock 探測——被持有的 legacy lock 跳過不刪（busy）。"""
        held = self.locks_dir / "copilot-cli__sid-001.lock"
        with held.open("a+", encoding="utf-8") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            result = ops.cleanup_legacy_locks(self.memory_root, apply=True,
                                              proc_root=self.proc)

        self.assertEqual(result["busy"], ["copilot-cli__sid-001.lock"])
        self.assertEqual(result["deleted"], ["claude-code__abc123.lock"])
        self.assertIn("copilot-cli__sid-001.lock", self.all_names())

    def test_missing_locks_dir_is_a_clean_noop(self):
        empty_root = self.base / "empty-memory"
        result = ops.cleanup_legacy_locks(empty_root, apply=True, proc_root=self.proc)

        self.assertEqual(result["legacy"], [])
        self.assertEqual(result["deleted"], [])
        self.assertTrue(result["applied"])


class LocksCleanupCliTest(unittest.TestCase):
    def test_cli_dry_run_prints_json_and_exits_zero(self):
        with TemporaryDirectory() as tmp:
            memory_root = Path(tmp) / "memory"
            locks_dir = memory_root / "runtime" / "locks"
            locks_dir.mkdir(parents=True)
            (locks_dir / "copilot-cli__sid-001.lock").touch()

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                # dry-run（無 --apply）：真實 /proc 上就算有其他 hippo 進程也只列出、exit 0
                rc = cli.main(["locks", "cleanup-legacy",
                               "--memory-root", str(memory_root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["legacy"], ["copilot-cli__sid-001.lock"])
            self.assertFalse(payload["applied"])
            self.assertTrue((locks_dir / "copilot-cli__sid-001.lock").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `python3 -m pytest tests/test_locks_cleanup.py -v`
Expected: FAIL/ERROR，訊息含 `AttributeError: module 'paulsha_hippo.ops' has no attribute 'cleanup_legacy_locks'`；CLI 測試 `cli.main` 回 2（argparse 不認得 `locks`）

- [ ] **Step 3: 最小實作——ops.cleanup_legacy_locks**

`paulsha_hippo/ops.py` 檔尾 runtime hygiene section（Task 5 之後）追加：

```python
_KEEP_LOCK_NAMES = {"import-ledger.lock", "dream.lock"}


def cleanup_legacy_locks(memory_root: Path, *, apply: bool = False,
                         proc_root: str | Path = "/proc") -> dict[str, object]:
    """#19：legacy per-session lock 檔一次性清理（僅維護窗口執行）。

    #19 教訓：執行中直接 unlink lock 檔會破壞 flock 互斥（新開者 rendezvous 到新
    inode）。因此雙層安全閘：
      1. 進程閘：偵測到其他 paulsha_hippo/hippo 進程（可能是尚未升版的 importer）
         → apply 直接拒絕（result["blocked"]），一檔不刪。
      2. flock 閘：逐檔 LOCK_EX|LOCK_NB 探測，busy 檔跳過（result["busy"]）。
    keep-set：import-ledger.lock、dream.lock（契約 3）、lock_shard_XX.lock（契約 4）。
    非 .lock 檔一律不碰。預設 dry-run（apply=False）只列清單。
    """
    from paulsha_hippo.importer.pipeline import is_shard_lock_name

    locks_dir = Path(memory_root) / "runtime" / "locks"
    others = scan_hippo_processes(proc_root=proc_root)
    legacy: list[str] = []
    kept: list[str] = []
    if locks_dir.is_dir():
        for path in sorted(locks_dir.iterdir()):
            if not path.is_file() or path.suffix != ".lock":
                continue
            if path.name in _KEEP_LOCK_NAMES or is_shard_lock_name(path.name):
                kept.append(path.name)
            else:
                legacy.append(path.name)
    result: dict[str, object] = {
        "locks_dir": str(locks_dir),
        "legacy": legacy,
        "kept": kept,
        "other_processes": [{"pid": r["pid"], "cmdline": r["cmdline"]} for r in others],
        "applied": False,
        "deleted": [],
        "busy": [],
    }
    if not apply:
        return result
    if others:
        result["blocked"] = "偵測到其他 hippo 進程，維護窗口未確立；拒絕清理"
        return result
    deleted: list[str] = []
    busy: list[str] = []
    for name in legacy:
        path = locks_dir / name
        try:
            with path.open("a+", encoding="utf-8") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    busy.append(name)
                    continue
                try:
                    path.unlink()
                    deleted.append(name)
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            busy.append(name)
    result["applied"] = True
    result["deleted"] = deleted
    result["busy"] = busy
    return result
```

- [ ] **Step 4: 最小實作——CLI 接線（契約 5 模式）**

`paulsha_hippo/cli.py`：在 `usage_p` 區塊（現行第 233-237 行）之後、`return parser`（現行第 239 行）之前插入：

```python
    locks_p = memory_subparsers.add_parser("locks", help="runtime lock 維運")
    locks_sub = locks_p.add_subparsers(dest="locks_command", required=True)
    locks_cleanup = locks_sub.add_parser(
        "cleanup-legacy",
        help="一次性清理 legacy per-session lock 檔（僅維護窗口；預設 dry-run）",
    )
    locks_cleanup.add_argument("--memory-root", required=True)
    locks_cleanup.add_argument("--apply", action="store_true")
    locks_cleanup.set_defaults(func=_locks_cleanup_legacy)
```

檔尾 handler 區：在 `_dream_supervise`（現行第 794-798 行）之後追加：

```python
def _locks_cleanup_legacy(args: argparse.Namespace) -> int:
    from paulsha_hippo import ops

    result = ops.cleanup_legacy_locks(Path(args.memory_root), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result.get("blocked") or result.get("busy"):
        return 1
    return 0
```

- [ ] **Step 5: 跑測試確認 PASS**

Run: `python3 -m pytest tests/test_locks_cleanup.py -v`
Expected: `6 passed`

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/ops.py paulsha_hippo/cli.py tests/test_locks_cleanup.py
git commit -m "feat(cli): hippo locks cleanup-legacy——legacy lock 維護窗口清理（#19，雙層安全閘）"
```

---

### Task 7: docs 同步（R-18）＋ changelog.d 碎片＋全套驗證

**Files:**
- Modify: `README.md:15`（Quickstart doctor 註解行——跨批次共用錨行，rebase 後不得整行覆蓋，見 Step 1 合併規則）與 `:27`（Usage「日常命令：」行——四批共用錨行，本批於其後補維運行，見 Step 2 合併規則）；行號皆為原始 main 快照，一律以行內容定位
- Modify: `CHANGELOG.md:8-12`（`[Unreleased]` 段——R-09 以此檔為準，policy_check 的 `_unreleased_has_bullet_entry` 檢查 `## [Unreleased]` 下有 bullet）
- Create: `changelog.d/19-lock-sharding.md`
- Test: 全套 `python3 -m pytest tests/ -q` ＋ `python3 -m policy_check --repo .`

**Interfaces:**
- Consumes: Tasks 1-6 全部產出（文件描述必須與實作逐字一致：命令名、旗標、輸出欄位）
- Produces: R-18 文件同步證據＋R-09 CHANGELOG `[Unreleased]` entry＋changelog.d 碎片（merge gate 檢查項；repo 慣例兩者並存——碎片供 release 彙整，`[Unreleased]` 供 R-09 gate）

- [ ] **Step 1: 更新 README Quickstart（doctor 註解行）**

`README.md:15`（Quickstart 段 `hippo doctor` 註解行；行號為原始 main 快照，一律以行內容定位），把：

```
    hippo doctor                        # 健檢：路徑契約/hooks/服務/backend
```

改為：

```
    hippo doctor                        # 健檢：路徑契約/hooks/服務/backend/runtime 進程與 lock
```

**合併規則（README 跨批次共用錨行；PR-A Task 12 Step 3／PR-B Task 6 Step 7／PR-F Task 7 Step 3 帶同一條規則）**：若該行已被 sibling 批次改寫（rebase 後與引用的 old 內容不符），不得整行覆蓋——以當前行內容為基底，僅追加本批新增片段，保留 sibling 已 merge 的全部新增。本批於 doctor 註解行的新增片段只有一個：在健檢清單尾端串接「/runtime 進程與 lock」；sibling 已 merge 的片段（如 PR-A 的 `--fix-backend` 括註）一律原樣保留，不得覆蓋或刪除。

- [ ] **Step 2: 更新 README Usage（「日常命令：」行之後補維運行）**

`README.md:27`（Usage 段「日常命令：」行；行號為原始 main 快照，一律以行內容定位），在該行之後補「維運：…」一行。現行：

```
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo replay`／`hippo bundle`。
```

改為：

```
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo replay`／`hippo bundle`。
維運：`hippo doctor`（含 dream lock 持鎖狀態與 dream/supervise 進程健康報告——PID/start/cmdline、非 canonical 標記，只報告不自動 kill）；`hippo locks cleanup-legacy --memory-root <root> [--apply]`（legacy per-session lock 一次性清理，預設 dry-run，僅維護窗口使用）。
```

**合併規則（README 跨批次共用錨行；PR-A Task 12 Step 3／PR-B Task 6 Step 7／PR-F Task 7 Step 3 帶同一條規則）**：若該行已被 sibling 批次改寫（rebase 後與引用的 old 內容不符），不得整行覆蓋——以當前行內容為基底，僅追加本批新增片段，保留 sibling 已 merge 的全部新增。本批新增片段只有一個：「日常命令」行本身不改（sibling 已 merge 的命令——`hippo recall`／`hippo index verify`／`hippo usage`／`hippo requeue …`——一律原樣保留），於其後插入上述「維運：…」一整行；sibling 各自的後續補充行（PR-A 蒸餾失敗顯性化／PR-F 跨 CLI 消費能力）不得覆蓋或刪除，本批維運行依落地順序接續其後。

- [ ] **Step 3: 新增 changelog.d 碎片（新檔 `changelog.d/19-lock-sharding.md`）**

```markdown
### Changed
- importer per-session lock 改為固定 64 個 hash-sharded locks（`lock_shard_{h:02x}.lock`，`h = crc32(safe_key(key)) % 64`）：`runtime/locks/` 檔案數收斂為常數上界，碰撞只降低並行度、不影響互斥正確性（#19）
- `hippo doctor` 新增 runtime 健康報告：global dream lock（`runtime/locks/dream.lock`）持鎖狀態＋dream/supervise 進程清單（PID/start time/cmdline/cwd），標記非 canonical 實例（interpreter-mismatch／cwd-missing／cwd-temp-worktree）；只報告，不自動 kill（#19）

### Added
- `hippo locks cleanup-legacy --memory-root <root> [--apply]`：legacy per-session lock 檔一次性清理；預設 dry-run，apply 受雙層安全閘保護（偵測到其他 hippo 進程即拒絕＋逐檔 flock 探測跳過 busy），僅供恢復序列維護窗口使用（#19）
```

- [ ] **Step 4: 更新 `CHANGELOG.md` `[Unreleased]` 段（R-09 gate 以此檔為準）**

在 `## [Unreleased]` 之下（現行第 9 行後、既有 `### Fixed` 段之前）插入與碎片同內容的段落：

```markdown
### Changed
- importer per-session lock 改為固定 64 個 hash-sharded locks（`lock_shard_{h:02x}.lock`，`h = crc32(safe_key(key)) % 64`）：`runtime/locks/` 檔案數收斂為常數上界，碰撞只降低並行度、不影響互斥正確性（#19）
- `hippo doctor` 新增 runtime 健康報告：global dream lock（`runtime/locks/dream.lock`）持鎖狀態＋dream/supervise 進程清單（PID/start time/cmdline/cwd），標記非 canonical 實例（interpreter-mismatch／cwd-missing／cwd-temp-worktree）；只報告，不自動 kill（#19）

### Added
- `hippo locks cleanup-legacy --memory-root <root> [--apply]`：legacy per-session lock 檔一次性清理；預設 dry-run，apply 受雙層安全閘保護（偵測到其他 hippo 進程即拒絕＋逐檔 flock 探測跳過 busy），僅供恢復序列維護窗口使用（#19）
```

（若 rebase 後 `[Unreleased]` 已有其他批次的 `### Changed`／`### Added` 標題，把 bullet 併入既有標題下，不重複標題——R-04 格式。）

- [ ] **Step 5: 全套測試**

Run: `python3 -m pytest tests/ -q`
Expected: 全數 passed、0 failed（既有 953+ tests 加上本批新增 22 個測試）

- [ ] **Step 6: policy 檢查**

Run: `python3 -m policy_check --repo .`
Expected: 零 failure（WARN 級提示不擋；R-18 已由 Step 1-2 的 README 同步滿足、R-09 由 Step 3-4 滿足）

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGELOG.md changelog.d/19-lock-sharding.md
git commit -m "docs: README 維運段＋CHANGELOG 與 changelog.d 碎片——#19 lock sharding 批次收尾"
```

---

## 驗收對照（spec §3.4）

| spec 驗收項 | 對應證據 |
|---|---|
| 並發 importer 壓力測試互斥正確 | Task 3 `test_concurrent_same_key_duplicates_yield_single_written_per_key`＋`test_colliding_keys_serialize_on_same_shard_without_error`＋既有 `test_idempotency.py` 全綠 |
| locks 目錄檔案數恆為常數 | Task 3 `test_lock_dir_file_count_stays_bounded_across_waves`（96 key > 64 shard，上界 65） |
| doctor 能識別偽造的孤兒進程 fixture | Task 5 `test_doctor_reports_dream_lock_and_identifies_fake_orphan`（fixture pid 非真實進程，誤 kill 即紅燈） |
| 新版程式不再產生舊命名 lock | Task 2 `test_ingest_creates_shard_lock_and_no_legacy_per_session_lock` |
| legacy 清理僅維護窗口、確認無舊 importer | Task 6 進程閘＋flock 閘測試（實際執行由恢復序列在 PR-C merge 後的維護窗口觸發，spec §4.8，非本 plan 範圍） |
| doctor 引用契約 3 同一路徑報告持鎖狀態 | Task 5 `_dream_lock_path` 固定 `<memory_root>/runtime/locks/dream.lock`＋`DreamLockStatusTest` |

## 設計備註（實作者需知）

1. **doctor exit code**：runtime 健康報告為 report-only，發現 non-canonical 進程**不**改變 doctor exit code（恢復序列以輸出文字為證據；既有 doctor 消費者不受影響）。
2. **dream_lock_status 探測視窗**：LOCK_NB 探測瞬間持鎖，與同瞬啟動的 dream run 有極小機率使其 skip 一輪（對方 fail-open exit 0），屬診斷面可接受成本，docstring 已標註。
3. **進程閘涵蓋範圍**：以「cmdline 含 `paulsha_hippo` 或 argv[0] basename 為 `hippo`」近似偵測；library 內嵌呼叫（無此 cmdline 特徵）偵測不到——第一層保證來自恢復序列 quiesce（spec §4.2），flock 逐檔探測為第二層兜底。
4. **flock 語義**：flock 以 open file description 為單位——同進程兩次 `open()` 同檔互斥成立（Task 3/5/6 的測試依賴此語義，已於 Linux 驗證）。
5. **測試不掃真實 `/proc`**：所有進程掃描測試一律注入 fake `proc_root`（唯一例外是 Task 6 的 CLI dry-run 測試，dry-run 對真實環境無副作用且 exit 恆 0）。
