"""
Test suite for ledger/relations.py — derivation graph relations ledger.

Task 2: Stage 2 T3 atomizer/linker relations ledger.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo.ledger import relations


class TestRelationsLedger(unittest.TestCase):
    """Test relations ledger for derivation graph."""

    def test_append_and_neighbors(self):
        """Test append edge and retrieve neighbors."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now_str = "2025-01-15T10:00:00Z"
            config_hash = "abc123"

            # Append edge: distilled_from slice:sl-1 → session:claude:s1
            relations.append_edge(
                root,
                type="distilled_from",
                frm="slice:sl-1",
                to="session:claude:s1",
                now=now_str,
                config_hash=config_hash,
            )

            # Retrieve neighbors of session:claude:s1
            edges = relations.neighbors(root, "session:claude:s1")

            self.assertEqual(len(edges), 1)
            edge = edges[0]
            self.assertEqual(edge["type"], "distilled_from")
            self.assertEqual(edge["from"], "slice:sl-1")
            self.assertEqual(edge["to"], "session:claude:s1")
            self.assertEqual(edge["ts"], now_str)
            self.assertEqual(edge["atomizer_config_hash"], config_hash)

    def test_neighbors_matches_from_or_to(self):
        """Test neighbors matches both endpoint directions."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now_str = "2025-01-15T11:00:00Z"
            config_hash = "def456"

            # Append edge: fragment_of fragment:f0 → session:claude:s1
            relations.append_edge(
                root,
                type="fragment_of",
                frm="fragment:f0",
                to="session:claude:s1",
                now=now_str,
                config_hash=config_hash,
            )

            # Check neighbors from both directions
            edges_for_session = relations.neighbors(root, "session:claude:s1")
            edges_for_fragment = relations.neighbors(root, "fragment:f0")

            # Both should return the same edge
            self.assertEqual(len(edges_for_session), 1)
            self.assertEqual(len(edges_for_fragment), 1)
            self.assertEqual(edges_for_session[0]["type"], "fragment_of")
            self.assertEqual(edges_for_fragment[0]["type"], "fragment_of")

    def test_neighbors_dedups_identical_edges(self):
        """Test neighbors deduplicates identical edges."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now_str = "2025-01-15T12:00:00Z"
            config_hash = "ghi789"

            # Append the same edge twice
            relations.append_edge(
                root,
                type="promoted_to",
                frm="fragment:f1",
                to="atom:a1",
                now=now_str,
                config_hash=config_hash,
            )
            relations.append_edge(
                root,
                type="promoted_to",
                frm="fragment:f1",
                to="atom:a1",
                now=now_str,
                config_hash=config_hash,
            )

            # Should deduplicate to one edge
            edges = relations.neighbors(root, "atom:a1")
            self.assertEqual(len(edges), 1)
            self.assertEqual(len(relations.read_edges(root)), 1)

    def test_ts_uses_injected_now(self):
        """Test edge ts field equals provided now string."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now_str = "2025-01-15T13:00:00Z"
            config_hash = "jkl012"

            relations.append_edge(
                root,
                type="supersedes",
                frm="atom:a1",
                to="atom:a0",
                now=now_str,
                config_hash=config_hash,
            )

            edges = relations.read_edges(root)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0]["ts"], now_str)

    def test_semantic_edge_types_are_valid(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            now_str = "2025-01-15T13:00:00Z"
            config_hash = "mno345"

            relations.append_edge(
                root,
                type="relates_to",
                frm="slice:sl-1",
                to="slice:sl-2",
                now=now_str,
                config_hash=config_hash,
            )
            relations.append_edge(
                root,
                type="mentions",
                frm="slice:sl-1",
                to="entity:MTK",
                now=now_str,
                config_hash=config_hash,
            )

            edges = relations.read_edges(root)
            self.assertEqual(
                [edge["type"] for edge in edges],
                ["relates_to", "mentions"],
            )

    def test_corrupt_line_fails_closed(self):
        """Test malformed JSON line raises RelationsLedgerError."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ledger_path = relations.relations_path(root)
            ledger_path.parent.mkdir(parents=True, exist_ok=True)

            # Write valid line then corrupt line
            with open(ledger_path, "w") as f:
                f.write('{"type":"fragment_of","from":"f1","to":"s1","ts":"2025-01-15T00:00:00Z","atomizer_config_hash":"abc"}\n')
                f.write('NOT VALID JSON\n')

            # Should raise RelationsLedgerError
            with self.assertRaises(relations.RelationsLedgerError) as ctx:
                relations.read_edges(root)

            # Error message should include line number
            self.assertIn("line 2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
