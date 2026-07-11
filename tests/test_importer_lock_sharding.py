"""#19 PR-C：importer lock sharding（契約 4）與並發互斥。"""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest import mock

from paulsha_hippo.importer import pipeline
from paulsha_hippo.importer.pipeline import is_shard_lock_name, shard_lock_path

REPO_ROOT = Path(__file__).resolve().parents[1]


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


class ConcurrentShardStressTest(_ScratchCase):
    def ledger_statuses(self) -> list[str]:
        ledger = self.root / "runtime" / "ledger" / "import.jsonl"
        if not ledger.exists():
            return []
        return [
            json.loads(line)["status"]
            for line in ledger.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def test_concurrent_same_key_duplicates_yield_single_written_per_key(self):
        """互斥正確性：8 個 key 各 6 份相同 payload 併發 → 每 key 恰一 written。"""
        items: list[tuple[str, Path]] = []
        for k in range(8):
            payload = _stress_payload(session_id=f"sid-dup-{k:02d}", cwd=str(self.root))
            for d in range(6):
                items.append(
                    (
                        f"sid-dup-{k:02d}",
                        self.write_queue_item(f"dup-{k:02d}-{d}", payload),
                    )
                )

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [
                (
                    key,
                    executor.submit(
                        pipeline.ingest_queue_item,
                        path,
                        memory_root=self.root,
                    ),
                )
                for key, path in items
            ]
            results = [(key, future.result(timeout=60)) for key, future in futures]

        by_key: dict[str, list[str]] = {}
        for key, decision in results:
            by_key.setdefault(key, []).append(decision["status"])
        for key, statuses in by_key.items():
            self.assertEqual(
                sorted(statuses),
                ["hash-duplicate"] * 5 + ["written"],
                f"key {key} 併發去重錯誤: {statuses}",
            )
        # 佇列全數消化、ledger 每行皆完整 JSON（flock 互斥未被 sharding 破壞）
        self.assertFalse(any(path.exists() for _, path in items))
        statuses = self.ledger_statuses()
        self.assertEqual(len(statuses), 48)
        self.assertEqual(statuses.count("written"), 8)
        self.assertEqual(statuses.count("hash-duplicate"), 40)

    def test_lock_dir_file_count_stays_bounded_across_waves(self):
        """locks 目錄檔案數恆為常數上界：96 個相異 key（> 64 shard，鴿籠必碰撞）
        分兩波併發 ingest，lock 檔集合始終 ⊆ 64 shard 名 ∪ {import-ledger.lock}。"""
        universe = {f"lock_shard_{h:02x}.lock" for h in range(64)} | {
            "import-ledger.lock"
        }

        def run_wave(start: int, count: int) -> None:
            paths = [
                self.write_queue_item(
                    f"wave-{i:03d}",
                    _stress_payload(
                        session_id=f"sid-stress-{i:03d}",
                        cwd=str(self.root),
                        turns=1 + i % 3,
                    ),
                )
                for i in range(start, start + count)
            ]
            with ThreadPoolExecutor(max_workers=16) as executor:
                decisions = list(
                    executor.map(
                        lambda p: pipeline.ingest_queue_item(p, memory_root=self.root),
                        paths,
                    )
                )
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
            "collide-a",
            _stress_payload(session_id="sid-stress-000", cwd=str(self.root)),
        )
        second_queue = self.write_queue_item(
            "collide-b",
            _stress_payload(session_id="sid-stress-126", cwd=str(self.root)),
        )
        lock_path = pipeline.shard_lock_path(self.root, "copilot-cli:sid-stress-000")
        self.assertEqual(
            lock_path,
            pipeline.shard_lock_path(self.root, "copilot-cli:sid-stress-126"),
        )

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
            if (
                Path(lock_handle.name) == lock_path
                and operation & _fcntl.LOCK_EX
                and first_holding.is_set()
            ):
                second_attempted.set()
            return real_flock(lock_handle, operation)

        with (
            mock.patch(
                "paulsha_hippo.importer.pipeline.preview_queue_item",
                side_effect=unlocked_preview,
            ),
            mock.patch(
                "paulsha_hippo.importer.pipeline._archive_queue",
                side_effect=blocking_archive,
            ),
            mock.patch(
                "paulsha_hippo.importer.pipeline.fcntl.flock",
                side_effect=instrumented_flock,
            ),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    pipeline.ingest_queue_item,
                    first_queue,
                    memory_root=self.root,
                )
                self.assertTrue(first_holding.wait(timeout=10))
                second_future = executor.submit(
                    pipeline.ingest_queue_item,
                    second_queue,
                    memory_root=self.root,
                )
                self.assertTrue(second_attempted.wait(timeout=10))
                self.assertFalse(second_future.done())  # 碰撞 → 序列化等待
                release_first.set()
                first_decision = first_future.result(timeout=10)
                second_decision = second_future.result(timeout=10)

        self.assertEqual(first_decision["status"], "written")
        self.assertEqual(second_decision["status"], "written")  # 相異 key，各自寫入
        self.assertEqual(sorted(self.ledger_statuses()), ["written", "written"])


if __name__ == "__main__":
    unittest.main()
