from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
    """寫入 pipeline `_read_fragment` 真的讀得動、且 frontmatter 屬於該 session
    的 fragment（含 project / source_agent / source_session；沿用 pipeline 契約）。"""
    agent, _, session = session_key.partition(":")
    frag = root / "inbox" / "_slices" / "proj" / f"{agent}__{session}__000.md"
    frag.parent.mkdir(parents=True, exist_ok=True)
    frag.write_text(
        "---\n"
        "memory_layer: inbox\n"
        "project: proj\n"
        f"source_agent: {agent}\n"
        f"source_session: {session}\n"
        "fragment_index: 0\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )


def _seed_fragment_bad_index(root: Path, session_key: str, *, index_value: str) -> None:
    """frontmatter 前段合法（project／source_agent／source_session 齊備、路徑安全）
    但 fragment_index 型別壞掉——pipeline `_read_fragment` 會一路讀到 `int(...)` 才
    raise（null → TypeError、非純量如 list → TypeError、非數字字串 → ValueError）。
    模擬 B2 gate 必須攔下的『讀到一半才炸』壞檔（`index_value` 直接接在 key 冒號後，
    如 ``""`` 產生 null、``" [1]"`` 產生 list、``" nope"`` 產生非數字字串）。"""
    agent, _, session = session_key.partition(":")
    frag = root / "inbox" / "_slices" / "proj" / f"{agent}__{session}__000.md"
    frag.parent.mkdir(parents=True, exist_ok=True)
    frag.write_text(
        "---\n"
        "memory_layer: inbox\n"
        "project: proj\n"
        f"source_agent: {agent}\n"
        f"source_session: {session}\n"
        f"fragment_index:{index_value}\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )


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
            _seed_fragment(root, "claude:p1")
            _seed_fragment(root, "codex:p2")
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

    def test_requeue_zero_fragment_parked_session_stays_parked(self):
        # Codex 複驗 B2：zero-fragment 的 parked session 一旦送回 split，
        # pipeline 永遠掃不到 fragment，session 永久卡非終態——gate 必須在
        # append_state「之前」擋下，維持 parked。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "no-valid-fragments"}],
            )
            # ledger 不得出現任何 split 事件（gate 在提交前）
            self.assertEqual(
                [e for e in processing.read_events(root) if e["state"] == "split"], []
            )

    def test_requeue_ignores_fragments_of_other_sessions(self):
        # 「屬於該 session」：別的 session 的 fragment 不得放行 gate
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            _seed_fragment(root, "claude:other")

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "no-valid-fragments"}],
            )

    def test_requeue_unreadable_fragment_counts_as_missing(self):
        # 「可讀」：glob 命中但讀不了（以同名目錄模擬）不算有效 fragment
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            bogus = root / "inbox" / "_slices" / "proj" / "claude__s1__000.md"
            bogus.mkdir(parents=True)

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "no-valid-fragments"}],
            )

    def test_requeue_gated_when_fragment_belongs_to_other_session(self):
        # 檔名對得上（claude__s1__000.md）但 frontmatter 的 source_session 指向
        # 別的 session——內容不屬於本 session。早前 gate 只 glob 檔名＋讀 1 char
        # 會誤放行，把別 session 的內容錯 promote／卡非終態；必須擋下。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            frag = root / "inbox" / "_slices" / "proj" / "claude__s1__000.md"
            frag.parent.mkdir(parents=True, exist_ok=True)
            frag.write_text(
                "---\nmemory_layer: inbox\nproject: proj\n"
                "source_agent: claude\nsource_session: other\n"
                "fragment_index: 0\n---\nbody\n",
                encoding="utf-8",
            )

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "no-valid-fragments"}],
            )

    def test_requeue_gated_when_fragment_frontmatter_unreadable(self):
        # 檔名對得上但 frontmatter 缺 pipeline `_read_fragment` 必要欄位（只有
        # fragment_index，無 project／source_session）——pipeline 讀不出；送回
        # split 只會每輪警告、永久卡非終態。gate 必須擋下（早前讀 1 char 會誤放）。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            frag = root / "inbox" / "_slices" / "proj" / "claude__s1__000.md"
            frag.parent.mkdir(parents=True, exist_ok=True)
            frag.write_text("---\nfragment_index: 0\n---\nbody\n", encoding="utf-8")

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "no-valid-fragments"}],
            )

    def test_requeue_gated_when_fragment_index_is_null(self):
        # 檔名對得上、frontmatter 前段合法（project／source_session 齊備）但
        # fragment_index 為 null——pipeline `_read_fragment` 讀到 `int(None)` 拋
        # TypeError。gate 必須把它與『讀不了』一視同仁不計，落入 no-valid-fragments
        # skip、維持 parked，而非讓 TypeError 逃出整個 requeue()。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            _seed_fragment_bad_index(root, "claude:s1", index_value="")

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "no-valid-fragments"}],
            )
            # gate 在 append_state 之前擋下：ledger 不得出現任何 split 事件
            self.assertEqual(
                [e for e in processing.read_events(root) if e["state"] == "split"], []
            )

    def test_requeue_gated_when_fragment_index_is_non_scalar(self):
        # fragment_index 為非純量（list）——`int([1])` 亦拋 TypeError；同樣不得逃出。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            _seed_fragment_bad_index(root, "claude:s1", index_value=" [1, 2]")

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "no-valid-fragments"}],
            )

    def test_requeue_all_parked_gates_only_zero_fragment_sessions(self):
        # 混合情境：有 fragment 的照常 requeue，zero-fragment 的維持 parked
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:p1", category="transient")
            _seed_fragment(root, "claude:p1")
            _park(root, "codex:p2", category="backend_unavailable")

            summary = requeue.requeue(root, all_parked=True, now="2026-07-10T01:00:00Z")

            self.assertEqual(
                [entry["session_key"] for entry in summary["requeued"]],
                ["claude:p1"],
            )
            self.assertEqual(processing.state_of(root, "claude:p1"), "split")
            self.assertEqual(processing.state_of(root, "codex:p2"), "parked")
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "codex:p2", "reason": "no-valid-fragments"}],
            )

    def test_requeue_all_parked_type_broken_fragment_does_not_block_healthy(self):
        # 回歸：型別壞掉的 fragment（fragment_index=null，`int(None)` 拋 TypeError）
        # 不得讓整批 requeue 未捕捉地 crash 而連坐正常 session。刻意讓壞檔 session
        # 排序在前（`claude:bad` < `claude:good`）——修法前 `claude:bad` 先被處理即
        # 整批炸掉，`claude:good` 連本可正常 requeue 都被迫留在 parked。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:bad", category="invalid_output")
            _seed_fragment_bad_index(root, "claude:bad", index_value="")
            _park(root, "claude:good", category="transient")
            _seed_fragment(root, "claude:good")

            summary = requeue.requeue(root, all_parked=True, now="2026-07-10T01:00:00Z")

            # 正常 session 仍被 requeue 回 split；壞檔 session 被 gate 擋下維持 parked
            self.assertEqual(
                [entry["session_key"] for entry in summary["requeued"]],
                ["claude:good"],
            )
            self.assertEqual(processing.state_of(root, "claude:good"), "split")
            self.assertEqual(processing.state_of(root, "claude:bad"), "parked")
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:bad", "reason": "no-valid-fragments"}],
            )


class RequeueAtomicTransitionTests(unittest.TestCase):
    """parked→split 的 read-check-write 必須原子：快照後被併發 writer 改狀態、或帶
    較舊 `--now`，都不得錯誤復活已 promote 的 session，也不得回報未生效的 split。"""

    def test_concurrent_promote_after_snapshot_is_refused_not_resurrected(self):
        # 併發回歸：requeue 取得快照時 s1 仍 parked，但在轉移前另一 writer 已把 s1
        # promote（較 requeue `--now` 早的 ts）。修法前 requeue 會以較晚的 now append
        # split，在 fold 中勝出 → 已 promote 的 session 被錯誤復活成 split。原子轉移
        # 以持鎖重讀為準：偵測 promoted、拒絕、維持 promoted。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            _seed_fragment(root, "claude:s1")

            def concurrent_promote() -> None:
                processing.append_state(
                    root, session_key="claude:s1", state="promoted",
                    now="2026-07-10T00:30:00Z", config_hash="cfg-hash",
                )

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-10T01:00:00Z",
                reason="backend fixed", _after_snapshot=concurrent_promote,
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "promoted")
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "promoted"}],
            )
            # 未生效的 split 不得落 ledger（拒絕在寫入之前）
            self.assertEqual(
                [e for e in processing.read_events(root) if e["state"] == "split"], []
            )

    def test_stale_now_does_not_false_succeed(self):
        # 較舊 `--now` 回歸：parked 事件 ts=2026-07-10T00:00:00Z，requeue 帶更早的
        # now。修法前 requeue 會 append 一筆較舊 ts 的 split——它在 fold 中輸給既有
        # parked，狀態實際仍是 parked，但 requeue 卻回報 requeued（false-success）。
        # 原子轉移拒絕 stale ts：維持 parked、計入 skipped、不寫入。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            _seed_fragment(root, "claude:s1")

            summary = requeue.requeue(
                root, session_key="claude:s1", now="2026-07-09T00:00:00Z",
                reason="backend fixed",
            )

            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:s1", "reason": "stale-timestamp"}],
            )
            self.assertEqual(
                [e for e in processing.read_events(root) if e["state"] == "split"], []
            )

    def test_all_parked_concurrent_promote_does_not_block_healthy(self):
        # --all-parked：併發被 promote 的 session 拒絕（維持 promoted），不連坐其他
        # 本可正常 requeue 的健康 session。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:raced", category="transient")
            _seed_fragment(root, "claude:raced")
            _park(root, "claude:healthy", category="transient")
            _seed_fragment(root, "claude:healthy")

            def concurrent_promote() -> None:
                processing.append_state(
                    root, session_key="claude:raced", state="promoted",
                    now="2026-07-10T00:30:00Z", config_hash="cfg-hash",
                )

            summary = requeue.requeue(
                root, all_parked=True, now="2026-07-10T01:00:00Z",
                _after_snapshot=concurrent_promote,
            )

            self.assertEqual(
                [entry["session_key"] for entry in summary["requeued"]],
                ["claude:healthy"],
            )
            self.assertEqual(processing.state_of(root, "claude:healthy"), "split")
            self.assertEqual(processing.state_of(root, "claude:raced"), "promoted")
            self.assertEqual(
                summary["skipped"],
                [{"session_key": "claude:raced", "reason": "promoted"}],
            )


class RequeueCliTests(unittest.TestCase):
    def _run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_cli_requeue_single(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            _seed_fragment(root, "claude:s1")
            rc, out, _ = self._run_cli(
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
            rc, _, _ = self._run_cli(["requeue", "--memory-root", str(root)])
            self.assertEqual(rc, 2)
            rc2, _, _ = self._run_cli(
                ["requeue", "claude:s1", "--all-parked", "--memory-root", str(root)]
            )
            self.assertEqual(rc2, 2)

    def test_cli_exit_1_when_target_not_requeued(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out, _ = self._run_cli(
                ["requeue", "claude:ghost", "--memory-root", str(root)]
            )
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(out)["requeued"], [])

    def test_cli_all_parked_with_zero_parked_is_ok(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out, _ = self._run_cli(
                ["requeue", "--all-parked", "--memory-root", str(root)]
            )
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"requeued": [], "skipped": []})

    def test_cli_zero_fragment_requeue_exits_nonzero_and_explains(self):
        # Codex 複驗 B2 回歸：零 fragment 的 parked session requeue →
        # 仍 parked + 非零 exit + stderr 說明（早前回 exit 0 誤報成功）。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:s1")
            rc, out, err = self._run_cli(
                ["requeue", "claude:s1", "--memory-root", str(root),
                 "--now", "2026-07-10T01:00:00Z"]
            )
            self.assertEqual(rc, 1)
            self.assertEqual(processing.state_of(root, "claude:s1"), "parked")
            payload = json.loads(out)
            self.assertEqual(payload["requeued"], [])
            self.assertEqual(payload["skipped"][0]["reason"], "no-valid-fragments")
            self.assertIn("claude:s1", err)
            self.assertIn("fragment", err)
            self.assertIn("parked", err)

    def test_cli_all_parked_partial_no_fragments_still_nonzero(self):
        # --all-parked 下只要有 zero-fragment 項被擋，整體就必須非零 exit，
        # 不得被其他成功項掩蓋。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _park(root, "claude:p1", category="transient")
            _seed_fragment(root, "claude:p1")
            _park(root, "codex:p2", category="backend_unavailable")
            rc, out, err = self._run_cli(
                ["requeue", "--all-parked", "--memory-root", str(root),
                 "--now", "2026-07-10T01:00:00Z"]
            )
            self.assertEqual(rc, 1)
            self.assertEqual(processing.state_of(root, "claude:p1"), "split")
            self.assertEqual(processing.state_of(root, "codex:p2"), "parked")
            payload = json.loads(out)
            self.assertEqual(
                [entry["session_key"] for entry in payload["requeued"]],
                ["claude:p1"],
            )
            self.assertIn("codex:p2", err)


if __name__ == "__main__":
    unittest.main()
