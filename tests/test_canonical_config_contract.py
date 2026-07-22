from __future__ import annotations

from pathlib import Path

from paulsha_hippo.atomizer.config import load_config


def test_managed_canonical_config_is_the_default_runtime_source(monkeypatch, tmp_path: Path):
    config_root = tmp_path / "hippo-config"
    config_root.mkdir()
    (config_root / "atomizer.yaml").write_text(
        "schema_version: 1\n"
        "split:\n  boundary_patterns: ['^#']\n  max_fragment_chars: 8000\n"
        "promoter: identity\n",
        encoding="utf-8",
    )
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "atomizer.override.yaml").write_text(
        "promoter: llm\nagent_exec:\n  api_key_env: MUST_NOT_BE_READ\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("PSC_CONFIG_ROOT", str(legacy))

    cfg, _ = load_config()

    assert cfg.default_promoter == "identity"
    assert cfg.agent_exec_backend == "external-cli"
