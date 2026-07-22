"""Command line interface for the Stage 2 memory importer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..build_info import version_json
from .pipeline import PipelineError, ingest_queue_item


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m paulsha_hippo.importer.cli")
    parser.add_argument("--version", action="version", version=version_json())
    subcommands = parser.add_subparsers(dest="command", required=True)
    ingest = subcommands.add_parser("ingest", help="ingest one queue item")
    ingest.add_argument("--queue-item", required=True, type=Path)
    ingest.add_argument("--memory-root", required=True, type=Path)
    ingest.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "ingest":
        try:
            decision = ingest_queue_item(args.queue_item, memory_root=args.memory_root, dry_run=args.dry_run)
        except (PipelineError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
