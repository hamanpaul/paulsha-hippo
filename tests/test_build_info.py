from __future__ import annotations

import json

from paulsha_hippo import __version__
from paulsha_hippo import build_info


def test_build_identity_includes_version_and_explicit_build_commit(monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_BUILD_COMMIT", "candidate-commit")
    identity = build_info.build_identity(package_root=tmp_path / "site-packages" / "paulsha_hippo")

    assert identity["version"] == __version__ == "0.1.1"
    assert identity["build_commit"] == "candidate-commit"
    assert identity["package_root"].endswith("site-packages/paulsha_hippo")
    assert json.loads(build_info.version_json(package_root=tmp_path))["version"] == "0.1.1"
