"""#19 PR-C：importer lock sharding（契約 4）與並發互斥。"""
import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
