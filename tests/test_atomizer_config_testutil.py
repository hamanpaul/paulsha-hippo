"""Unit coverage for the ``isolated_atomizer_config`` test helper itself
(paulsha-hippo#141): it must never resolve ``paths.atomizer_config_path()``
to the operator's real ``~/.config/paulsha-hippo/config.yaml``, and it must
restore ``HIPPO_CONFIG_ROOT`` on exit even when the block raises.
"""
from __future__ import annotations

import os
import unittest

from paulsha_hippo import paths

try:
    from atomizer_config_testutil import isolated_atomizer_config
except ImportError:  # pragma: no cover - only hit under `-m unittest tests.x` from repo root
    from tests.atomizer_config_testutil import isolated_atomizer_config


class IsolatedAtomizerConfigTests(unittest.TestCase):
    def test_yields_a_seeded_config_under_a_tmp_root(self):
        before = os.environ.get("HIPPO_CONFIG_ROOT")
        with isolated_atomizer_config() as canonical:
            self.assertTrue(canonical.is_file())
            self.assertEqual(canonical.name, "config.yaml")
            self.assertEqual(canonical, paths.atomizer_config_path())
            self.assertNotEqual(
                canonical, paths.home_root() / ".config" / "paulsha-hippo" / "config.yaml"
            )
        self.assertEqual(os.environ.get("HIPPO_CONFIG_ROOT"), before)

    def test_restores_env_even_if_the_block_raises(self):
        before = os.environ.get("HIPPO_CONFIG_ROOT")
        with self.assertRaises(RuntimeError):
            with isolated_atomizer_config():
                raise RuntimeError("boom")
        self.assertEqual(os.environ.get("HIPPO_CONFIG_ROOT"), before)


if __name__ == "__main__":
    unittest.main()
