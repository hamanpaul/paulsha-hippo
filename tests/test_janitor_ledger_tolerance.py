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


if __name__ == "__main__":
    unittest.main()
