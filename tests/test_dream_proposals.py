import unittest
import tempfile
from pathlib import Path

from paulsha_hippo.dream import proposals


class DreamProposalsTest(unittest.TestCase):
    def test_append_and_pending(self):
        # create two proposals in non-sorted order and verify pending() is deterministic
        with tempfile.TemporaryDirectory() as tmp:
            p_b = proposals.Proposal(
                proposal_id="b",
                kind="merge",
                status="pending",
                created_ts="2026-06-02T00:00:00Z",
                subject_slice_ids=["s1"],
                detail={"x": 1},
                source="tests",
                config_hash="abc123",
            )
            p_a = proposals.Proposal(
                proposal_id="a",
                kind="merge",
                status="pending",
                created_ts="2026-06-02T00:00:01Z",
                subject_slice_ids=["s2"],
                detail={"y": 2},
                source="tests",
                config_hash="def456",
            )
            # append in order b then a
            ret_b = proposals.append(Path(tmp), p_b)
            ret_a = proposals.append(Path(tmp), p_a)
            self.assertIsNone(ret_b)
            self.assertIsNone(ret_a)

            pend = proposals.pending(Path(tmp))
            # expect deterministic sorted order by filename (a.json then b.json)
            self.assertEqual(len(pend), 2)
            self.assertEqual(pend[0]["proposal_id"], "a")
            self.assertEqual(pend[1]["proposal_id"], "b")

    def test_requires_approval_for_canonical_kinds(self):
        self.assertTrue(proposals.requires_approval("merge"))
        self.assertTrue(proposals.requires_approval("supersede"))
        self.assertTrue(proposals.requires_approval("contradiction"))
        self.assertFalse(proposals.requires_approval("decay"))
        self.assertTrue(
            proposals.requires_approval("decay", decay_requires_approval=True)
        )


if __name__ == "__main__":
    unittest.main()
