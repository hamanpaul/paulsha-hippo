from __future__ import annotations

import unittest

from paulsha_hippo.atomizer import promoter
from paulsha_hippo.atomizer.config import AtomizerConfig
from paulsha_hippo.atomizer.splitter import Fragment

CFG = AtomizerConfig(
    schema_version="1",
    boundary_patterns=(r"^#{1,6}\s",),
    max_fragment_chars=8000,
    artifact_kind_map={"research": "research"},
    phase_map={"research": "research"},
    default_artifact_kind="report",
    default_phase="review",
)


def _frag(index: int) -> Fragment:
    return Fragment(
        project="paulshaclaw",
        source_agent="claude",
        source_session="s1",
        source_artifact="research",
        captured_at="2026-06-02T00:00:00Z",
        provenance={"repo": "r", "commit": "c", "path": "p"},
        fragment_index=index,
        body=f"b{index}",
    )


class IdentityPromoterTests(unittest.TestCase):
    def test_one_slice_per_fragment(self):
        slices = promoter.IdentityPromoter().promote([_frag(0), _frag(1)], CFG)
        self.assertEqual(len(slices), 2)

    def test_single_fragment_input_still_promotes_one_slice(self):
        slices = promoter.IdentityPromoter().promote(_frag(0), CFG)
        self.assertEqual(len(slices), 1)

    def test_empty_relations(self):
        promoted = promoter.IdentityPromoter().promote([_frag(0)], CFG)
        self.assertEqual(promoted[0].relations, ())

    def test_promoted_slice_keeps_existing_derivation_contract(self):
        promoted = promoter.IdentityPromoter().promote([_frag(0)], CFG)
        self.assertEqual(promoted[0].frontmatter["distilled_from"], "claude:s1")
        self.assertEqual(promoted[0].frontmatter["fragment_ref"], "claude__s1__000")


if __name__ == "__main__":
    unittest.main()
