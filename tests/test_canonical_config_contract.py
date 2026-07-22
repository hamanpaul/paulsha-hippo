from __future__ import annotations

from pathlib import Path

import pytest

from paulsha_hippo.atomizer.config import AtomizerConfigError, load_config


def test_managed_canonical_config_is_the_default_runtime_source(monkeypatch, tmp_path: Path):
    config_root = tmp_path / "hippo-config"
    config_root.mkdir(exist_ok=True)
    (config_root / "config.yaml").write_text(
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


def test_missing_managed_canonical_config_fails_closed(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(tmp_path / "missing"))

    with pytest.raises(AtomizerConfigError, match="Canonical runtime config not found"):
        load_config()


def test_nested_provider_fields_are_rejected_without_echoing_value(
    monkeypatch, tmp_path: Path
):
    config_root = tmp_path / "hippo-config"
    config_root.mkdir(exist_ok=True)
    secret_value = "must-not-appear-in-errors"
    (config_root / "config.yaml").write_text(
        "schema_version: 1\n"
        "split:\n  boundary_patterns: ['^#']\n  max_fragment_chars: 8000\n"
        "external_agents:\n"
        "  profiles:\n"
        "    - id: local\n"
        "      metadata:\n"
        f"        provider_url: {secret_value}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(config_root))

    with pytest.raises(AtomizerConfigError) as caught:
        load_config()

    message = str(caught.value)
    assert "operator-redaction-required" in message
    assert "external_agents.profiles[0].metadata.provider_url" in message
    assert secret_value not in message
