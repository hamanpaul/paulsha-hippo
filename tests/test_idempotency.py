import json
import tempfile
import time
import unittest
import subprocess
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from unittest import mock

from paulsha_hippo.importer import pipeline
from paulsha_hippo.importer.pipeline import ingest_queue_item


REPO_ROOT = Path(__file__).resolve().parents[1]


def _init_repo(path: Path, remote: str | None = None) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)


class IdempotencyPipelineTest(unittest.TestCase):
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

    def payload(self, *, session_id="sid-001", scope="turn", turns=1, files=None, prompts=None, ended_at="2026-05-24T10:00:00+00:00"):
        return {
            "tool": "copilot-cli",
            "session_id": session_id,
            "capture_scope": scope,
            "ended_at": ended_at,
            "cwd": str(REPO_ROOT),
            "repo": "hamanpaul/paulshaclaw",
            "commit": "e300b08",
            "turn_count": turns,
            "user_prompts": prompts if prompts is not None else ["implement importer"],
            "assistant_summary": "summary",
            "touched_files": files if files is not None else ["a.py"],
            "referenced_artifacts": ["docs/spec.md"],
        }

    def write_queue_item(self, name, payload):
        path = self.queue / f"{name}.json"
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    def write_queue_item_at(self, relative_path, payload):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    def ledger_entries(self):
        ledger = self.root / "runtime" / "ledger" / "import.jsonl"
        if not ledger.exists():
            return []
        return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]

    def expected_hash(self, payload):
        subset = (
            payload["session_id"],
            payload["capture_scope"],
            payload["turn_count"],
            payload["ended_at"],
            sorted(payload["touched_files"]),
            len(payload["user_prompts"]),
        )
        canonical = json.dumps(subset, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    def wait_until(self, predicate, *, timeout=2.0, interval=0.01):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def test_first_write_then_identical_retry_is_hash_duplicate(self):
        payload = self.payload()
        first = self.write_queue_item("first", payload)
        retry = self.write_queue_item("retry", payload)

        first_decision = ingest_queue_item(first, memory_root=self.root)
        retry_decision = ingest_queue_item(retry, memory_root=self.root)

        self.assertEqual(first_decision["status"], "written")
        self.assertEqual(retry_decision["status"], "hash-duplicate")
        self.assertEqual(first_decision["content_hash"], self.expected_hash(payload))
        inbox = self.root / "inbox" / "sessions" / "copilot-cli" / "2026-05-24" / "sid-001.md"
        self.assertTrue(inbox.exists())
        self.assertIn("source_session: sid-001", inbox.read_text(encoding="utf-8"))
        archive = self.root / "archive" / "queue" / "2026-05" / f"copilot-cli__sid-001--written--{self.expected_hash(payload)[:12]}.json"
        self.assertTrue(archive.exists())
        self.assertEqual([entry["status"] for entry in self.ledger_entries()], ["written", "hash-duplicate"])

    def test_written_markdown_uses_archived_payload_path_for_persisted_provenance(self):
        payload = self.payload(session_id="sid-provenance-archive")
        queue_item = self.write_queue_item("provenance-archive", payload)

        decision = ingest_queue_item(queue_item, memory_root=self.root)

        inbox = Path(decision["inbox_path"])
        archive = Path(decision["archive_path"])
        rendered = inbox.read_text(encoding="utf-8")
        self.assertFalse(queue_item.exists())
        self.assertTrue(archive.exists())
        self.assertIn(f"  path: {archive}", rendered)
        self.assertIn(f"- Raw payload: {archive}", rendered)
        self.assertNotIn(f"  path: {queue_item}", rendered)
        self.assertNotIn(f"- Raw payload: {queue_item}", rendered)

    def test_written_markdown_uses_detected_git_remote_for_persisted_provenance_repo(self):
        repo = self.root.parent / "remote-repo"
        worktree = repo / "nested" / "cwd"
        worktree.mkdir(parents=True)
        _init_repo(repo, "git@github.com:owner/x.git")
        payload = self.payload(session_id="sid-provenance-remote", scope="session_end")
        payload["cwd"] = str(worktree)
        payload["repo"] = "hamanpaul/other-repo"
        queue_item = self.write_queue_item("provenance-remote", payload)

        decision = ingest_queue_item(queue_item, memory_root=self.root)

        inbox = Path(decision["inbox_path"])
        rendered = inbox.read_text(encoding="utf-8")
        self.assertIn("  repo: github.com/owner/x", rendered)
        self.assertNotIn("  repo: hamanpaul/other-repo", rendered)

    def test_higher_completeness_updates_and_lower_completeness_stale_skips(self):
        base = self.payload(scope="turn", turns=1, files=["a.py"], prompts=["one"])
        richer = self.payload(scope="turn", turns=2, files=["a.py", "b.py"], prompts=["one", "two"])
        stale = self.payload(scope="turn", turns=1, files=["a.py"], prompts=["one"], ended_at="2026-05-24T10:05:00+00:00")

        decisions = [
            ingest_queue_item(self.write_queue_item("base", base), memory_root=self.root),
            ingest_queue_item(self.write_queue_item("richer", richer), memory_root=self.root),
            ingest_queue_item(self.write_queue_item("stale", stale), memory_root=self.root),
        ]

        self.assertEqual([decision["status"] for decision in decisions], ["written", "updated", "stale-skip"])
        self.assertEqual(decisions[1]["from_completeness"], [0, 1, 1, 1])
        self.assertEqual(decisions[1]["to_completeness"], [0, 2, 2, 2])
        self.assertEqual(decisions[2]["from_completeness"], [0, 2, 2, 2])
        self.assertEqual(decisions[2]["to_completeness"], [0, 1, 1, 1])
        inbox = self.root / "inbox" / "sessions" / "copilot-cli" / "2026-05-24" / "sid-001.md"
        rendered = inbox.read_text(encoding="utf-8")
        self.assertIn("2. two", rendered)
        self.assertIn("- b.py", rendered)
        self.assertEqual([entry["status"] for entry in self.ledger_entries()], ["written", "updated", "stale-skip"])

    def test_watcher_final_updates_session_end_because_scope_rank_is_higher(self):
        session_end = self.payload(scope="session_end", turns=3, files=["a.py"], prompts=["one"])
        watcher_final = self.payload(scope="watcher_final", turns=3, files=["a.py"], prompts=["one"])

        first = ingest_queue_item(self.write_queue_item("session_end", session_end), memory_root=self.root)
        final = ingest_queue_item(self.write_queue_item("watcher_final", watcher_final), memory_root=self.root)

        self.assertEqual(first["status"], "written")
        self.assertEqual(final["status"], "updated")
        self.assertNotEqual(first["content_hash"], final["content_hash"])
        self.assertEqual(final["from_completeness"], [1, 3, 1, 1])
        self.assertEqual(final["to_completeness"], [2, 3, 1, 1])
        self.assertEqual([entry["status"] for entry in self.ledger_entries()], ["written", "updated"])

    def test_updated_reclassification_moves_inbox_file_to_new_bucket(self):
        first_payload = self.payload(
            session_id="sid-bucket-move",
            scope="turn",
            turns=1,
            files=["src/main.py"],
            prompts=["implement importer"],
        )
        updated_payload = self.payload(
            session_id="sid-bucket-move",
            scope="session_end",
            turns=1,
            files=["docs/plan.md"],
            prompts=["implementation plan"],
        )

        first = ingest_queue_item(self.write_queue_item("bucket-first", first_payload), memory_root=self.root)
        updated = ingest_queue_item(self.write_queue_item("bucket-updated", updated_payload), memory_root=self.root)

        sessions_path = self.root / "inbox" / "sessions" / "copilot-cli" / "2026-05-24" / "sid-bucket-move.md"
        plans_path = self.root / "inbox" / "plans" / "copilot-cli" / "2026-05-24" / "sid-bucket-move.md"
        self.assertEqual(first["classifier_bucket"], "sessions")
        self.assertEqual(updated["classifier_bucket"], "plans")
        self.assertEqual(updated["status"], "updated")
        self.assertFalse(sessions_path.exists())
        self.assertTrue(plans_path.exists())
        self.assertIn("source_artifact: plan", plans_path.read_text(encoding="utf-8"))

    def test_pipeline_uses_repo_field_as_git_toplevel_fallback_for_project_resolution(self):
        config_dir = self.root.parent / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "projects.yaml").write_text(
            "\n".join(
                [
                    "version: 1",
                    "projects:",
                    "  obs-auto-moc:",
                    "    roots:",
                    "      - /work/custom-claw-tools/obs-auto-moc",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        payload = self.payload(
            session_id="sid-project-fallback",
            scope="session_end",
            turns=1,
            files=["src/main.py"],
            prompts=["implement importer"],
            ended_at="2026-05-24T11:00:00+00:00",
        )
        payload["cwd"] = "/tmp/unmatched/worktree"
        payload["repo"] = "/work/custom-claw-tools/obs-auto-moc"

        decision = ingest_queue_item(self.write_queue_item("project-fallback", payload), memory_root=self.root)

        inbox = self.root / "inbox" / "sessions" / "copilot-cli" / "2026-05-24" / "sid-project-fallback.md"
        self.assertEqual(decision["project"], "obs-auto-moc")
        self.assertTrue(inbox.exists())
        self.assertIn("project: obs-auto-moc", inbox.read_text(encoding="utf-8"))

    def test_pipeline_uses_raw_remote_url_for_project_resolution_when_repo_path_does_not_match(self):
        config_dir = self.root.parent / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "projects.yaml").write_text(
            "\n".join(
                [
                    "version: 1",
                    "projects:",
                    "  paulshaclaw:",
                    "    remotes:",
                    "      - github.com/hamanpaul/paulshaclaw",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        payload = self.payload(
            session_id="sid-project-remote",
            scope="session_end",
            turns=1,
            files=["src/main.py"],
            prompts=["implement importer"],
            ended_at="2026-05-24T11:10:00+00:00",
        )
        payload["cwd"] = "/worktrees/unmatched"
        payload["repo"] = "/work/custom-claw-tools/unmatched"
        payload["remote_url"] = "https://GitHub.com/hamanpaul/paulshaclaw.git/"

        decision = ingest_queue_item(self.write_queue_item("project-remote", payload), memory_root=self.root)

        inbox = self.root / "inbox" / "sessions" / "copilot-cli" / "2026-05-24" / "sid-project-remote.md"
        rendered = inbox.read_text(encoding="utf-8")
        self.assertEqual(decision["project"], "paulshaclaw")
        self.assertTrue(inbox.exists())
        self.assertIn("project: paulshaclaw", rendered)
        self.assertIn("  repo: _unknown", rendered)
        self.assertNotIn("  repo: /work/custom-claw-tools/unmatched", rendered)

    def test_reclassification_updates_when_bucket_changes_without_content_hash_change(self):
        payload = self.payload(
            session_id="sid-hash-bucket",
            scope="session_end",
            turns=2,
            files=["src/main.py"],
            prompts=["implement importer"],
            ended_at="2026-05-24T11:30:00+00:00",
        )

        first = ingest_queue_item(
            self.write_queue_item_at("runtime/queue/hash-bucket-first.json", payload),
            memory_root=self.root,
        )
        second = ingest_queue_item(
            self.write_queue_item_at("docs/superpowers/plans/hash-bucket-second.json", payload),
            memory_root=self.root,
        )

        sessions_path = self.root / "inbox" / "sessions" / "copilot-cli" / "2026-05-24" / "sid-hash-bucket.md"
        plans_path = self.root / "inbox" / "plans" / "copilot-cli" / "2026-05-24" / "sid-hash-bucket.md"
        self.assertEqual(first["status"], "written")
        self.assertEqual(first["classifier_bucket"], "sessions")
        self.assertEqual(second["status"], "updated")
        self.assertEqual(second["classifier_bucket"], "plans")
        self.assertFalse(sessions_path.exists())
        self.assertTrue(plans_path.exists())

    def test_second_same_session_ingest_waits_for_lock_and_finishes_normally(self):
        import fcntl

        payload = self.payload()
        first_queue = self.write_queue_item("first", payload)
        retry_queue = self.write_queue_item("retry", payload)
        first_holding_lock = Event()
        second_attempted_lock = Event()
        release_first = Event()
        lock_path = pipeline.shard_lock_path(self.root, "copilot-cli:sid-001")
        real_archive_queue = pipeline._archive_queue
        real_flock = pipeline.fcntl.flock

        def unlocked_preview(queue_item, *, memory_root):
            return pipeline._preview_queue_item_unlocked(queue_item, memory_root=memory_root)

        def blocking_archive(queue_path, archive_path):
            if Path(queue_path) == first_queue and not first_holding_lock.is_set():
                first_holding_lock.set()
                self.assertTrue(release_first.wait(timeout=2))
            return real_archive_queue(queue_path, archive_path)

        def instrumented_flock(lock_handle, operation):
            if Path(lock_handle.name) == lock_path and operation & fcntl.LOCK_EX and first_holding_lock.is_set():
                second_attempted_lock.set()
            return real_flock(lock_handle, operation)

        with mock.patch("paulsha_hippo.importer.pipeline.preview_queue_item", side_effect=unlocked_preview):
            with mock.patch("paulsha_hippo.importer.pipeline._archive_queue", side_effect=blocking_archive):
                with mock.patch("paulsha_hippo.importer.pipeline.fcntl.flock", side_effect=instrumented_flock):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        first_future = executor.submit(ingest_queue_item, first_queue, memory_root=self.root)
                        self.assertTrue(first_holding_lock.wait(timeout=2))
                        retry_future = executor.submit(ingest_queue_item, retry_queue, memory_root=self.root)
                        self.assertTrue(second_attempted_lock.wait(timeout=2))
                        self.assertFalse(self.wait_until(retry_future.done, timeout=0.2))
                        release_first.set()
                        first_decision = first_future.result(timeout=2)
                        retry_decision = retry_future.result(timeout=2)

        self.assertEqual(first_decision["status"], "written")
        self.assertEqual(retry_decision["status"], "hash-duplicate")
        self.assertFalse(first_queue.exists())
        self.assertFalse(retry_queue.exists())
        self.assertTrue(Path(first_decision["archive_path"]).exists())
        self.assertTrue(Path(retry_decision["archive_path"]).exists())
        self.assertEqual([entry["status"] for entry in self.ledger_entries()], ["written", "hash-duplicate"])

    def test_queue_item_remains_retryable_when_ledger_append_fails_after_archive(self):
        payload = self.payload(session_id="sid-ledger-fail")
        queue_item = self.write_queue_item("ledger_fail", payload)
        archive = self.root / "archive" / "queue" / "2026-05" / f"copilot-cli__sid-ledger-fail--written--{self.expected_hash(payload)[:12]}.json"

        with mock.patch("paulsha_hippo.importer.pipeline._append_ledger", side_effect=OSError("ledger full")):
            with self.assertRaisesRegex(OSError, "ledger full"):
                ingest_queue_item(queue_item, memory_root=self.root)

        self.assertTrue(queue_item.exists())
        self.assertTrue(archive.exists())
        self.assertEqual(self.ledger_entries(), [])

    def test_archive_failure_does_not_commit_written_ledger_and_retry_succeeds(self):
        payload = self.payload(session_id="sid-archive-fail")
        failing_queue = self.write_queue_item("archive_fail", payload)
        inbox = self.root / "inbox" / "sessions" / "copilot-cli" / "2026-05-24" / "sid-archive-fail.md"

        with mock.patch("paulsha_hippo.importer.pipeline._archive_queue", side_effect=OSError("archive full")):
            with self.assertRaisesRegex(OSError, "archive full"):
                ingest_queue_item(failing_queue, memory_root=self.root)

        self.assertEqual(
            [entry["status"] for entry in self.ledger_entries() if entry["idempotency_key"] == "copilot-cli:sid-archive-fail"],
            [],
        )
        self.assertTrue(failing_queue.exists())
        self.assertFalse(inbox.exists())

        retry_decision = ingest_queue_item(failing_queue, memory_root=self.root)

        self.assertEqual(retry_decision["status"], "written")
        self.assertFalse(failing_queue.exists())
        self.assertTrue(Path(retry_decision["archive_path"]).exists())
        self.assertEqual(
            [entry["status"] for entry in self.ledger_entries() if entry["idempotency_key"] == "copilot-cli:sid-archive-fail"],
            ["written"],
        )

    def test_concurrent_distinct_sessions_write_valid_complete_ledger_jsonl(self):
        import time

        queue_items = [
            self.write_queue_item(f"concurrent-{index}", self.payload(session_id=f"sid-concurrent-{index:03d}", turns=index + 1))
            for index in range(24)
        ]

        def slow_split_append(memory_root, entry):
            ledger = memory_root / "runtime" / "ledger" / "import.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
            midpoint = len(line) // 2
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(line[:midpoint])
                handle.flush()
                time.sleep(0.005)
                handle.write(line[midpoint:])
                handle.flush()

        with mock.patch("paulsha_hippo.importer.pipeline._append_ledger", side_effect=slow_split_append):
            with ThreadPoolExecutor(max_workers=8) as executor:
                decisions = list(executor.map(lambda path: ingest_queue_item(path, memory_root=self.root), queue_items))

        self.assertEqual([decision["status"] for decision in decisions], ["written"] * len(queue_items))
        ledger = self.root / "runtime" / "ledger" / "import.jsonl"
        lines = ledger.read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(line) for line in lines]
        self.assertEqual(len(parsed), len(queue_items))
        self.assertEqual({entry["status"] for entry in parsed}, {"written"})
        self.assertEqual(
            {entry["idempotency_key"] for entry in parsed},
            {f"copilot-cli:sid-concurrent-{index:03d}" for index in range(24)},
        )

    def test_duplicate_and_stale_queue_items_are_archived_with_unique_names(self):
        base = self.payload(session_id="sid-archive-all", scope="turn", turns=1, files=["a.py"], prompts=["one"])
        richer = self.payload(session_id="sid-archive-all", scope="turn", turns=2, files=["a.py", "b.py"], prompts=["one", "two"])
        stale = self.payload(
            session_id="sid-archive-all",
            scope="turn",
            turns=1,
            files=["a.py"],
            prompts=["one"],
            ended_at="2026-05-24T10:05:00+00:00",
        )
        queue_items = [
            self.write_queue_item("archive-all-base", base),
            self.write_queue_item("archive-all-duplicate-1", base),
            self.write_queue_item("archive-all-duplicate-2", base),
            self.write_queue_item("archive-all-richer", richer),
            self.write_queue_item("archive-all-stale", stale),
        ]

        decisions = [ingest_queue_item(queue_item, memory_root=self.root) for queue_item in queue_items]

        self.assertEqual(
            [decision["status"] for decision in decisions],
            ["written", "hash-duplicate", "hash-duplicate", "updated", "stale-skip"],
        )
        self.assertFalse(any(queue_item.exists() for queue_item in queue_items))
        archive_paths = [Path(decision["archive_path"]) for decision in decisions]
        self.assertEqual(len(set(archive_paths)), len(archive_paths))
        for decision, archive_path in zip(decisions, archive_paths):
            self.assertTrue(archive_path.exists())
            self.assertIn(f"--{decision['status']}--{decision['content_hash'][:12]}", archive_path.name)


if __name__ == "__main__":
    unittest.main()
