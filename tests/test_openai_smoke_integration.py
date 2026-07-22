"""Retirement contract for the former direct-provider integration profile.

The old live HTTP smoke is intentionally gone: Hippo only executes external
headless CLI profiles and must never accept a provider URL or API-key field.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from paulsha_hippo.atomizer import config


class DirectProviderRetirementTests(unittest.TestCase):
    def test_http_backend_is_rejected_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = (Path(config.DEFAULT_CONFIG_DIR) / "atomizer.yaml").read_text(
                encoding="utf-8"
            )
            (root / "atomizer.yaml").write_text(base, encoding="utf-8")
            override = root / "override.yaml"
            override.write_text(
                'schema_version: "1"\nagent_exec:\n  backend: http\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(config.AtomizerConfigError, "operator-redaction"):
                config.load_config(default_dir=root, override_path=override)

    def test_api_key_field_is_rejected_without_echoing_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "atomizer.yaml").write_text(
                "schema_version: 1\nsplit:\n  boundary_patterns: []\n",
                encoding="utf-8",
            )
            override = root / "override.yaml"
            override.write_text(
                "agent_exec:\n  api_key_env: HIPPO_TEST_PROVIDER_KEY\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(config.AtomizerConfigError, "operator-redaction") as ctx:
                config.load_config(default_dir=root, override_path=override)
            self.assertNotIn("HIPPO_TEST_PROVIDER_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
