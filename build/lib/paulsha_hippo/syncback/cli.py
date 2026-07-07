from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .gate import evaluate_gate


def run(args: argparse.Namespace, *, test_runner: Callable | None = None) -> int:
    now = args.now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    kwargs = {
        "now": now,
        "run_tests": not args.no_run_tests,
    }
    if test_runner is not None:
        kwargs["test_runner"] = test_runner

    verdict = evaluate_gate(Path(args.repo_root), **kwargs)

    if args.json:
        print(json.dumps(asdict(verdict), ensure_ascii=False, sort_keys=True))
    else:
        print(f"sync-back gate: {'PASS' if verdict.ok else 'FAIL'} ({verdict.ts})")
        for condition in verdict.conditions:
            status = "PASS" if condition.passed else "FAIL"
            detail = f" — {condition.detail}" if condition.detail else ""
            print(f"{status} {condition.id}: {condition.name}{detail}")
        if verdict.ok:
            print("sync manifest (NOT executed; manual sync only):")
            for path in verdict.sync_manifest:
                print(f"- {path}")
    return 0 if verdict.ok else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="psc memory syncback")
    subparsers = parser.add_subparsers(dest="syncback_command", required=True)

    check = subparsers.add_parser("check", help="evaluate the sync-back gate")
    check.add_argument("--repo-root", default=".")
    check.add_argument("--no-run-tests", action="store_true")
    check.add_argument("--json", action="store_true")
    check.add_argument("--now", default=None)
    check.set_defaults(func=run)
    return parser


def main(argv: Sequence[str] | None = None, *, _test_runner: Callable | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args, test_runner=_test_runner))


if __name__ == "__main__":
    raise SystemExit(main())
