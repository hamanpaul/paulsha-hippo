from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from paulsha_hippo import paths
from . import policy as memory_policy
from .moc import frontmatter_io as _fio
from .moc import moc_builder as _moc_builder
from .noise import classify_noise

BOUNDARY = "raw_to_distilled"


class PayloadReadError(Exception):
    """Raised when a payload file cannot be read as UTF-8 text."""


def _pct_arg(s):
    import math
    v = float(s)
    if not math.isfinite(v) or not (0.0 <= v <= 100.0):
        raise argparse.ArgumentTypeError("--min-avail-mem-pct must be a finite number in [0, 100]")
    return v


def _tool_arg(s: str) -> str:
    """`--tool` 會嵌入 runtime/wakeup 檔名：argparse 層即拒絕非 path-safe token（防 traversal）。"""
    from .hooks._wakeup_common import validate_tool

    try:
        return validate_tool(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse：--version/-h → 0；缺子命令/錯誤 → 2
        return int(exc.code or 0)
    try:
        return int(args.func(args))
    except PayloadReadError as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hippo",
        description="paulsha-hippo：跨 LLM vendor 的經驗筆記基座",
    )
    from paulsha_hippo import __version__

    parser.add_argument("--version", action="version", version=f"hippo {__version__}")
    memory_subparsers = parser.add_subparsers(dest="command", required=True)

    init_p = memory_subparsers.add_parser("init", help="初始化 config 與蒸餾 backend")
    init_p.add_argument("--memory-root")
    init_p.add_argument("--backend", default="claude-headless",
                        choices=["claude-headless", "openai-compatible", "custom-argv"])
    init_p.add_argument("--base-url")
    init_p.add_argument("--api-key-env")
    init_p.add_argument("--model")
    init_p.add_argument("--yes", action="store_true")
    init_p.set_defaults(func=_ops_init)

    doctor_p = memory_subparsers.add_parser("doctor", help="健檢：路徑契約/hooks/服務/backend")
    doctor_p.add_argument(
        "--fix-backend", action="store_true",
        help="冪等遷移：override 中 service-effective 解析不到的裸 backend 命令改寫為絕對路徑"
             "（先備份）；隱含 --probe-live 以真實 smoke probe 驗證遷移結果")
    doctor_p.add_argument(
        "--probe-live", action="store_true",
        help="對 configured backend 實際送一次 bounded smoke prompt（真實喚起，60s timeout、"
             "可能產生 API 成本；亦可 HIPPO_DOCTOR_LIVE_PROBE=1）。預設僅做解析檢查")
    doctor_p.set_defaults(func=_ops_doctor)

    install_p = memory_subparsers.add_parser("install")
    install_sub = install_p.add_subparsers(dest="install_target", required=True)
    install_hooks = install_sub.add_parser("hooks", help="安裝 agent host hooks（冪等）")
    install_hooks.add_argument("--memory-root")
    install_hooks.add_argument("--repo-root")
    install_hooks.set_defaults(func=_ops_install_hooks)
    install_service = install_sub.add_parser("service", help="安裝 dream 常駐（systemd 偵測+fallback）")
    install_service.add_argument("--enable", action="store_true")
    install_service.set_defaults(func=_ops_install_service)

    dry_run = memory_subparsers.add_parser("dry-run-policy")
    dry_run.add_argument("session_id")
    dry_run.add_argument("--payload-file", required=True)
    dry_run.add_argument("--project", default="_unknown")
    dry_run.add_argument("--override")
    dry_run.set_defaults(func=_dry_run_policy)

    replay = memory_subparsers.add_parser("replay")
    replay.add_argument("--session", required=True)
    replay.add_argument("--payload-file", required=True)
    replay.add_argument("--out", required=True)
    replay.add_argument("--project", default="_unknown")
    replay.add_argument("--override")
    replay.set_defaults(func=_replay)

    janitor = memory_subparsers.add_parser("janitor")
    janitor_subparsers = janitor.add_subparsers(dest="janitor_command", required=True)
    scan = janitor_subparsers.add_parser("scan")
    scan.add_argument("--memory-root", required=True)
    scan.add_argument("--knowledge-root", default=None)
    scan.add_argument("--now", default=None)
    scan.add_argument("--override", default=None)
    scan.add_argument("--dry-run", action="store_true")
    scan.set_defaults(func=_janitor_scan)

    atomize = memory_subparsers.add_parser("atomize")
    atomize.add_argument("--memory-root", required=True)
    atomize.add_argument("--now", default=None)
    atomize.add_argument("--override", default=None)
    atomize.add_argument("--promoter", choices=["identity", "llm"], default=None)
    atomize.add_argument("--agent-command", default=None)
    atomize.add_argument(
        "--instruction-root", action="append", default=None,
        help="agent-instruction doc root/file; when given, drops doc-fragment slices "
             "(verbatim instruction-doc sections) at produce time. Repeatable.")
    atomize.add_argument("--dry-run", action="store_true")
    atomize.set_defaults(func=_atomize)

    dream = memory_subparsers.add_parser("dream")
    dream_subparsers = dream.add_subparsers(dest="dream_command", required=True)
    dream_run = dream_subparsers.add_parser("run")
    dream_run.add_argument("--memory-root", required=True)
    dream_run.add_argument("--now", default=None)
    dream_run.add_argument("--dry-run", action="store_true")
    dream_run.add_argument("--require-idle", action="store_true")
    dream_run.add_argument("--max-load", type=float, default=1.0)
    dream_run.add_argument("--min-avail-mem-pct", type=_pct_arg, default=20.0)
    dream_run.add_argument("--promoter", choices=["identity", "llm"], default=None)
    dream_run.add_argument("--agent-command", default=None)
    dream_run.add_argument(
        "--instruction-root", action="append", default=None,
        help="agent-instruction doc root/file; when given, the atomize pass drops "
             "doc-fragment slices (verbatim instruction-doc sections) at produce "
             "time. Repeatable; omit to keep doc-fragment detection off.")
    dream_run.set_defaults(func=_dream)
    dream_supervise = dream_subparsers.add_parser(
        "supervise", help="前景常駐：每 interval 秒 dream run --require-idle（非 systemd 主機用）"
    )
    dream_supervise.add_argument("--interval", type=int, default=3600)
    dream_supervise.add_argument("--memory-root")
    dream_supervise.set_defaults(func=_dream_supervise)

    dream_status = dream_subparsers.add_parser("status")
    dream_status.add_argument("--memory-root", required=True)
    dream_status.set_defaults(func=_dream)

    skillopt = memory_subparsers.add_parser("skillopt")
    skillopt_subparsers = skillopt.add_subparsers(dest="skillopt_command", required=True)
    skillopt_run = skillopt_subparsers.add_parser("run")
    skillopt_run.add_argument("--memory-root", default=str(paths.memory_root()))
    skillopt_run.add_argument("--reference-root", default=str(paths.notes_root()))
    skillopt_run.add_argument("--skill-path", default=None)
    skillopt_run.add_argument("--budget", type=int, default=1)
    skillopt_run.add_argument("--dry-run", action="store_true")
    skillopt_run.add_argument("--now", default=None)
    skillopt_run.set_defaults(func=_skillopt)

    bundle_p = memory_subparsers.add_parser("bundle")
    bundle_p.add_argument("--memory-root", required=True)
    bundle_p.add_argument("--project", default=None)
    bundle_p.add_argument("--tag", action="append", default=None)
    bundle_p.add_argument("--entity", default=None)
    bundle_p.add_argument("--include-decayed", action="store_true")
    bundle_p.add_argument("--out", required=True)
    bundle_p.add_argument("--now", default=None)
    bundle_p.set_defaults(func=_bundle)

    search_p = memory_subparsers.add_parser("search")
    search_p.add_argument("query")
    search_p.add_argument("--memory-root", required=True)
    search_p.add_argument("--project", default=None)
    search_p.add_argument("--limit", type=int, default=10)
    search_p.add_argument("--include-decayed", action="store_true")
    search_p.set_defaults(func=_search)

    index_p = memory_subparsers.add_parser("index", help="檢索索引維護")
    index_subparsers = index_p.add_subparsers(dest="index_command", required=True)
    index_verify = index_subparsers.add_parser(
        "verify", help="三方對賬：filesystem census × coverage 報表 × index DB 反查")
    index_verify.add_argument("--memory-root", required=True)
    index_verify.set_defaults(func=_index_verify)

    wakeup_p = memory_subparsers.add_parser("wakeup")
    wakeup_p.add_argument("--memory-root", default=str(paths.memory_root()))
    wakeup_p.add_argument("--project", default=None)
    wakeup_p.add_argument("--cwd", default=None)
    wakeup_p.add_argument("--k", type=int, default=8)
    wakeup_p.add_argument("--char-budget", type=int, default=8000)
    wakeup_p.add_argument("--now", default=None)
    wakeup_p.set_defaults(func=_wakeup)

    syncback = memory_subparsers.add_parser("syncback")
    syncback_subparsers = syncback.add_subparsers(dest="syncback_command", required=True)
    syncback_check = syncback_subparsers.add_parser("check")
    syncback_check.add_argument("--repo-root", default=".")
    syncback_check.add_argument("--no-run-tests", action="store_true")
    syncback_check.add_argument("--json", action="store_true")
    syncback_check.add_argument("--now", default=None)
    syncback_check.set_defaults(func=_syncback)

    knowledge = memory_subparsers.add_parser("knowledge")
    knowledge_subparsers = knowledge.add_subparsers(dest="knowledge_command", required=True)
    prune = knowledge_subparsers.add_parser("prune-noise")
    prune.add_argument("--memory-root", required=True)
    prune.add_argument("--now", default=None)
    prune.add_argument(
        "--instruction-root", action="append", default=None,
        help="agent-instruction doc root/file (CLAUDE.md/AGENTS.md/GEMINI.md). Repeatable. "
             "When given, enables doc-fragment pruning against that corpus; omit to disable.")
    prune.add_argument(
        "--project", action="append", default=None,
        help="restrict pruning to these project(s). Repeatable; omit to scan all projects.")
    prune.add_argument(
        "--paths", default=None,
        help="固定清單檔：每行一個 knowledge slice 絕對路徑（# 開頭與空行忽略）。"
             "給定時清單即刪除權威，且與 --instruction-root/--project 互斥。")
    group = prune.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    prune.set_defaults(func=_prune_noise)

    retitle = knowledge_subparsers.add_parser("retitle-untitled")
    retitle.add_argument("--memory-root", required=True)
    retitle.add_argument("--now", default=None)
    retitle.add_argument(
        "--instruction-root", action="append", default=None,
        help="agent-instruction doc root/file; builds the doc-fragment guard corpus so "
             "instruction fragments are skipped (left for prune-noise) instead of retitled.")
    retitle.add_argument("--agent-command", default=None,
                         help="override the title-distillation command (default: gemma4 wrapper).")
    retitle.add_argument(
        "--project", action="append", default=None,
        help="restrict retitling to these project(s). Repeatable; omit to scan all projects.")
    rgroup = retitle.add_mutually_exclusive_group()
    rgroup.add_argument("--dry-run", action="store_true")
    rgroup.add_argument("--apply", action="store_true")
    retitle.set_defaults(func=_retitle_untitled)

    rekey_p = knowledge_subparsers.add_parser("rekey")
    rekey_p.add_argument("--memory-root", required=True)
    rekey_p.add_argument("--from", dest="from_key", required=True,
                         help="舊 project key（可含 '/'，嚴格相等比對）。")
    rekey_p.add_argument("--to", dest="to_slug", required=True,
                         help="新短 slug（path-safe，不得含 '/'）。")
    rekey_p.add_argument("--now", default=None)
    kgroup = rekey_p.add_mutually_exclusive_group()
    kgroup.add_argument("--dry-run", action="store_true")
    kgroup.add_argument("--apply", action="store_true")
    rekey_p.set_defaults(func=_rekey)

    usage_p = memory_subparsers.add_parser("usage")
    # Let argparse accept `hippo usage mark-applied --memory-root ...`; the report path
    # still errors with exit 2 when the flag is omitted.
    usage_p.add_argument("--memory-root", default=None)
    usage_p.add_argument("--since", default=None)
    usage_p.add_argument("--json", action="store_true")
    usage_p.set_defaults(func=_memory_usage)
    usage_sub = usage_p.add_subparsers(dest="usage_command")
    mark_applied_p = usage_sub.add_parser(
        "mark-applied", help="記錄 applied 顯式訊號（agent structured acknowledgement，契約 8）"
    )
    mark_applied_p.add_argument("--memory-root", required=True)
    mark_applied_p.add_argument("--session-id", required=True)
    mark_applied_p.add_argument("--slice-id", required=True)
    mark_applied_p.add_argument("--tool", required=True)
    mark_applied_p.set_defaults(func=_usage_mark_applied)

    locks_p = memory_subparsers.add_parser("locks", help="runtime lock 維運")
    locks_sub = locks_p.add_subparsers(dest="locks_command", required=True)
    locks_cleanup = locks_sub.add_parser(
        "cleanup-legacy",
        help="一次性清理 legacy per-session lock 檔（僅維護窗口；預設 dry-run）",
    )
    locks_cleanup.add_argument("--memory-root", required=True)
    locks_cleanup.add_argument("--apply", action="store_true")
    locks_cleanup.set_defaults(func=_locks_cleanup_legacy)

    requeue_p = memory_subparsers.add_parser(
        "requeue", help="把 parked session 送回 split 重走 promote（#15 恢復路徑）"
    )
    requeue_p.add_argument("session_key", nargs="?", default=None,
                           help="session key（如 claude:s1）；與 --all-parked 擇一")
    requeue_p.add_argument("--all-parked", action="store_true",
                           help="requeue 全部 parked sessions")
    requeue_p.add_argument("--memory-root", required=True)
    requeue_p.add_argument("--reason", default="",
                           help="requeue 原因（記入 ledger requeue_reason）")
    requeue_p.add_argument("--now", default=None)
    requeue_p.set_defaults(func=_requeue)

    recall_p = memory_subparsers.add_parser(
        "recall", help="任務相關記憶 shortlist（跨 CLI consumer API；記 offered，含 tool 歸因）")
    recall_p.add_argument("--memory-root", default=str(paths.memory_root()))
    recall_p.add_argument("--cwd", default=None)
    recall_p.add_argument("--prompt", required=True)
    recall_p.add_argument("--tool", required=True, type=_tool_arg)
    recall_p.add_argument("--session-id", required=True)
    recall_p.set_defaults(func=_recall)

    return parser


def _dry_run_policy(args: argparse.Namespace) -> int:
    payload = _read_payload(args.payload_file)
    policy = _load_policy(args.override)
    result = _check(payload, session_ref=args.session_id, project_slug=args.project, policy=policy)
    summary = _summary(
        result,
        skipped_overrides=_skipped_overrides(
            payload,
            policy=policy,
            session_ref=args.session_id,
            boundary=BOUNDARY,
        ),
        override_path=args.override,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


def _replay(args: argparse.Namespace) -> int:
    payload = _read_payload(args.payload_file)
    policy = _load_policy(args.override)
    result = _check(payload, session_ref=args.session, project_slug=args.project, policy=policy)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_artifact(result), encoding="utf-8")
    _append_replay_audit(result, session_ref=args.session, audit_path=_replay_audit_path(out))
    summary = _summary(
        result,
        skipped_overrides=_skipped_overrides(
            payload,
            policy=policy,
            session_ref=args.session,
            boundary=BOUNDARY,
        ),
        override_path=args.override,
    )
    summary["out"] = str(out)
    print(json.dumps(summary, sort_keys=True))
    return 0


def _janitor_scan(args: argparse.Namespace) -> int:
    from .janitor import cli as janitor_cli
    return janitor_cli.run(args)


def _atomize(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from .atomizer.cli import run as atomize_run

    if args.now is None:
        args.now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return atomize_run(args)


def _dream(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from .dream.cli import run as dream_run

    if getattr(args, "now", None) is None:
        args.now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return dream_run(args)


def _skillopt(args: argparse.Namespace) -> int:
    from .skillopt import cli as skillopt_cli

    return skillopt_cli.run(args)


def _bundle(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from .replay.cli import run as bundle_run

    if args.now is None:
        args.now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return bundle_run(args)


def _search(args: argparse.Namespace) -> int:
    from .moc.cli import run as search_run

    return search_run(args)


def _index_verify(args: argparse.Namespace) -> int:
    from .moc.cli import run_index_verify

    return run_index_verify(args)


def _wakeup(args: argparse.Namespace) -> int:
    from .wakeup import cli as wakeup_cli

    return wakeup_cli.run(args)


def _syncback(args: argparse.Namespace) -> int:
    from .syncback import cli as syncback_cli

    return syncback_cli.run(args)


def _write_manifest(manifest: Path, rows: list[dict]) -> None:
    # Atomic replace so the manifest is never left half-written (#139 finding 2).
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp = manifest.with_name(f".{manifest.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(manifest)


def _prune_noise(args: argparse.Namespace) -> int:
    from .instruction_corpus import corpus_for_roots

    root = Path(args.memory_root)
    now = (args.now or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")
    apply = bool(getattr(args, "apply", False))
    paths_file = getattr(args, "paths", None)
    if paths_file:
        if getattr(args, "instruction_root", None) or getattr(args, "project", None):
            print("error: --paths 與 --instruction-root/--project 互斥", file=sys.stderr)
            return 2
        return _prune_listed(root, Path(paths_file), now=now, apply=apply)
    corpus = corpus_for_roots(getattr(args, "instruction_root", None))
    projects = getattr(args, "project", None)
    knowledge = root / "knowledge"

    # Phase 1: scan + classify only. No deletes yet — build the full candidate list.
    rows: list[dict] = []
    for path in sorted(knowledge.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, body = _fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            # Unreadable/non-UTF-8 slice: cannot classify, so never delete it. When a
            # project filter is set we cannot confirm scope, so skip rather than record.
            if projects:
                continue
            rows.append({"slice_id": "", "project": "", "path": str(path),
                         "reason": "unreadable", "status": "error", "error": str(exc)})
            continue
        if fm.get("memory_layer") != "knowledge":
            continue
        if projects and str(fm.get("project", "")) not in projects:
            continue
        verdict = classify_noise(fm, body, doc_corpus=corpus)
        if not verdict.is_noise:
            continue
        rows.append({"slice_id": str(fm.get("slice_id", "")), "project": str(fm.get("project", "")),
                     "path": str(path), "reason": verdict.reason,
                     "status": "planned" if apply else "dry-run"})

    ledger_dir = root / "runtime" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    safe_now = now.replace(":", "")  # strip ':' for filesystem-safe filename; Z-normalized so no '+'
    manifest = ledger_dir / f"prune-{safe_now}.jsonl"

    # Phase 2: persist the planned manifest BEFORE any unlink, so a later failure can
    # never leave deletes without a durable audit record (#139 finding 2).
    _write_manifest(manifest, rows)

    # Phase 3: delete, updating each row's status, then atomically rewrite the manifest.
    if apply:
        deleted = False
        for row in rows:
            if row["status"] != "planned":
                continue
            try:
                Path(row["path"]).unlink()
                row["status"] = "deleted"
                deleted = True
            except OSError as exc:
                row["status"] = "error"
                row["error"] = str(exc)
        _write_manifest(manifest, rows)
        if deleted:
            _moc_builder.build_mocs(root, now=now)

    stats = Counter(r["reason"] for r in rows)
    print(json.dumps({"scanned_noise": len(rows), "applied": apply, "by_reason": dict(stats),
                      "manifest": str(manifest)}, ensure_ascii=False))
    return 0


def _prune_listed(root: Path, paths_file: Path, *, now: str, apply: bool) -> int:
    knowledge = (root / "knowledge").resolve()
    try:
        raw_lines = paths_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read --paths file: {exc}", file=sys.stderr)
        return 2

    listed = [line.strip() for line in raw_lines if line.strip() and not line.strip().startswith("#")]
    if not listed:
        print("error: --paths file is empty", file=sys.stderr)
        return 2

    rows: list[dict] = []
    problems: list[str] = []
    seen_resolved: set[Path] = set()
    for entry in listed:
        raw_path = Path(entry)
        if not raw_path.is_absolute():
            problems.append(f"not-absolute: {entry}")
            continue
        if raw_path.is_symlink():
            problems.append(f"symlink-not-allowed: {entry}")
            continue
        try:
            resolved = raw_path.resolve(strict=True)
        except OSError:
            problems.append(f"missing: {entry}")
            continue
        if not resolved.is_file() or resolved.suffix != ".md" or resolved.name.endswith("-moc.md"):
            problems.append(f"not-a-slice: {entry}")
            continue
        if knowledge not in resolved.parents:
            problems.append(f"outside-knowledge-root: {entry}")
            continue
        try:
            fm, _body = _fio.read(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"unreadable: {entry}: {exc}")
            continue
        if fm.get("memory_layer") != "knowledge":
            problems.append(f"not-knowledge-layer: {entry}")
            continue
        if resolved in seen_resolved:
            problems.append(f"duplicate: {entry}")
            continue
        seen_resolved.add(resolved)
        rows.append(
            {
                "slice_id": str(fm.get("slice_id", "")),
                "project": str(fm.get("project", "")),
                "path": str(resolved),
                "reason": "listed",
                "status": "planned" if apply else "dry-run",
            }
        )

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2

    ledger_dir = root / "runtime" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    manifest = ledger_dir / f"prune-{now.replace(':', '')}.jsonl"
    _write_manifest(manifest, rows)

    if apply:
        deleted = False
        for row in rows:
            try:
                Path(row["path"]).unlink()
                row["status"] = "deleted"
                deleted = True
            except OSError as exc:
                row["status"] = "error"
                row["error"] = str(exc)
        _write_manifest(manifest, rows)
        if deleted:
            _moc_builder.build_mocs(root, now=now)

    stats = Counter(row["reason"] for row in rows)
    status_counts = Counter(row["status"] for row in rows)
    print(
        json.dumps(
            {
                "scanned_noise": len(rows),
                "applied": apply,
                "mode": "listed",
                "by_reason": dict(stats),
                "deleted": status_counts.get("deleted", 0),
                "errors": status_counts.get("error", 0),
                "manifest": str(manifest),
            },
            ensure_ascii=False,
        )
    )
    if apply and status_counts.get("error", 0):
        return 1
    return 0


def _retitle_untitled(args: argparse.Namespace) -> int:
    from . import retitle as retitle_mod
    from .importer.title import generate_atom_title

    from .instruction_corpus import corpus_for_roots

    root = Path(args.memory_root)
    now = (args.now or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")
    apply = bool(getattr(args, "apply", False))
    corpus = corpus_for_roots(getattr(args, "instruction_root", None))

    command = getattr(args, "agent_command", None)
    title_kwargs = {"command": tuple(command.split())} if command else {}

    def distill(body: str):
        title, _source = generate_atom_title(body, **title_kwargs)
        return title

    summary = retitle_mod.retitle_untitled(
        root, now=now, apply=apply, distill=distill, doc_corpus=corpus,
        projects=getattr(args, "project", None))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _rekey(args: argparse.Namespace) -> int:
    from . import rekey as rekey_mod

    root = Path(args.memory_root)
    now = (args.now or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")
    apply = bool(getattr(args, "apply", False))
    try:
        summary = rekey_mod.rekey_project(
            root,
            old_key=args.from_key,
            new_slug=args.to_slug,
            now=now,
            apply=apply,
        )
    except rekey_mod.RekeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for warning in summary.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False))
    if summary.get("errors", 0):
        return 1
    if summary.get("indexed") is False:
        return 1
    return 0


def _memory_usage(args: argparse.Namespace) -> int:
    from collections import defaultdict

    if not args.memory_root:
        print("hippo usage: error: --memory-root is required", file=sys.stderr)
        return 2

    root = Path(args.memory_root)
    led = root / "runtime" / "ledger"

    def _read_jsonl(p):
        out = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if args.since and str(e.get("ts", "")) < args.since:
                    continue
                out.append(e)
        return out

    offered_rows = _read_jsonl(led / "offered.jsonl")
    usage_rows = _read_jsonl(led / "memory_usage.jsonl")
    used_rows = [e for e in usage_rows if e.get("source") == "read"]
    applied_rows = [e for e in usage_rows if e.get("kind") == "applied"]

    agg = defaultdict(lambda: {"offered_count": 0, "read_count": 0, "last_read": ""})
    sessions = set()
    for e in offered_rows:
        sessions.add(e.get("session_id"))
        for o in e.get("offered", []):
            sid = o.get("sl_id") if isinstance(o, dict) else o
            if sid:
                agg[sid]["offered_count"] += 1
    for e in used_rows:
        # Count the session even when the read was not from an offered/attributable
        # slice, so avg_reads_per_session is not skewed by offered-only session counting.
        sessions.add(e.get("session_id"))
        sid = e.get("sl_id") or "(unattributed)"
        ts = str(e.get("ts", ""))
        agg[sid]["read_count"] += 1
        if ts > agg[sid]["last_read"]:
            agg[sid]["last_read"] = ts

    def _tool_key(e) -> str:
        return str(e.get("tool") or "(unknown)")

    by_tool: dict[str, dict] = {}
    for e in offered_rows:
        t = by_tool.setdefault(_tool_key(e), {"offered": 0, "read": 0, "applied": 0})
        t["offered"] += len(e.get("offered", []))
    for e in used_rows:
        t = by_tool.setdefault(_tool_key(e), {"offered": 0, "read": 0, "applied": 0})
        t["read"] += 1
    applied_tools: set[str] = set()
    for e in applied_rows:
        t = by_tool.setdefault(_tool_key(e), {"offered": 0, "read": 0, "applied": 0})
        t["applied"] += 1
        applied_tools.add(_tool_key(e))
    for name, t in by_tool.items():
        if name not in applied_tools:
            t["applied"] = None  # 該 tool 無任何 applied 訊號 → n/a（不以內容猜測補值）

    slices = [{"slice_id": sid, **v} for sid, v in agg.items()]
    slices.sort(key=lambda s: (s["read_count"], s["offered_count"]), reverse=True)
    never_read = sum(1 for s in slices if s["offered_count"] > 0 and s["read_count"] == 0)
    n = len(sessions)
    total_reads = len(used_rows)
    summary = {
        "sessions": n, "slices": len(slices), "never_read": never_read,
        "total_reads": total_reads,
        "avg_reads_per_session": round(total_reads / n, 3) if n else 0.0,
    }
    report = {"summary": summary,
              "by_tool": {k: by_tool[k] for k in sorted(by_tool)},
              "slices": slices}

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"sessions={summary['sessions']} slices={summary['slices']} "
              f"never_read={summary['never_read']} total_reads={summary['total_reads']} "
              f"avg_reads/session={summary['avg_reads_per_session']}")
        for name in sorted(by_tool):
            t = by_tool[name]
            applied_disp = "n/a" if t["applied"] is None else str(t["applied"])
            print(f"  tool={name} offered={t['offered']} read={t['read']} applied={applied_disp}")
        for s in slices[:30]:
            print(f"  {s['slice_id']}  offered={s['offered_count']} "
                  f"read={s['read_count']} last_read={s['last_read']}")
    return 0


def _recall(args: argparse.Namespace) -> int:
    """跨 CLI consumer API：重用 prompt-time shortlist 管線（best-effort，恆 exit 0）。"""
    from .hooks._shortlist_common import build_shortlist_and_record

    block = build_shortlist_and_record(
        Path(args.memory_root), args.tool, args.session_id, args.cwd, args.prompt)
    if block:
        print(block)
    return 0


def _usage_mark_applied(args: argparse.Namespace) -> int:
    """applied 顯式訊號（契約 8）：agent 主動回報某條記憶實際影響了做法。

    寫入前反查 offered.jsonl：同 (session_id, tool) 必須存在先行 offered 記錄，且
    slice_id 必須屬於那些 offered slices，否則 exit 1 並拒絕寫入偽造事件。
    """
    led_dir = Path(args.memory_root) / "runtime" / "ledger"
    session_seen = False
    offered_slices: set[str] = set()
    offered_path = led_dir / "offered.jsonl"
    if offered_path.exists():
        for line in offered_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("session_id") != args.session_id or e.get("tool") != args.tool:
                continue
            session_seen = True
            for offered in e.get("offered", []):
                sid = offered.get("sl_id") if isinstance(offered, dict) else offered
                if sid:
                    offered_slices.add(str(sid))
    if not session_seen:
        print(
            f"hippo usage mark-applied: error: 查無 (session_id={args.session_id}, "
            f"tool={args.tool}) 的先行 offered 記錄——拒絕寫入（applied 只能回報真實被 offer 的記憶）",
            file=sys.stderr,
        )
        return 1
    if args.slice_id not in offered_slices:
        print(
            f"hippo usage mark-applied: error: slice_id={args.slice_id} 不在 "
            f"(session_id={args.session_id}, tool={args.tool}) 的 offered slice 集合內——拒絕寫入",
            file=sys.stderr,
        )
        return 1
    ev = {
        "kind": "applied",
        "session_id": args.session_id,
        "slice_id": args.slice_id,
        "tool": args.tool,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    led_dir.mkdir(parents=True, exist_ok=True)
    with (led_dir / "memory_usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    print(json.dumps(ev, ensure_ascii=False))
    return 0


def _load_policy(override_path: str | None):
    if override_path is None:
        return memory_policy.load_policy()
    return memory_policy.load_policy(override_path=override_path)


def _read_payload(payload_file: str) -> str:
    path = Path(payload_file)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadReadError(f"cannot read payload file {path!s}: {exc}") from None
    except OSError as exc:
        raise PayloadReadError(f"cannot read payload file {path!s}: {exc}") from None


def _check(text: str, *, session_ref: str, project_slug: str, policy):
    return memory_policy.check_boundary(
        BOUNDARY,
        text,
        project_slug=project_slug,
        session_ref=session_ref,
        policy=policy,
    )


def _summary(result, *, skipped_overrides: list[dict[str, object]], override_path: str | None) -> dict[str, object]:
    metadata = dict(result.ledger_metadata)
    metadata.update(
        {
            "boundary": BOUNDARY,
            "hits": [_hit_summary(hit, BOUNDARY) for hit in result.hits],
            "policy_version": result.policy.policy_version,
            "effective_policy_hash": result.policy.effective_policy_hash,
            "skipped_overrides": skipped_overrides,
            "override_path": str(override_path) if override_path else None,
        }
    )
    return metadata


def _hit_summary(hit, boundary: str) -> dict[str, object]:
    return {
        "rule_id": hit.rule_id,
        "detector": hit.detector,
        "line_no": hit.line_no,
        "action": hit.action,
        "boundary": boundary,
    }


def _skipped_overrides(text: str, *, policy, session_ref: str, boundary: str) -> list[dict[str, object]]:
    skipped: list[dict[str, object]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule in policy.secret_rules.values():
            if rule.detector != "regex" or not memory_policy.is_rule_disabled(policy, rule.rule_id, session_ref):
                continue
            if re.search(rule.pattern, line):
                skipped.append(
                    {
                        "rule_id": rule.rule_id,
                        "detector": rule.detector,
                        "line_no": line_no,
                        "action": "skipped",
                        "boundary": boundary,
                    }
                )
    return skipped


def _artifact(result) -> str:
    classification = result.classification
    return "".join(
        (
            "---\n",
            f"classification_level: {_yaml_scalar(classification.level)}\n",
            f"classification_reason: {_yaml_scalar(classification.reason)}\n",
            f"classification_policy_hash: {_yaml_scalar(classification.policy_hash)}\n",
            f"classification_source: {_yaml_scalar(classification.source)}\n",
            "---\n\n",
            result.text,
        )
    )


def _yaml_scalar(value: str) -> str:
    if (
        not value
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or ": " in value
        or "#" in value
    ):
        return json.dumps(value)
    return value


def _append_replay_audit(result, *, session_ref: str, audit_path: Path) -> None:
    boundary_policy = result.policy.boundaries.get(BOUNDARY)
    if boundary_policy is None or not boundary_policy.audit_required:
        return
    memory_policy.append_policy_audits(
        audit_path,
        memory_policy.build_policy_audit_events(
            boundary=BOUNDARY,
            component=str(result.ledger_metadata["redaction_stage"]),
            session_ref=session_ref,
            policy=result.policy,
            hits=result.hits,
        ),
    )


def _replay_audit_path(out: Path) -> Path:
    return out.with_name(f"{out.stem}.policy-audit.jsonl")


def _ops_init(args) -> int:
    from paulsha_hippo import ops

    return ops.run_init(
        memory_root=args.memory_root,
        backend=args.backend,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        model=args.model,
        assume_yes=args.yes,
    )


def _ops_doctor(args) -> int:
    from paulsha_hippo import ops

    return ops.run_doctor(
        fix_backend=getattr(args, "fix_backend", False),
        live_probe=getattr(args, "probe_live", False),
    )


def _ops_install_hooks(args) -> int:
    from paulsha_hippo import ops

    return ops.run_install_hooks(memory_root=args.memory_root, repo_root=args.repo_root)


def _ops_install_service(args) -> int:
    from paulsha_hippo import ops

    return ops.run_install_service(enable=args.enable)


def _requeue(args: argparse.Namespace) -> int:
    from . import requeue as requeue_mod

    if bool(args.session_key) == bool(args.all_parked):
        print("error: 需指定 <session-key> 或 --all-parked（擇一）", file=sys.stderr)
        return 2
    root = Path(args.memory_root)
    now = (args.now or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")
    summary = requeue_mod.requeue(
        root,
        session_key=args.session_key,
        all_parked=args.all_parked,
        now=now,
        reason=args.reason,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    # Codex 複驗 B2：無「有效且屬於該 session」fragment 的 parked session 被 gate
    # 擋下（維持 parked）時必須非零 exit＋stderr 說明——早前 exit 0 會把「沒東西
    # 可重走」誤報成功。有效 = pipeline `_read_fragment` 讀得動且 frontmatter 相符。
    no_valid_entries = [
        entry
        for entry in summary["skipped"]
        if entry.get("reason") == "no-valid-fragments"
    ]
    for entry in no_valid_entries:
        print(
            f"error: {entry['session_key']} 無有效 fragment（inbox 的 _slices 下"
            "無 frontmatter 完整（project／source_session）且屬於該 session 的 "
            "fragment 檔）——維持 parked 未 requeue；送回 split 會永久卡非終態",
            file=sys.stderr,
        )
    if no_valid_entries:
        return 1
    if not summary["requeued"] and summary["skipped"]:
        return 1
    return 0


def _dream_supervise(args) -> int:
    from paulsha_hippo import ops

    extra = ["--memory-root", args.memory_root] if args.memory_root else []
    return ops.run_dream_supervise(interval=args.interval, extra_argv=extra)


def _locks_cleanup_legacy(args: argparse.Namespace) -> int:
    from paulsha_hippo import ops

    result = ops.cleanup_legacy_locks(Path(args.memory_root), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if (result.get("blocked") or result.get("busy")
            or result.get("unknown") or result.get("unsafe_locks_dir")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
