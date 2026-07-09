from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..atomizer import cli as atomizer_cli
from ..atomizer import config as atomizer_config
from ..atomizer import pipeline as atomizer_pipeline
from ..instruction_corpus import corpus_for_roots
from ..janitor import config as janitor_config
from ..janitor import scanner as janitor_scanner
from ..ledger import dream as dream_ledger
from ..moc import runner as moc_runner
from ..lib import idle
from . import orchestrator


def _run(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root)

    if args.require_idle and not idle.is_idle(max_load=args.max_load):
        print(
            json.dumps(
                {
                    "skipped": "system busy",
                    "backlog_depth": dream_ledger.backlog_depth(memory_root),
                },
                sort_keys=True,
            )
        )
        return 0

    mem_info: dict[str, int] | None = None

    def mem_probe() -> dict[str, int]:
        nonlocal mem_info
        if mem_info is None:
            reader = getattr(idle, "_read_meminfo", None)
            mem_info = reader() if callable(reader) else {}
        return mem_info

    if args.require_idle and not idle.has_mem_headroom(
        getattr(args, "min_avail_mem_pct", 20.0) / 100.0,
        probe=mem_probe,
    ):
        info = mem_info or {}
        try:
            avail_pct = round(100.0 * info["MemAvailable"] / info["MemTotal"], 1)
        except (KeyError, ZeroDivisionError, TypeError):
            avail_pct = None
        print(
            json.dumps(
                {
                    "skipped": "low memory",
                    "avail_pct": avail_pct,
                    "backlog_depth": dream_ledger.backlog_depth(memory_root),
                },
                sort_keys=True,
            )
        )
        return 0

    atom_cfg, atom_hash = atomizer_config.load_config()
    jan_cfg, jan_hash = janitor_config.load_config()
    promoter = atomizer_cli._build_promoter(args, atom_cfg, memory_root)
    now = args.now
    doc_corpus = corpus_for_roots(getattr(args, "instruction_root", None))

    def atomize_fn() -> dict[str, object]:
        return atomizer_pipeline.run(
            memory_root,
            config=atom_cfg,
            config_hash=atom_hash,
            now=now,
            dry_run=args.dry_run,
            promoter=promoter,
            doc_corpus=doc_corpus,
        )

    def janitor_fn() -> dict[str, object]:
        # In the dream/service context the provenance source repos are usually
        # not checked out at the run CWD, so a CWD-relative path probe gives
        # false negatives and would spuriously decay freshly atomized knowledge.
        # Return None (cannot determine) so source_invalid decay is disabled here;
        # TTL and supersede decay still apply.
        return janitor_scanner.run_scan(
            memory_root=memory_root,
            knowledge_root=memory_root / "knowledge",
            config=jan_cfg,
            config_hash=jan_hash,
            now=now,
            dry_run=args.dry_run,
            source_path_exists=lambda record: None,
        )

    def moc_fn() -> dict[str, object]:
        if args.dry_run:
            return {"summary": {"skipped": "dry-run"}, "warnings": []}
        result = moc_runner.run_moc(memory_root, now)
        warnings = result.pop("warnings", [])
        return {
            "summary": result,
            "warnings": warnings,
        }

    result = orchestrator.run_dream(
        memory_root,
        atomize_fn=atomize_fn,
        janitor_fn=janitor_fn,
        moc_fn=moc_fn,
        now=now,
        config_hash=f"{atom_hash[:8]}:{jan_hash[:8]}",
        dry_run=args.dry_run,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


def _status(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root)
    print(
        json.dumps(
            {
                "last_run": dream_ledger.last_run(memory_root),
                "backlog_depth": dream_ledger.backlog_depth(memory_root),
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0


def run(args: argparse.Namespace) -> int:
    if args.dream_command == "status":
        return _status(args)
    return _run(args)
