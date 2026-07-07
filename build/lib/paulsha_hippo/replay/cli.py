from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..ledger.relations import RelationsLedgerError

from . import bundle, selector


def _print_manifest_warnings(out_dir: Path) -> None:
    try:
        manifest_path = out_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        warnings = manifest.get("warnings")
        if isinstance(warnings, list):
            for w in warnings:
                if w:
                    print(f"warning: {w}", file=sys.stderr)
    except Exception:
        return


def run(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root)
    tags = args.tag or None

    include_decayed = bool(args.include_decayed)

    try:
        try:
            slices = selector.select(
                memory_root,
                project=args.project,
                tags=tags,
                entity=args.entity,
                include_decayed=include_decayed,
            )
        except RelationsLedgerError as exc:
            raise selector.SelectorError(f"relations ledger unreadable: {exc}") from exc
        except ValueError:
            if include_decayed:
                raise
            print(
                "warning: lifecycle ledger unreadable; exporting without active filtering",
                file=sys.stderr,
            )
            include_decayed = True
            slices = selector.select(
                memory_root,
                project=args.project,
                tags=tags,
                entity=args.entity,
                include_decayed=include_decayed,
            )

        if not slices:
            print("warning: empty selection", file=sys.stderr)

        selection = {
            "project": args.project,
            "tags": tags,
            "entity": args.entity,
            "include_decayed": include_decayed,
        }
        out = bundle.build(memory_root, slices, Path(args.out), selection=selection, now=args.now)
        _print_manifest_warnings(out)
    except selector.SelectorError as exc:
        print(f"selector error: {exc}", file=sys.stderr)
        return 2
    except bundle.BundleError as exc:
        print(f"bundle error: {exc}", file=sys.stderr)
        return 2

    print(str(out))
    return 0
