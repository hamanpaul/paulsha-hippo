from __future__ import annotations

import json

import pytest

from paulsha_hippo import __version__
from paulsha_hippo import build_info


def test_build_identity_includes_version_and_explicit_build_commit(monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_BUILD_COMMIT", "candidate-commit")
    identity = build_info.build_identity(package_root=tmp_path / "site-packages" / "paulsha_hippo")

    assert identity["version"] == __version__ == "0.1.1"
    assert identity["build_commit"] == "candidate-commit"
    assert identity["source_dirty"] is False
    assert identity["package_root"].endswith("site-packages/paulsha_hippo")
    assert json.loads(build_info.version_json(package_root=tmp_path))["version"] == "0.1.1"


def test_build_identity_reads_embedded_wheel_commit(monkeypatch, tmp_path):
    monkeypatch.delenv("HIPPO_BUILD_COMMIT", raising=False)
    package = tmp_path / "site-packages" / "paulsha_hippo"
    package.mkdir(parents=True)
    (package / "_build.json").write_text(
        json.dumps(
            {"schema_version": "1", "version": "0.1.1", "build_commit": "abc123"}
        ),
        encoding="utf-8",
    )
    identity = build_info.build_identity(package_root=package, resolve_git=False)
    assert identity["build_commit"] == "abc123"
    assert identity["source_dirty"] is False


def test_build_identity_rejects_embedded_version_mismatch(monkeypatch, tmp_path):
    monkeypatch.delenv("HIPPO_BUILD_COMMIT", raising=False)
    package = tmp_path / "paulsha_hippo"
    package.mkdir()
    (package / "_build.json").write_text(
        json.dumps({"schema_version": "1", "version": "9.9.9", "build_commit": "abc"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="package identity mismatch"):
        build_info.build_identity(package_root=package, resolve_git=False)
