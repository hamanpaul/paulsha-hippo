#!/usr/bin/env python3
"""Build one wheel plus a non-recursive identity sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_release_artifact")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]), type=Path)
    parser.add_argument("--commit", default=None)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if args.commit:
        env["HIPPO_BUILD_COMMIT"] = args.commit
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(repo), "--no-deps", "--wheel-dir", str(out)],
        cwd=str(repo),
        env=env,
        check=True,
    )
    wheels = sorted(out.glob("*.whl"), key=lambda path: path.stat().st_mtime)
    if not wheels:
        raise SystemExit("no wheel was produced")
    wheel = wheels[-1]
    commit = args.commit
    if not commit:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=False
        )
        commit = result.stdout.strip() if result.returncode == 0 else "unknown"
    manifest = {
        "schema_version": "1",
        "version": os.environ.get("HIPPO_VERSION", "0.1.1"),
        "commit": commit or "unknown",
        "wheel": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    manifest_path = out / f"{wheel.stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
