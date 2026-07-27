from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.janitor import config as janitor_config
from paulsha_hippo.janitor import scanner
from paulsha_hippo.ledger import retrieval_set


def _write_record(path: Path, slice_id: str, source_session: str) -> None:
    path.write_text(
        "---\n"
        "memory_layer: knowledge\n"
        f"slice_id: {slice_id}\n"
        "project: paulshaclaw\n"
        "source_agent: claude\n"
        f"source_session: {source_session}\n"
        "source_artifact: a.md\n"
        'captured_at: "2020-01-01T00:00:00Z"\n'
        "provenance:\n"
        "  repo: paulshaclaw\n"
        "  commit: c\n"
        "  path: docs/x.md\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )


class JanitorLedgerToleranceTests(unittest.TestCase):
    def test_mixed_import_jsonl_keeps_good_reactivation_lines(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir(parents=True)
            _write_record(knowledge_root / "sl-a.md", "sl-a", "sess-a")
            _write_record(knowledge_root / "sl-b.md", "sl-b", "sess-b")

            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            kwargs = dict(
                knowledge_root=knowledge_root,
                config=cfg,
                config_hash=cfg_hash,
                source_path_exists=lambda record: True,
            )

            scanner.run_scan(root, now="2026-05-31T00:00:00Z", **kwargs)

            import_path = root / "runtime" / "ledger" / "import.jsonl"
            import_path.parent.mkdir(parents=True, exist_ok=True)
            import_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "idempotency_key": "claude:sess-a",
                                "status": "updated",
                                "recorded_at": "2026-06-01T00:00:00Z",
                            }
                        ),
                        "",
                        "{bad json}",
                        json.dumps(
                            {
                                "idempotency_key": "claude:sess-b",
                                "status": "written",
                                "recorded_at": "2026-06-01T00:00:00Z",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = scanner.run_scan(root, now="2026-06-02T00:00:00Z", **kwargs)

            self.assertEqual(result["summary"]["reactivated"], 2)
            self.assertIn("skipped 2 bad line(s)", " ".join(result["warnings"]))
            self.assertEqual(
                retrieval_set.active_records(root, ["sl-a", "sl-b"]),
                ["sl-a", "sl-b"],
            )


class JanitorDirectReadWarningTests(unittest.TestCase):
    """Issue #67: un-offered reads must not pin ``hippo dream run`` at ``partial``.

    ``run_scan`` surfaces any non-zero usage diagnostic as a warning, and dream
    downgrades a run to ``partial`` whenever ``warnings_total > 0``. A direct
    read is normal agent behaviour, so it must not reach that warning at all —
    only genuine ledger defects may.
    """

    def _scan_with_reads(self, reads: list[dict], offers: list[dict] | None = None) -> dict:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir(parents=True)
            _write_record(knowledge_root / "sl-a.md", "sl-a", "sess-a")
            led = root / "runtime" / "ledger"
            led.mkdir(parents=True, exist_ok=True)
            (led / "offered.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in (offers or [])), encoding="utf-8"
            )
            (led / "memory_usage.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in reads), encoding="utf-8"
            )
            cfg, cfg_hash = janitor_config.load_config(override_path=None)
            return scanner.run_scan(
                root,
                now="2026-07-27T00:00:00Z",
                knowledge_root=knowledge_root,
                config=cfg,
                config_hash=cfg_hash,
                source_path_exists=lambda record: True,
            )

    def test_direct_reads_emit_no_usage_diagnostics_warning(self):
        result = self._scan_with_reads([
            {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a",
             "source": "read", "ts": "2026-07-26T00:00:00Z"},
        ])

        self.assertEqual(
            [w for w in result["warnings"] if "usage ledger diagnostics" in w], []
        )

    def test_genuine_ledger_defects_still_warn(self):
        result = self._scan_with_reads([
            {"tool": "claude-code", "session_id": "s1", "sl_id": "sl-a",
             "source": "read", "ts": "not-a-timestamp"},
        ])

        self.assertTrue(
            any("usage ledger diagnostics" in w and "invalid_ts" in w
                for w in result["warnings"]),
            result["warnings"],
        )


if __name__ == "__main__":
    unittest.main()
