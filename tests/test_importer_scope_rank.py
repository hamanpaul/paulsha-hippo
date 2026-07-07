import unittest
from paulsha_hippo.importer.pipeline import _SCOPE_RANK


class ScopeRankTest(unittest.TestCase):
    def test_pre_compact_is_turn_level(self):
        self.assertEqual(_SCOPE_RANK["pre_compact"], 0)
        self.assertGreater(_SCOPE_RANK["session_end"], _SCOPE_RANK["pre_compact"])
        self.assertGreater(_SCOPE_RANK["watcher_final"], _SCOPE_RANK["pre_compact"])


if __name__ == "__main__":
    unittest.main()
