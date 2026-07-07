"""Tests for atomizer splitter module."""
import unittest
from paulsha_hippo.atomizer.config import AtomizerConfig
from paulsha_hippo.atomizer.splitter import split


class TestAtomizerSplitter(unittest.TestCase):
    """Test atomizer splitter functionality."""
    
    def _make_config(self, max_fragment_chars=8000):
        """Create test config with specified max_fragment_chars."""
        return AtomizerConfig(
            schema_version="1",
            boundary_patterns=(r"^#\s+\w+",),
            max_fragment_chars=max_fragment_chars,
            artifact_kind_map={},
            phase_map={},
            default_artifact_kind="report",
            default_phase="review"
        )
    
    def test_splits_on_heading_boundary(self):
        """Body with two headings splits into 2 fragments."""
        body = "# A\nalpha\n# B\nbeta\n"
        config = self._make_config()
        
        fragments = split(body, config)
        
        self.assertEqual(len(fragments), 2)
        self.assertIn("alpha", fragments[0])
        self.assertIn("beta", fragments[1])
    
    def test_empty_body_yields_zero_fragments(self):
        """Empty string and whitespace-only string yield empty list."""
        config = self._make_config()
        
        self.assertEqual(split("", config), [])
        self.assertEqual(split("   \n\t  \n", config), [])
    
    def test_no_boundary_returns_single_fragment(self):
        """Non-heading body yields one fragment containing the text."""
        body = "just plain text\nno headings here\n"
        config = self._make_config()
        
        fragments = split(body, config)
        
        self.assertEqual(len(fragments), 1)
        self.assertIn("just plain text", fragments[0])
        self.assertIn("no headings here", fragments[0])
    
    def test_oversize_fragment_is_split_by_max_chars(self):
        """Fragment exceeding max_fragment_chars is split."""
        heading = "# Test\n"
        body = heading + ("x" * 35)
        config = self._make_config(max_fragment_chars=10)
        
        fragments = split(body, config)
        
        # Should produce more than one fragment
        self.assertGreater(len(fragments), 1)
        # Every fragment should be <= 10 chars
        for frag in fragments:
            self.assertLessEqual(len(frag), 10)

    def test_heading_preserved_when_content_exceeds_max(self):
        """Heading is preserved with each chunk when section content is oversized."""
        body = "# Section\n" + ("x" * 50)
        config = self._make_config(max_fragment_chars=20)

        fragments = split(body, config)

        self.assertGreater(len(fragments), 1)
        for fragment in fragments:
            self.assertLessEqual(len(fragment), 20)
            self.assertIn("# Section", fragment)
    
    def test_deterministic(self):
        """Repeated split on same body/config returns equal lists."""
        body = "# A\nalpha\n# B\nbeta\n"
        config = self._make_config()
        
        result1 = split(body, config)
        result2 = split(body, config)
        
        self.assertEqual(result1, result2)


if __name__ == "__main__":
    unittest.main()
