from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..atomizer import cli as atomizer_cli
from ..atomizer import pipeline as atomizer_pipeline
from ..instruction_corpus import corpus_for_roots
from ..janitor import config as janitor_config
from ..janitor import scanner as janitor_scanner
from ..ledger import dream as dream_ledger
from ..moc import runner as moc_runner
from ..lib import idle
from . import lock as dream_lock
from . import orchestrator


def _run(args: argparse.Namespace) -> int:
    memory_root = Path(args.memory_root)

    # #19/#15：global dream singleton——整輪持有 nonblocking flock；
    # 取不到鎖代表另一個 dream run 進行中，記 log 後 skip（exit 0），不得競寫。
    lock_handle = dream_lock.acquire_dream_lock(memory_root)
    if lock_handle is None:
        print(
            json.dumps(
                {
                    "skipped": "dream lock held by another process",
                    "lock_path": str(dream_lock.dream_lock_path(memory_root)),
                },
                sort_keys=True,
            )
        )
        return 0
    try:
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

        now = args.now

        # #15 失敗鏈：config 載入與 promoter 建構是 atomize 失敗邊界的一部分，
        # 不得逃出 run_dream 記錄邊界（否則無 failure category／evidence／dream
        # error record，timer 每輪重複整輪失敗）。初始化改走 atomizer_cli 共用
        # 邊界（直呼 `hippo atomize` 同一套）：失敗分類 backend_unavailable，
        # eligible split sessions 立即 park（含證據），失敗本身由 run_dream 記
        # 為 dream error record。
        atom_cfg, atom_hash, promoter, atom_error = atomizer_cli.prepare_pipeline_inputs(
            args, memory_root
        )

        jan_error: Exception | None = None
        jan_cfg = None
        jan_hash = ""
        try:
            jan_cfg, jan_hash = janitor_config.load_config()
        except Exception as exc:  # noqa: BLE001 —同上：janitor config 失敗也要入記錄邊界
            jan_error = exc

        doc_corpus = corpus_for_roots(getattr(args, "instruction_root", None))

        def atomize_fn() -> dict[str, object]:
            if atom_error is not None:
                atomizer_cli.park_init_failure(
                    memory_root, error=atom_error, now=now,
                    config_hash=atom_hash, dry_run=args.dry_run,
                )
                raise atom_error
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
            if jan_error is not None:
                raise jan_error
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
            config_hash=(
                f"{atom_hash[:8] if atom_hash else 'invalid'}"
                f":{jan_hash[:8] if jan_hash else 'invalid'}"
            ),
            dry_run=args.dry_run,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    finally:
        lock_handle.close()


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


def _reconcile(args: argparse.Namespace) -> int:
    from . import reconcile as reconcile_mod
    memory_root = Path(args.memory_root)
    now = args.now or datetime.now().isoformat()
    result = reconcile_mod.run_reconcile(
        memory_root,
        now=now,
        dry_run=args.dry_run,
        apply=args.apply,
        limit=args.limit,
    )
    print(result)
    return 0


def run(args: argparse.Namespace) -> int:
    if args.dream_command == "status":
        return _status(args)
    if args.dream_command == "reconcile":
        return _reconcile(args)
    return _run(args)
