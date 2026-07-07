"""
Test suite for janitor configuration loader and hash.
"""
import unittest
import json
import tempfile
import hashlib
from pathlib import Path
from paulsha_hippo.janitor.config import (
    JanitorConfigError,
    JanitorConfig,
    load_config,
    DEFAULT_CONFIG_DIR,
    _deep_merge,
)


class TestJanitorConfig(unittest.TestCase):
    """Test janitor configuration loading and hash."""

    def test_load_defaults(self):
        """Should load default lifecycle.yaml successfully."""
        config, config_hash = load_config(override_path=None)
        
        self.assertEqual(config.schema_version, "1")
        self.assertEqual(config.default_decay_age_days, 90)
        self.assertEqual(config.by_artifact_kind, {})
        self.assertTrue(config.check_provenance_path)
        self.assertFalse(config.check_provenance_commit)
        self.assertTrue(config.decay_superseded)
        self.assertIsInstance(config_hash, str)
        self.assertEqual(len(config_hash), 64)  # sha256 hex

    def test_unsupported_schema_version_raises(self):
        """Should raise JanitorConfigError for unsupported schema version."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("schema_version: 999\n")
            f.write("ttl:\n")
            f.write("  default_decay_age_days: 90\n")
            override_path = f.name
        
        try:
            with self.assertRaises(JanitorConfigError) as ctx:
                load_config(override_path=override_path)
            self.assertIn("schema_version", str(ctx.exception).lower())
        finally:
            Path(override_path).unlink(missing_ok=True)

    def test_override_merge_and_changed_hash(self):
        """Should merge override deeply and produce different hash."""
        # Get baseline
        config1, hash1 = load_config(override_path=None)
        
        # Create override that changes a value
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("schema_version: 1\n")
            f.write("ttl:\n")
            f.write("  default_decay_age_days: 180\n")
            f.write("  by_artifact_kind:\n")
            f.write("    spec: 365\n")
            override_path = f.name
        
        try:
            config2, hash2 = load_config(override_path=override_path)
            
            # Check merge worked
            self.assertEqual(config2.schema_version, "1")
            self.assertEqual(config2.default_decay_age_days, 180)
            self.assertEqual(config2.by_artifact_kind, {"spec": 365})
            # Untouched defaults preserved
            self.assertTrue(config2.check_provenance_path)
            self.assertFalse(config2.check_provenance_commit)
            self.assertTrue(config2.decay_superseded)
            
            # Hash should differ
            self.assertNotEqual(hash1, hash2)
        finally:
            Path(override_path).unlink(missing_ok=True)

    def test_deterministic_hash(self):
        """Config hash should be deterministic."""
        config1, hash1 = load_config(override_path=None)
        config2, hash2 = load_config(override_path=None)
        
        self.assertEqual(hash1, hash2)

    def test_default_override_path_if_exists(self):
        """Should check ~/.config/paulshaclaw/janitor.override.yaml if it exists."""
        # This test documents behavior but may not have the file
        # Just ensure it doesn't crash when override doesn't exist
        config, config_hash = load_config()
        self.assertIsInstance(config, JanitorConfig)
        self.assertIsInstance(config_hash, str)

    def test_invalid_config_shape_raises(self):
        """Should raise JanitorConfigError for invalid config structure."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("schema_version: 1\n")
            f.write("ttl: invalid_string_not_dict\n")
            override_path = f.name
        
        try:
            with self.assertRaises(JanitorConfigError):
                load_config(override_path=override_path)
        finally:
            Path(override_path).unlink(missing_ok=True)

    def test_empty_override_is_noop(self):
        """Empty override file should be treated as an empty override."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("")
            override_path = f.name

        try:
            config, config_hash = load_config(override_path=override_path)
            default_config, default_hash = load_config(override_path=None)
        finally:
            Path(override_path).unlink(missing_ok=True)

        self.assertEqual(config, default_config)
        self.assertEqual(config_hash, default_hash)

    def test_non_dict_override_raises_janitor_config_error(self):
        """Non-dict override YAML should fail closed with JanitorConfigError."""
        invalid_overrides = ["plain-string\n", "- item\n", "42\n"]

        for content in invalid_overrides:
            with self.subTest(content=content):
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                    f.write(content)
                    override_path = f.name

                try:
                    with self.assertRaises(JanitorConfigError):
                        load_config(override_path=override_path)
                finally:
                    Path(override_path).unlink(missing_ok=True)

    def test_missing_explicit_override_raises_janitor_config_error(self):
        """Explicit missing override path should raise JanitorConfigError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "missing.yaml"

            with self.assertRaises(JanitorConfigError):
                load_config(override_path=missing_path)

    def test_invalid_yaml_raises_janitor_config_error(self):
        """Invalid YAML should raise JanitorConfigError instead of parser errors."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("ttl:\n  default_decay_age_days: [unterminated\n")
            override_path = f.name

        try:
            with self.assertRaises(JanitorConfigError):
                load_config(override_path=override_path)
        finally:
            Path(override_path).unlink(missing_ok=True)

    def test_by_artifact_kind_values_must_be_positive_ints(self):
        """Should reject non-positive or non-int artifact-kind TTL values."""
        invalid_overrides = [
            "ttl:\n  by_artifact_kind:\n    spec: not_an_int\n",
            "ttl:\n  by_artifact_kind:\n    spec: 0\n",
            "ttl:\n  by_artifact_kind:\n    spec: -1\n",
        ]

        for content in invalid_overrides:
            with self.subTest(content=content):
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                    f.write(content)
                    override_path = f.name

                try:
                    with self.assertRaises(JanitorConfigError):
                        load_config(override_path=override_path)
                finally:
                    Path(override_path).unlink(missing_ok=True)

    def test_default_decay_age_days_must_be_positive(self):
        """Should reject non-positive default decay age."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("ttl:\n")
            f.write("  default_decay_age_days: 0\n")
            override_path = f.name

        try:
            with self.assertRaises(JanitorConfigError):
                load_config(override_path=override_path)
        finally:
            Path(override_path).unlink(missing_ok=True)

    def test_default_decay_age_days_rejects_bool(self):
        """Should reject YAML booleans for integer default decay age."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("ttl:\n")
            f.write("  default_decay_age_days: true\n")
            override_path = f.name

        try:
            with self.assertRaises(JanitorConfigError):
                load_config(override_path=override_path)
        finally:
            Path(override_path).unlink(missing_ok=True)

    def test_by_artifact_kind_values_reject_bool(self):
        """Should reject YAML booleans for artifact-kind TTL values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("ttl:\n")
            f.write("  by_artifact_kind:\n")
            f.write("    spec: true\n")
            override_path = f.name

        try:
            with self.assertRaises(JanitorConfigError):
                load_config(override_path=override_path)
        finally:
            Path(override_path).unlink(missing_ok=True)

    def test_deep_merge_does_not_alias_nested_base_dicts(self):
        """Deep merge should not share untouched nested dicts with base."""
        base = {"ttl": {"by_artifact_kind": {"spec": 90}}}
        merged = _deep_merge(base, {"ttl": {"default_decay_age_days": 180}})

        merged["ttl"]["by_artifact_kind"]["spec"] = 365

        self.assertEqual(base["ttl"]["by_artifact_kind"]["spec"], 90)

    def test_default_config_dir_exists(self):
        """DEFAULT_CONFIG_DIR should point to janitor module directory."""
        self.assertTrue(DEFAULT_CONFIG_DIR.exists())
        self.assertTrue((DEFAULT_CONFIG_DIR / "lifecycle.yaml").exists())


if __name__ == '__main__':
    unittest.main()
