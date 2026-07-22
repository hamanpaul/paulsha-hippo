"""Test suite for atomizer config loader."""
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from paulsha_hippo.atomizer.config import (
    AtomizerConfig,
    AtomizerConfigError,
    DEFAULT_CONFIG_DIR,
    _deep_merge,
    load_config,
    resolve_command_argv,
)


class TestAtomizerConfig(unittest.TestCase):
    """Test atomizer configuration loading and hashing."""

    def test_load_defaults(self):
        """load_config(override_path=None) returns cfg with expected defaults and hash."""
        cfg, hash_value = load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=None)
        
        self.assertEqual(cfg.default_artifact_kind, "report")
        self.assertEqual(cfg.default_phase, "review")
        self.assertGreater(cfg.max_fragment_chars, 0)
        self.assertIsInstance(cfg.boundary_patterns, tuple)
        self.assertGreater(len(cfg.boundary_patterns), 0)
        self.assertEqual(cfg.context_window, 32768)
        self.assertEqual(len(hash_value), 64)  # SHA-256 hex digest

    def test_override_merges_and_changes_hash(self):
        """Base hash differs after override file with split.max_fragment_chars: 100."""
        # Load default config and get hash
        cfg_default, hash_default = load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=None)
        
        # Create override file with modified max_fragment_chars
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            override_path = Path(f.name)
            f.write("split:\n  max_fragment_chars: 100\n")
        
        try:
            cfg_override, hash_override = load_config(
                default_dir=DEFAULT_CONFIG_DIR, override_path=override_path
            )
            
            # Hash should change
            self.assertNotEqual(hash_default, hash_override)
            
            # max_fragment_chars should be overridden
            self.assertEqual(cfg_override.max_fragment_chars, 100)
            
            # default_artifact_kind should remain report
            self.assertEqual(cfg_override.default_artifact_kind, "report")
        finally:
            override_path.unlink()

    def test_unsupported_schema_fails_closed(self):
        """Default dir containing atomizer.yaml with schema_version: 9 raises AtomizerConfigError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            config_file = config_dir / "atomizer.yaml"
            config_file.write_text("schema_version: 9\n")
            
            with self.assertRaises(AtomizerConfigError) as ctx:
                load_config(default_dir=config_dir, override_path=None)
            
            self.assertIn("schema", str(ctx.exception).lower())

    def test_hash_deterministic(self):
        """Repeated default config loads produce same hash."""
        _, hash1 = load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=None)
        _, hash2 = load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=None)
        
        self.assertEqual(hash1, hash2)

    def test_context_window_below_minimum_fails_closed(self):
        """Provider contexts below the shipped 32K baseline fail at config load."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            override_path = Path(f.name)
            f.write("context_window: 32767\n")

        try:
            with self.assertRaisesRegex(
                AtomizerConfigError,
                r"context_window must be at least 32768, got 32767",
            ):
                load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=override_path)
        finally:
            override_path.unlink()

    def test_context_window_at_or_above_minimum_is_accepted(self):
        """Operators may declare larger provider contexts without widening Hippo gates."""
        for value in (32768, 32769, 262144):
            with self.subTest(value=value):
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".yaml", delete=False
                ) as f:
                    override_path = Path(f.name)
                    f.write(f"context_window: {value}\n")

                try:
                    cfg, _ = load_config(
                        default_dir=DEFAULT_CONFIG_DIR, override_path=override_path
                    )
                    self.assertEqual(cfg.context_window, value)
                finally:
                    override_path.unlink()

    def test_context_window_remains_in_config_hash(self):
        """32K and 256K provider declarations remain provenance-distinguishable."""
        hashes = []
        for value in (32768, 262144):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                override_path = Path(f.name)
                f.write(f"context_window: {value}\n")

            try:
                _, hash_value = load_config(
                    default_dir=DEFAULT_CONFIG_DIR, override_path=override_path
                )
                hashes.append(hash_value)
            finally:
                override_path.unlink()

        self.assertNotEqual(*hashes)

    def test_larger_context_declaration_cannot_weaken_fixed_safety_limits(self):
        """A 256K declaration does not make any execution safety limit tunable."""
        unsafe_overrides = (
            ("max_input_tokens: 12001\n", "max_input_tokens is fixed at 12000"),
            (
                "max_prompt_argv_bytes: 49153\n",
                "max_prompt_argv_bytes is fixed at 49152",
            ),
            ("chunk_retries: 3\n", "chunk_retries is fixed at 2"),
            ("parallelism: 2\n", "parallelism is fixed at 1"),
            (
                "agent_exec:\n  timeout_seconds: 301\n",
                "agent_exec.timeout_seconds is fixed at 300",
            ),
            (
                "agent_exec:\n  max_output_tokens: 2049\n",
                "agent_exec.max_output_tokens is fixed at 2048",
            ),
        )
        for override_body, expected_error in unsafe_overrides:
            with self.subTest(override_body=override_body):
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".yaml", delete=False
                ) as f:
                    override_path = Path(f.name)
                    f.write("context_window: 262144\n" + override_body)

                try:
                    with self.assertRaisesRegex(
                        AtomizerConfigError,
                        expected_error,
                    ):
                        load_config(
                            default_dir=DEFAULT_CONFIG_DIR,
                            override_path=override_path,
                        )
                finally:
                    override_path.unlink()

    def test_bool_as_int_rejected(self):
        """Boolean max_fragment_chars must fail closed instead of becoming 1 or 0."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            override_path = Path(f.name)
            f.write("split:\n  max_fragment_chars: true\n")

        try:
            with self.assertRaises(AtomizerConfigError):
                load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=override_path)
        finally:
            override_path.unlink()

    def test_nonpositive_max_chars_rejected(self):
        """Zero or negative max_fragment_chars must fail closed."""
        for value in (0, -1000):
            with self.subTest(value=value):
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                    override_path = Path(f.name)
                    f.write(f"split:\n  max_fragment_chars: {value}\n")

                try:
                    with self.assertRaises(AtomizerConfigError):
                        load_config(
                            default_dir=DEFAULT_CONFIG_DIR,
                            override_path=override_path,
                        )
                finally:
                    override_path.unlink()

    def test_string_boundary_patterns_rejected(self):
        """boundary_patterns must be a list, not a string split into characters."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            override_path = Path(f.name)
            f.write("split:\n  boundary_patterns: not-a-list\n")

        try:
            with self.assertRaises(AtomizerConfigError):
                load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=override_path)
        finally:
            override_path.unlink()

    def test_config_maps_are_immutable(self):
        """Frozen config must not expose mutable mapping internals."""
        cfg, _ = load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=None)

        with self.assertRaises(TypeError):
            cfg.artifact_kind_map["injected"] = "malicious"

        with self.assertRaises(TypeError):
            cfg.phase_map["injected"] = "malicious"

    def test_deep_merge_does_not_mutate_nested_base(self):
        """Deep merge must not share nested dict references from the base config."""
        base = {
            "split": {"max_fragment_chars": 8000, "boundary_patterns": ["^#"]},
            "phase_map": {"report": "review"},
        }
        merged = _deep_merge(base, {"split": {"max_fragment_chars": 100}})
        merged["phase_map"]["report"] = "mutated"

        self.assertEqual(base["split"]["max_fragment_chars"], 8000)
        self.assertEqual(base["phase_map"]["report"], "review")
        self.assertEqual(merged["split"]["max_fragment_chars"], 100)


class AgentExecConfigTests(unittest.TestCase):
    def test_agent_exec_and_promoter_defaults(self):
        cfg, _ = load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=None)

        self.assertTrue(cfg.agent_exec_command)
        self.assertGreater(cfg.agent_exec_timeout, 0)
        self.assertEqual(cfg.agent_exec_backend, "external-cli")
        self.assertGreaterEqual(len(cfg.external_profiles), 6)
        self.assertNotIn("upstream_url", cfg.__dict__)
        self.assertIn(cfg.default_promoter, ("identity", "llm"))
        self.assertTrue(cfg.skill_path)
        self.assertTrue(cfg.known_projects_file)

    def test_retired_provider_fields_are_rejected(self):
        import pathlib
        from paulsha_hippo.atomizer import config as cfgmod

        base = (
            "schema_version: 1\n"
            "split:\n"
            "  boundary_patterns:\n"
            "    - '^#'\n"
            "  max_fragment_chars: 8000\n"
            "agent_exec:\n"
            "  api_key_env: HIPPO_PROVIDER_KEY\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "atomizer.yaml").write_text(base, encoding="utf-8")
            with self.assertRaisesRegex(cfgmod.AtomizerConfigError, "operator-redaction"):
                cfgmod.load_config(default_dir=p, override_path=None)

    def test_resolve_agent_exec_settings_has_no_endpoint(self):
        from paulsha_hippo.atomizer import config as cfgmod

        with mock.patch.dict(
            "os.environ",
            {"PSC_CLAUDE_GEMMA4_UPSTREAM_URL": "http://10.0.0.9:9002"},
            clear=False,
        ):
            command, model = cfgmod.resolve_agent_exec_settings()
        self.assertTrue(command)
        self.assertTrue(model)
        self.assertNotIn("http://", " ".join(command))

    def test_missing_known_projects_file_uses_facade_default(self):
        from paulsha_hippo.atomizer import config as cfgmod

        base = "schema_version: 1\nsplit:\n  boundary_patterns:\n    - '^#'\n  max_fragment_chars: 8000\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "atomizer.yaml").write_text(base, encoding="utf-8")
            agents_root = p / "custom-agents"
            with mock.patch.dict(
                "os.environ",
                {
                    "PSC_AGENTS_ROOT": str(agents_root),
                    "PSC_CONFIG_ROOT": "",
                },
                clear=False,
            ):
                cfg, _ = cfgmod.load_config(default_dir=p, override_path=None)

            self.assertEqual(
                cfg.known_projects_file,
                str(agents_root / "config" / "projects.yaml"),
            )

    def test_invalid_timeout_fails_closed_with_config_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            override_path = Path(f.name)
            f.write("agent_exec:\n  timeout_seconds: nope\n")

        try:
            with self.assertRaises(AtomizerConfigError):
                load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=override_path)
        finally:
            override_path.unlink()

    def test_float_timeout_fails_closed_with_config_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            override_path = Path(f.name)
            f.write("agent_exec:\n  timeout_seconds: 1.5\n")

        try:
            with self.assertRaises(AtomizerConfigError):
                load_config(default_dir=DEFAULT_CONFIG_DIR, override_path=override_path)
        finally:
            override_path.unlink()

    def test_resolve_command_argv_expands_repo_relative_script(self):
        resolved = resolve_command_argv(("scripts/build_release_artifact.py",))
        self.assertTrue(Path(resolved[0]).is_absolute())
        self.assertTrue(resolved[0].endswith("/scripts/build_release_artifact.py"))

    def test_max_output_tokens_default_and_in_hash(self):
        import tempfile, pathlib
        from paulsha_hippo.atomizer import config as cfgmod
        base = "schema_version: 1\nsplit:\n  boundary_patterns:\n    - '^#'\n  max_fragment_chars: 8000\n"
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "atomizer.yaml").write_text(base, encoding="utf-8")
            cfg_default, hash_default = cfgmod.load_config(default_dir=p, override_path=None)
            self.assertEqual(cfg_default.agent_exec_max_output_tokens, 2048)
            (p / "atomizer.yaml").write_text(base + "agent_exec:\n  max_output_tokens: 16384\n", encoding="utf-8")
            with self.assertRaises(cfgmod.AtomizerConfigError):
                cfgmod.load_config(default_dir=p, override_path=None)

    def test_max_output_tokens_rejects_non_positive(self):
        import tempfile, pathlib
        from paulsha_hippo.atomizer import config as cfgmod
        base = ("schema_version: 1\nsplit:\n  boundary_patterns:\n    - '^#'\n"
                "  max_fragment_chars: 8000\nagent_exec:\n  max_output_tokens: 0\n")
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "atomizer.yaml").write_text(base, encoding="utf-8")
            with self.assertRaises(cfgmod.AtomizerConfigError):
                cfgmod.load_config(default_dir=p, override_path=None)


if __name__ == '__main__':
    unittest.main()
