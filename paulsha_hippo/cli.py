from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from paulsha_hippo import paths
from . import policy as memory_policy
from .ledger import usage as usage_ledger
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
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in raw_argv and "--json" in raw_argv:
        from .build_info import version_json

        print(version_json())
        return 0
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

    from paulsha_hippo import backends as hippo_backends

    init_p = memory_subparsers.add_parser("init", help="初始化 config 與蒸餾 backend")
    init_p.add_argument("--memory-root")
    _backend_help = "蒸餾 backend preset：" + "、".join(
        name + ("（尚不可用）" if not preset.available else "")
        for name, preset in hippo_backends.PRESETS.items()
    )
    init_p.add_argument("--backend", default="claude-headless",
                        choices=list(hippo_backends.PRESETS), help=_backend_help)
    init_p.add_argument("--model")
    init_p.add_argument("--yes", action="store_true")
    init_p.set_defaults(func=_ops_init)

    doctor_p = memory_subparsers.add_parser("doctor", help="健檢：路徑契約/hooks/服務/backend")
    doctor_p.add_argument(
        "--fix-backend", action="store_true",
        help="冪等遷移：canonical config 中 enabled profile 的裸命令改寫為絕對路徑"
             "（先備份）；隱含 --probe-live 以真實 smoke probe 驗證遷移結果")
    doctor_p.add_argument(
        "--probe-live", action="store_true",
        help="對 configured backend 實際送一次 bounded smoke prompt（真實喚起，60s timeout、"
             "可能產生 API 成本；亦可 HIPPO_DOCTOR_LIVE_PROBE=1）。預設僅做解析檢查")
    doctor_p.add_argument(
        "--probe-profiles", action="store_true",
        help="逐一實跑所有 enabled Dream external profiles；任一失敗即 fail closed（可能產生成本）",
    )
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
    install_all = install_sub.add_parser(
        "all", help="依 ownership manifest 安全更新 Hippo-owned release surfaces"
    )
    install_all.add_argument("--force", action="store_true", help="允許 manifest 證明的 owned changes")
    install_all.add_argument("--dry-run", action="store_true", help="只輸出 plan，不落盤")
    install_all.add_argument("--manifest", default=None)
    install_all.add_argument("--target-root", default=None)
    install_all.add_argument("--transaction-root", default=None)
    install_all.add_argument(
        "--runtime-plan",
        default=None,
        help="reviewed JSON writer/service/rollback override; omit to use the packaged live plan",
    )
    install_all.set_defaults(func=_ops_install_all)

    upgrade = memory_subparsers.add_parser(
        "upgrade", help="isolated artifact upgrade transaction (service gates remain pending)"
    )
    upgrade_sub = upgrade.add_subparsers(dest="upgrade_command", required=True)
    upgrade_plan = upgrade_sub.add_parser("plan")
    upgrade_plan.add_argument("--candidate", required=True)
    upgrade_plan.add_argument("--target-root", required=True)
    upgrade_plan.add_argument("--profile-id", default="candidate")
    upgrade_plan.add_argument("--artifact-name", default="hippo.whl")
    upgrade_plan.add_argument(
        "--command-plan",
        default=None,
        help="reviewed JSON phase/rollback argv plan; omit for dry-run skeleton",
    )
    upgrade_plan.add_argument("--out", required=True)
    upgrade_plan.set_defaults(func=_upgrade)
    upgrade_prepare = upgrade_sub.add_parser("prepare")
    upgrade_prepare.add_argument("--plan", required=True)
    upgrade_prepare.add_argument("--transaction-root", default=None)
    upgrade_prepare.set_defaults(func=_upgrade)
    upgrade_apply = upgrade_sub.add_parser("apply")
    upgrade_apply.add_argument("--manifest", required=True)
    upgrade_apply.add_argument("--force", action="store_true")
    upgrade_apply.add_argument("--dry-run", action="store_true")
    upgrade_apply.set_defaults(func=_upgrade)
    upgrade_rollback = upgrade_sub.add_parser("rollback")
    upgrade_rollback.add_argument("--manifest", required=True)
    upgrade_rollback.set_defaults(func=_upgrade)

    config = memory_subparsers.add_parser(
        "config", help="canonical runtime config migration and rollback"
    )
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_migrate = config_sub.add_parser(
        "migrate", help="plan/apply a hash-bound legacy config migration"
    )
    migrate_sub = config_migrate.add_subparsers(dest="migrate_command", required=True)
    migrate_plan = migrate_sub.add_parser("plan")
    migrate_plan.add_argument("--canonical", default=None)
    migrate_plan.add_argument("--legacy", default=None)
    migrate_plan.add_argument("--out", default=None)
    migrate_plan.set_defaults(func=_config_migrate)
    migrate_apply = migrate_sub.add_parser("apply")
    migrate_apply.add_argument("--plan", required=True)
    migrate_apply.add_argument("--resolution", default=None)
    migrate_apply.add_argument("--dry-run", action="store_true")
    migrate_apply.add_argument("--out", default=None)
    migrate_apply.set_defaults(func=_config_migrate)
    migrate_rollback = migrate_sub.add_parser("rollback")
    migrate_rollback.add_argument("--report", required=True)
    migrate_rollback.set_defaults(func=_config_migrate)

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
    atomize.add_argument("--promoter", choices=["identity", "llm"], default=None)
    atomize.add_argument(
        "--instruction-root", action="append", default=None,
        help="agent-instruction doc root/file; when given, drops doc-fragment slices "
             "(verbatim instruction-doc sections) at produce time. Repeatable.")
    atomize.add_argument("--dry-run", action="store_true")
    atomize.set_defaults(func=_atomize)

    recovery = memory_subparsers.add_parser(
        "recovery", help="hash-pinned importer recovery（不自動重播 LLM）"
    )
    recovery_subparsers = recovery.add_subparsers(dest="recovery_command", required=True)
    recovery_plan = recovery_subparsers.add_parser("plan")
    recovery_plan.add_argument("--memory-root", required=True)
    recovery_plan.add_argument("--manifest", default=None)
    recovery_plan.add_argument(
        "--source-manifest",
        default=None,
        help="沿用既有 recovery manifest 的精確 frozen source set，並以目前 candidate 重建 pins",
    )
    recovery_plan.add_argument("--batch-size", type=int, default=5)
    recovery_plan.add_argument("--baseline-count", type=int, default=None)
    recovery_plan.set_defaults(func=_recovery)
    for command in ("apply", "resume", "rollback"):
        recovery_action = recovery_subparsers.add_parser(command)
        recovery_action.add_argument("--manifest", required=True)
        recovery_action.set_defaults(func=_recovery)

    dream = memory_subparsers.add_parser("dream")
    dream_subparsers = dream.add_subparsers(dest="dream_command", required=True)
    dream_run = dream_subparsers.add_parser("run")
    dream_run.add_argument("--memory-root", required=True)
    dream_run.add_argument("--now", default=None)
    dream_run.add_argument("--dry-run", action="store_true")
    dream_run.add_argument("--require-idle", action="store_true")
    dream_run.add_argument(
        "--max-load", type=float, default=4.0,
        help="--require-idle 的 1 分鐘 loadavg 上限（預設 4.0）。舊預設 1.0 在"
             "多核機器上過嚴——20 核機器上等於只准 5%% 總負載，實測近 5 天內"
             "29%% 有新進料的時段因此被誤判為忙碌而整輪跳過，閘門專打有工作"
             "發生的時段，與服務目的相反；cgroup CPUWeight/MemoryHigh 已是"
             "第二層資源保護。")
    dream_run.add_argument("--min-avail-mem-pct", type=_pct_arg, default=20.0)
    dream_run.add_argument("--promoter", choices=["identity", "llm"], default=None)
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
    dream_supervise.add_argument("--once", action="store_true",
                                 help="只跑一輪就結束（無 systemd 主機的單輪驗收，#10）")
    dream_supervise.add_argument("--max-load", type=float, default=None,
                                 help="透傳 dream run --max-load（覆蓋內建 4.0）")
    dream_supervise.add_argument("--promoter", choices=["identity", "llm"], default=None,
                                 help="透傳 dream run --promoter（覆蓋內建 llm）")
    dream_supervise.set_defaults(func=_dream_supervise)

    dream_status = dream_subparsers.add_parser("status")
    dream_status.add_argument("--memory-root", required=True)
    dream_status.set_defaults(func=_dream)

    dream_reconcile = dream_subparsers.add_parser(
        "reconcile",
        help="對賬 _slices 與 processing ledger（診斷 / 修復積壓）",
    )
    dream_reconcile.add_argument("--memory-root", required=True)
    dream_reconcile.add_argument("--now", default=None)
    reconcile_mode = dream_reconcile.add_mutually_exclusive_group()
    reconcile_mode.add_argument("--dry-run", action="store_true",
                                 help="只產出報告（預設行為）")
    reconcile_mode.add_argument("--apply", action="store_true",
                                help="執行修復")
    dream_reconcile.add_argument("--limit", type=int, default=None,
                                 help="每類最多處理 N 個 session")
    dream_reconcile.set_defaults(func=_dream)

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

    entity_hubs_p = knowledge_subparsers.add_parser(
        "entity-hubs",
        help="entity hub 同步：mentions 物化斷鏈的常態維護（#107）。"
             "dry-run（預設）只回報缺頁/待刷清單，有待辦即 exit 1 供排程檢查。")
    entity_hubs_p.add_argument("--memory-root", required=True)
    entity_hubs_p.add_argument("--now", default=None)
    egroup = entity_hubs_p.add_mutually_exclusive_group()
    egroup.add_argument("--dry-run", action="store_true")
    egroup.add_argument("--apply", action="store_true")
    entity_hubs_p.set_defaults(func=_entity_hubs)

    usage_p = memory_subparsers.add_parser("usage")
    # Let argparse accept `hippo usage mark-applied --memory-root ...`; the report path
    # still errors with exit 2 when the flag is omitted.
    usage_p.add_argument("--memory-root", default=None)
    usage_p.add_argument("--since", default=None)
    usage_p.add_argument("--json", action="store_true")
    usage_p.set_defaults(func=_memory_usage)
    usage_sub = usage_p.add_subparsers(dest="usage_command")
    funnel_p = usage_sub.add_parser(
        "funnel", help="session 層級 offered→read→applied 漏斗報表"
    )
    funnel_p.add_argument("--memory-root", required=True)
    funnel_p.add_argument("--since", default=None)
    funnel_p.add_argument("--json", action="store_true")
    noise_mode = funnel_p.add_mutually_exclusive_group()
    noise_mode.add_argument(
        "--exclude-noise",
        dest="exclude_noise",
        action="store_true",
        help="將 processing ledger 最終 state=no-findings 的 session 排除於主指標；仍輸出兩組數字",
    )
    noise_mode.add_argument(
        "--include-noise",
        dest="exclude_noise",
        action="store_false",
        help="以含 no-findings 噪音的 session 組作為主指標；仍輸出兩組數字",
    )
    funnel_p.set_defaults(func=_memory_usage_funnel, exclude_noise=True)
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

    ledger_p = memory_subparsers.add_parser("ledger", help="append-only ledger 維運")
    ledger_sub = ledger_p.add_subparsers(dest="ledger_command", required=True)
    ledger_repair = ledger_sub.add_parser(
        "repair",
        help="修復 ledger 撕裂行（僅維護窗口；預設 dry-run）",
    )
    ledger_repair.add_argument("--memory-root", required=True)
    ledger_repair.add_argument("--apply", action="store_true")
    ledger_repair.add_argument("--now", default=None,
                               help="ISO8601 時間戳；未給時取當下 UTC")
    ledger_repair.set_defaults(func=_ledger_repair)

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


def _recovery(args: argparse.Namespace) -> int:
    from . import recovery

    if args.recovery_command == "plan":
        manifest = recovery.create_plan(
            args.memory_root,
            manifest_path=args.manifest,
            source_manifest_path=args.source_manifest,
            batch_size=args.batch_size,
            baseline_count=args.baseline_count,
        )
        print(json.dumps({"manifest": str(manifest)}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.recovery_command == "rollback":
        result = recovery.rollback_plan(args.manifest)
    else:
        result = recovery.apply_plan(
            args.manifest,
            resume=args.recovery_command == "resume",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


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

    def distill(body: str):
        title, _source = generate_atom_title(body)
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


def _entity_hubs(args: argparse.Namespace) -> int:
    """`hippo knowledge entity-hubs`：同步（或 dry-run 檢查）entity hub 層。

    dry-run（預設）不寫檔，輸出待建/待刷動作清單；有待辦動作時 exit 1，
    供排程當健康檢查用（比照 `hippo index verify` 的 exit 語意）。
    """
    from .moc import entity_hub

    root = Path(args.memory_root)
    now = (args.now or datetime.now(timezone.utc).isoformat()).replace("+00:00", "Z")
    apply = bool(getattr(args, "apply", False))
    stats, warnings = entity_hub.sync_entity_hubs(root, now, apply=apply)
    actions = stats.get("actions", [])
    print(json.dumps({
        "mode": "apply" if apply else "dry-run",
        "stats": {k: v for k, v in stats.items() if k not in ("actions", "structural")},
        "structural": stats.get("structural", []),
        "actions": actions,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    # 比照 _rekey/_prune_listed：apply 失敗（I/O warnings）以 exit code 回報；
    # dry-run 有待辦動作即 1（健檢語意）。結構性跳過只入 JSON，不影響 exit。
    if warnings:
        return 1
    if not apply and actions:
        return 1
    return 0


def _read_usage_jsonl(path: Path, since: str | None,
                       diagnostics: dict[str, int] | None = None) -> Iterator[dict]:
    """Read one usage ledger without changing it, using the existing since rule.

    v5 BLOCKER #1/#4: streams the ledger one line at a time via
    ``ledger.usage.iter_ledger_events`` (no ``Path.read_text().splitlines()``,
    no whole-file list materialization); I/O, UTF-8 and per-line parse errors
    are fail-soft and never mutate the ledger. Malformed/non-object lines are
    tallied into ``diagnostics`` (v5 BLOCKER #2) when the caller supplies a
    bounded counter dict; otherwise a scratch one is used and discarded.
    """
    diag = diagnostics if diagnostics is not None else usage_ledger.new_diagnostics()
    for event in usage_ledger.iter_ledger_events(path, diag):
        if since and str(event.get("ts", "")) < since:
            continue
        yield event


def _load_usage_rows(
    root: Path, since: str | None,
) -> tuple[Iterator[dict], Iterator[dict], dict[str, int]]:
    """Return one-shot offered/usage iterators plus bounded parse diagnostics.

    Raw ledger rows remain streaming. Callers fold the iterators into compact
    per-session/per-slice state instead of retaining ledger-wide row lists.
    """
    led = root / "runtime" / "ledger"
    diagnostics = usage_ledger.new_diagnostics()
    offered_rows = _read_usage_jsonl(led / "offered.jsonl", since, diagnostics)
    usage_rows = _read_usage_jsonl(led / "memory_usage.jsonl", since, diagnostics)
    return offered_rows, usage_rows, diagnostics


def _offered_items(event: dict) -> list:
    offered = event.get("offered")
    return offered if isinstance(offered, list) else []


def _offered_slice_ids(event: dict) -> list[str]:
    """Normalize both legacy string and current object offered entries."""
    slice_ids = []
    for item in _offered_items(event):
        slice_id = item.get("sl_id") if isinstance(item, dict) else item
        if slice_id:
            slice_ids.append(str(slice_id))
    return slice_ids


def _usage_tool_key(event: dict) -> str:
    return str(event.get("tool") or "(unknown)")


def _usage_session_key(event: dict) -> str:
    value = event.get("session_id")
    return str(value) if value not in (None, "") else ""


def _memory_usage(args: argparse.Namespace) -> int:
    from collections import defaultdict

    if not args.memory_root:
        print("hippo usage: error: --memory-root is required", file=sys.stderr)
        return 2

    root = Path(args.memory_root)
    offered_rows, usage_rows, _load_diagnostics = _load_usage_rows(root, args.since)

    agg = defaultdict(lambda: {"offered_count": 0, "read_count": 0, "last_read": ""})
    sessions = set()
    by_tool: dict[str, dict] = {}
    for e in offered_rows:
        sessions.add(e.get("session_id"))
        offered_items = _offered_items(e)
        for sid in _offered_slice_ids(e):
            agg[sid]["offered_count"] += 1
        t = by_tool.setdefault(_usage_tool_key(e), {"offered": 0, "read": 0, "applied": 0})
        t["offered"] += len(offered_items)

    applied_tools: set[str] = set()
    total_reads = 0
    for e in usage_rows:
        tool = _usage_tool_key(e)
        if e.get("kind") == "applied":
            t = by_tool.setdefault(tool, {"offered": 0, "read": 0, "applied": 0})
            t["applied"] += 1
            applied_tools.add(tool)
            continue
        if e.get("source") != "read":
            continue
        t = by_tool.setdefault(tool, {"offered": 0, "read": 0, "applied": 0})
        # Count the session even when the read was not from an offered/attributable
        # slice, so avg_reads_per_session is not skewed by offered-only session counting.
        sessions.add(e.get("session_id"))
        sid = e.get("sl_id") or "(unattributed)"
        ts = str(e.get("ts", ""))
        agg[sid]["read_count"] += 1
        if ts > agg[sid]["last_read"]:
            agg[sid]["last_read"] = ts
        t["read"] += 1
        total_reads += 1
    for name, t in by_tool.items():
        if name not in applied_tools:
            t["applied"] = None  # 該 tool 無任何 applied 訊號 → n/a（不以內容猜測補值）

    slices = [{"slice_id": sid, **v} for sid, v in agg.items()]
    slices.sort(key=lambda s: (s["read_count"], s["offered_count"]), reverse=True)
    never_read = sum(1 for s in slices if s["offered_count"] > 0 and s["read_count"] == 0)
    n = len(sessions)
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


_FUNNEL_TOOLS = ("claude-code", "codex", "copilot-cli")
_FUNNEL_TOP_N = 30


def _funnel_metrics(
    offered_sessions: set[str],
    read_sessions: set[str],
    applied_sessions: set[str],
) -> dict[str, float | int]:
    offered = len(offered_sessions)
    with_read = len(offered_sessions & read_sessions)
    with_applied = len(offered_sessions & applied_sessions)

    def _rate(count: int) -> float:
        return round(count * 100 / offered, 2) if offered else 0.0

    return {
        "sessions_offered": offered,
        "sessions_with_read": with_read,
        "read_through_rate": _rate(with_read),
        "sessions_with_applied": with_applied,
        "applied_rate": _rate(with_applied),
    }


def _funnel_coverage(offered: set[str], read: set[str]) -> dict[str, int | float]:
    offered_count = len(offered)
    read_count = len(read)
    return {
        "offered": offered_count,
        "read": read_count,
        "read_rate": round(read_count * 100 / offered_count, 2)
        if offered_count
        else 0.0,
    }


def _usage_logical_session_key(event: dict) -> str:
    session_id = _usage_session_key(event)
    if not session_id:
        return ""
    return f"{_usage_tool_key(event)}:{session_id}"


def _funnel_offer_index(
    offered_rows: Iterator[dict],
) -> dict[str, object]:
    """Fold offered rows into compact per-session/per-slice state."""
    offered_sessions: set[str] = set()
    offered_by_tool: dict[str, set[str]] = {}
    offered_slices: dict[str, dict[str, str]] = {}
    offered_counts: dict[str, dict[str, int]] = {}
    offered_unique: dict[str, dict[str, set[str]]] = {}

    for event in offered_rows:
        session_key = _usage_logical_session_key(event)
        if not session_key or not _offered_items(event):
            continue
        tool = _usage_tool_key(event)
        offered_sessions.add(session_key)
        offered_by_tool.setdefault(tool, set()).add(session_key)
        session_slices = offered_slices.setdefault(session_key, {})
        session_counts = offered_counts.setdefault(session_key, {})
        tool_slices = offered_unique.setdefault(session_key, {}).setdefault(tool, set())
        offered_ts = str(event.get("ts") or "")
        for slice_id in _offered_slice_ids(event):
            session_counts[slice_id] = session_counts.get(slice_id, 0) + 1
            tool_slices.add(slice_id)
            prior = session_slices.get(slice_id)
            if offered_ts and (not prior or offered_ts < prior):
                session_slices[slice_id] = offered_ts
            elif prior is None:
                session_slices[slice_id] = ""

    return {
        "offered_sessions": offered_sessions,
        "offered_by_tool": offered_by_tool,
        "offered_slices": offered_slices,
        "offered_counts": offered_counts,
        "offered_unique": offered_unique,
    }


def _funnel_read_attribution(
    usage_rows: Iterator[dict],
    offered_slices: dict[str, dict[str, str]],
) -> dict[str, object]:
    """Fold read/applied rows into compact attribution state.

    A read is attributable only when the same logical session was offered the
    same slice and the read timestamp is strictly later than an offer
    timestamp.  Missing timestamps are conservatively treated as direct reads
    because the ledger cannot prove the required ordering.
    """
    attributed_event_count = 0
    direct_event_count = 0
    attributed_sessions: set[str] = set()
    direct_sessions: set[str] = set()
    read_by_tool: dict[str, set[str]] = {}
    by_tool: dict[str, dict[str, int | set[str]]] = {}
    attributed_counts: dict[str, dict[str, int]] = {}
    read_unique: dict[str, dict[str, set[str]]] = {}
    applied_sessions: set[str] = set()
    applied_by_tool: dict[str, set[str]] = {}

    for event in usage_rows:
        session_key = _usage_logical_session_key(event)
        tool = _usage_tool_key(event)
        if event.get("kind") == "applied":
            if session_key:
                applied_sessions.add(session_key)
                applied_by_tool.setdefault(tool, set()).add(session_key)
            continue
        if event.get("source") != "read":
            continue
        slice_id = str(event.get("sl_id") or "")
        read_ts = str(event.get("ts") or "")
        offer_ts = offered_slices.get(session_key, {}).get(slice_id, "")
        attributed = bool(read_ts and offer_ts and read_ts > offer_ts)

        stats = by_tool.setdefault(
            tool,
            {
                "offer_then_read_events": 0,
                "offer_then_read_sessions": set(),
                "direct_read_events": 0,
                "direct_read_sessions": set(),
            },
        )
        if attributed:
            attributed_event_count += 1
            stats["offer_then_read_events"] += 1
            if session_key:
                attributed_sessions.add(session_key)
                read_by_tool.setdefault(tool, set()).add(session_key)
                stats["offer_then_read_sessions"].add(session_key)
                session_counts = attributed_counts.setdefault(session_key, {})
                session_counts[slice_id] = session_counts.get(slice_id, 0) + 1
                read_unique.setdefault(session_key, {}).setdefault(tool, set()).add(
                    slice_id
                )
        else:
            direct_event_count += 1
            stats["direct_read_events"] += 1
            if session_key:
                direct_sessions.add(session_key)
                stats["direct_read_sessions"].add(session_key)

    attribution_by_tool = {}
    for tool, stats in sorted(by_tool.items()):
        attribution_by_tool[tool] = {
            "offer_then_read_events": stats["offer_then_read_events"],
            "offer_then_read_sessions": len(stats["offer_then_read_sessions"]),
            "direct_read_events": stats["direct_read_events"],
            "direct_read_sessions": len(stats["direct_read_sessions"]),
        }

    return {
        "attributed_event_count": attributed_event_count,
        "direct_event_count": direct_event_count,
        "attributed_sessions": attributed_sessions,
        "direct_sessions": direct_sessions,
        "read_by_tool": read_by_tool,
        "attribution_by_tool": attribution_by_tool,
        "attributed_counts": attributed_counts,
        "read_unique": read_unique,
        "applied_sessions": applied_sessions,
        "applied_by_tool": applied_by_tool,
    }


def _funnel_mode_report(
    *,
    offer_state: dict[str, object],
    read_state: dict[str, object],
    excluded_sessions: set[str],
) -> dict:
    offered_sessions = offer_state["offered_sessions"]
    offered_by_tool = offer_state["offered_by_tool"]
    offered_counts = offer_state["offered_counts"]
    offered_unique = offer_state["offered_unique"]
    attributed_read_sessions = read_state["attributed_sessions"]
    read_by_tool = read_state["read_by_tool"]
    attributed_counts = read_state["attributed_counts"]
    read_unique = read_state["read_unique"]
    applied_sessions = read_state["applied_sessions"]
    applied_by_tool = read_state["applied_by_tool"]
    included_sessions = offered_sessions - excluded_sessions

    offered_slices_by_tool: dict[str, set[str]] = {}
    read_slices_by_tool: dict[str, set[str]] = {}
    total_offered: set[str] = set()
    total_read: set[str] = set()
    slice_counts: dict[str, dict[str, int]] = {}
    for session_key in included_sessions:
        for slice_id, count in offered_counts.get(session_key, {}).items():
            total_offered.add(slice_id)
            stats = slice_counts.setdefault(
                slice_id, {"offered_count": 0, "read_count": 0}
            )
            stats["offered_count"] += count
        for slice_id, count in attributed_counts.get(session_key, {}).items():
            total_read.add(slice_id)
            stats = slice_counts.setdefault(
                slice_id, {"offered_count": 0, "read_count": 0}
            )
            stats["read_count"] += count
        for tool, slice_ids in offered_unique.get(session_key, {}).items():
            offered_slices_by_tool.setdefault(tool, set()).update(slice_ids)
        for tool, slice_ids in read_unique.get(session_key, {}).items():
            read_slices_by_tool.setdefault(tool, set()).update(slice_ids)

    unique_slice_coverage = _funnel_coverage(total_offered, total_read)
    unique_slice_coverage_by_tool = {}
    coverage_tools = set(_FUNNEL_TOOLS)
    coverage_tools.update(offered_slices_by_tool)
    coverage_tools.update(read_slices_by_tool)
    for tool in sorted(coverage_tools):
        unique_slice_coverage_by_tool[tool] = _funnel_coverage(
            offered_slices_by_tool.get(tool, set()),
            read_slices_by_tool.get(tool, set()),
        )

    by_tool = {}
    tool_names = set(_FUNNEL_TOOLS)
    tool_names.update(offered_by_tool)
    tool_names.update(unique_slice_coverage_by_tool)
    for tool in sorted(tool_names):
        metrics = _funnel_metrics(
            offered_by_tool.get(tool, set()) - excluded_sessions,
            read_by_tool.get(tool, set()),
            applied_by_tool.get(tool, set()),
        )
        metrics["unique_slice_coverage"] = unique_slice_coverage_by_tool.get(
            tool,
            {"offered": 0, "read": 0, "read_rate": 0.0},
        )
        by_tool[tool] = metrics

    summary = _funnel_metrics(
        included_sessions,
        attributed_read_sessions,
        applied_sessions,
    )
    summary["unique_slice_coverage"] = unique_slice_coverage

    top_slices = []
    for slice_id, counts in slice_counts.items():
        if counts["read_count"] == 0:
            continue
        offered_count = counts["offered_count"]
        top_slices.append(
            {
                "slice_id": slice_id,
                "offered_count": offered_count,
                "read_count": counts["read_count"],
                "read_offer_ratio": (
                    round(counts["read_count"] / offered_count, 6)
                    if offered_count
                    else None
                ),
            }
        )
    top_slices.sort(
        key=lambda item: (-item["read_count"], -item["offered_count"], item["slice_id"])
    )

    return {
        "summary": summary,
        "by_tool": by_tool,
        "top_slices": top_slices[:_FUNNEL_TOP_N],
    }


def _memory_usage_funnel(args: argparse.Namespace) -> int:
    if not args.memory_root:
        print("hippo usage funnel: error: --memory-root is required", file=sys.stderr)
        return 2

    root = Path(args.memory_root)
    offered_rows, usage_rows, _load_diagnostics = _load_usage_rows(root, args.since)
    offer_state = _funnel_offer_index(offered_rows)
    read_state = _funnel_read_attribution(
        usage_rows, offer_state["offered_slices"]
    )
    offered_sessions = offer_state["offered_sessions"]

    from .ledger import processing

    processing_states = processing.fold_states(root)
    noise_sessions = {
        session_key
        for session_key in offered_sessions
        if processing_states.get(session_key) == "no-findings"
    }
    with_noise = _funnel_mode_report(
        offer_state=offer_state,
        read_state=read_state,
        excluded_sessions=set(),
    )
    without_noise = _funnel_mode_report(
        offer_state=offer_state,
        read_state=read_state,
        excluded_sessions=noise_sessions,
    )
    modes = {"with_noise": with_noise, "without_noise": without_noise}
    selected_mode = "without-noise" if args.exclude_noise else "with-noise"
    selected = modes[selected_mode.replace("-", "_")]
    read_attribution = {
        "offer_then_read_events": read_state["attributed_event_count"],
        "offer_then_read_sessions": len(read_state["attributed_sessions"]),
        "direct_read_events": read_state["direct_event_count"],
        "direct_read_sessions": len(read_state["direct_sessions"]),
        "by_tool": read_state["attribution_by_tool"],
    }

    report = {
        "selected_mode": selected_mode,
        "summary": selected["summary"],
        "by_tool": selected["by_tool"],
        "top_slices": selected["top_slices"],
        "with_noise": with_noise,
        "without_noise": without_noise,
        "noise_filter": {
            "state": "no-findings",
            "excluded_sessions": len(noise_sessions),
        },
        "read_attribution": read_attribution,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"selected_mode={selected_mode}")
        print(
            f"noise_filter=state:no-findings "
            f"excluded_sessions={len(noise_sessions)}"
        )
        for mode_name in ("with_noise", "without_noise"):
            mode = modes[mode_name]
            summary = mode["summary"]
            print(mode_name)
            print(
                f"sessions_offered={summary['sessions_offered']} "
                f"sessions_with_read={summary['sessions_with_read']} "
                f"read-through={summary['read_through_rate']:.2f}% "
                f"sessions_with_applied={summary['sessions_with_applied']} "
                f"applied-rate={summary['applied_rate']:.2f}% "
                f"unique-slice-offered={summary['unique_slice_coverage']['offered']} "
                f"unique-slice-read={summary['unique_slice_coverage']['read']} "
                f"unique-slice-read-rate={summary['unique_slice_coverage']['read_rate']:.2f}%"
            )
            for tool, metrics in mode["by_tool"].items():
                print(
                    f"  tool={tool} sessions_offered={metrics['sessions_offered']} "
                    f"sessions_with_read={metrics['sessions_with_read']} "
                    f"read-through={metrics['read_through_rate']:.2f}% "
                    f"sessions_with_applied={metrics['sessions_with_applied']} "
                    f"applied-rate={metrics['applied_rate']:.2f}% "
                    f"unique-slice-offered={metrics['unique_slice_coverage']['offered']} "
                    f"unique-slice-read={metrics['unique_slice_coverage']['read']} "
                    f"unique-slice-read-rate={metrics['unique_slice_coverage']['read_rate']:.2f}%"
                )
        print(
            f"read_attribution offer-then-read-events={read_attribution['offer_then_read_events']} "
            f"offer-then-read-sessions={read_attribution['offer_then_read_sessions']} "
            f"direct-read-events={read_attribution['direct_read_events']} "
            f"direct-read-sessions={read_attribution['direct_read_sessions']}"
        )
        print("top_slices")
        for item in selected["top_slices"]:
            ratio = item["read_offer_ratio"]
            ratio_display = "n/a" if ratio is None else f"{ratio:.6f}"
            print(
                f"  {item['slice_id']} offered={item['offered_count']} "
                f"read={item['read_count']} "
                f"read/offer={ratio_display}"
            )
    return 0


def _recall(args: argparse.Namespace) -> int:
    """跨 CLI consumer API：重用 prompt-time shortlist 管線（best-effort，恆 exit 0）。

    bypass_early_stop=True：顯式 recall 是使用者主動操作，意圖明確，不受
    OFFER_STOP_AFTER_EVENTS 早停（自動 UserPromptSubmit hook 專用的雜訊抑制）影響。
    """
    from .hooks._shortlist_common import build_shortlist_and_record

    block = build_shortlist_and_record(
        Path(args.memory_root), args.tool, args.session_id, args.cwd, args.prompt,
        bypass_early_stop=True)
    if block:
        print(block)
    return 0


def _usage_mark_applied(args: argparse.Namespace) -> int:
    """applied 顯式訊號（契約 8）：agent 主動回報某條記憶實際影響了做法。

    寫入前反查 offered.jsonl：同 (session_id, tool) 必須存在先行 offered 記錄，且
    slice_id 必須屬於那些 offered slices，否則 exit 1 並拒絕寫入偽造事件。

    v5 BLOCKER #1/#4：offered.jsonl 以逐行 iterator 讀取（不使用
    ``Path.read_text().splitlines()``），I/O 與單行 parse 錯誤 fail-soft，且此
    函式只讀取 offered.jsonl，不會改寫它。
    """
    led_dir = Path(args.memory_root) / "runtime" / "ledger"
    session_seen = False
    offered_slices: set[str] = set()
    offered_path = led_dir / "offered.jsonl"
    diagnostics = usage_ledger.new_diagnostics()
    for e in usage_ledger.iter_ledger_events(offered_path, diagnostics):
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
        model=args.model,
        assume_yes=args.yes,
    )


def _ops_doctor(args) -> int:
    from paulsha_hippo import ops

    return ops.run_doctor(
        fix_backend=getattr(args, "fix_backend", False),
        live_probe=getattr(args, "probe_live", False),
        probe_profiles=getattr(args, "probe_profiles", False),
    )


def _ops_install_hooks(args) -> int:
    from paulsha_hippo import ops

    return ops.run_install_hooks(memory_root=args.memory_root, repo_root=args.repo_root)


def _ops_install_service(args) -> int:
    from paulsha_hippo import ops

    return ops.run_install_service(enable=args.enable)


def _ops_install_all(args) -> int:
    from . import deployment

    package_root = Path(__file__).resolve().parent
    manifest = Path(args.manifest) if args.manifest else package_root / "install-manifest.json"
    target_root = Path(args.target_root) if args.target_root else paths.hippo_config_root()
    try:
        runtime_plan = Path(args.runtime_plan) if args.runtime_plan else package_root / "install-runtime-plan.json"
        runtime = _load_install_runtime(
            runtime_plan,
            deployment,
            target_root,
            manifest_path=manifest,
            package_root=package_root,
        )
        result = deployment.install_all(
            manifest_path=manifest,
            target_root=target_root,
            package_root=package_root,
            force=bool(args.force),
            dry_run=bool(args.dry_run),
            transaction_root=args.transaction_root,
            runtime=runtime,
        )
    except deployment.DeploymentError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _install_plan_hash(payload: dict) -> str:
    unsigned = dict(payload)
    unsigned.pop("review", None)
    canonical = (json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def _load_install_runtime(
    value: str | Path,
    deployment,
    target_root: Path,
    *,
    manifest_path: Path,
    package_root: Path,
):
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from . import ops

    try:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise deployment.DeploymentError("invalid install runtime plan") from exc
    if not isinstance(payload, dict):
        raise deployment.DeploymentError("install runtime plan root must be an object")
    allowed = {
        "schema_version", "runtime_kind", "review",
        "commands", "rollback_commands", "profile_id", "command_timeout",
        "drain_timeout", "lock_timeout", "rollback_timeout",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise deployment.DeploymentError(
            f"unknown install runtime plan field: {unknown[0]}"
        )
    if str(payload.get("schema_version", "")) != "1":
        raise deployment.DeploymentError("unsupported install runtime plan schema")
    runtime_kind = str(payload.get("runtime_kind", "reviewed-override"))
    if runtime_kind not in {"package-default", "reviewed-override"}:
        raise deployment.DeploymentError("unsupported install runtime plan kind")
    if runtime_kind == "package-default" and Path(value).resolve() != (
        package_root / "install-runtime-plan.json"
    ).resolve():
        raise deployment.DeploymentError("package-default runtime plan must come from the release package")
    review = payload.get("review")
    if not isinstance(review, dict) or review.get("status") != "approved":
        raise deployment.DeploymentError("install runtime plan requires approved review")
    try:
        manifest_sha256 = hashlib.sha256(manifest_path.resolve().read_bytes()).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise deployment.DeploymentError("install manifest is unavailable for runtime review") from exc
    if review.get("manifest_sha256") != manifest_sha256:
        raise deployment.DeploymentError("install runtime plan is not bound to this manifest")
    plan_sha256 = _install_plan_hash(payload)
    if review.get("plan_sha256") != plan_sha256:
        raise deployment.DeploymentError("install runtime plan review hash mismatch")
    commands = payload.get("commands")
    rollback_commands = payload.get("rollback_commands")
    if runtime_kind == "package-default":
        from . import install_runtime

        executor = install_runtime.package_runtime_executor
    else:
        install_runtime = None
        executor = None
    runner = deployment.AllowlistedCommandRunner(
        commands, executor=executor, location=sys.executable, target_root=target_root
    )
    rollback_runner = deployment.AllowlistedCommandRunner(
        rollback_commands, executor=executor, location=sys.executable, target_root=target_root
    )

    def doctor_static(context):
        if runtime_kind == "package-default":
            assert install_runtime is not None
            return install_runtime.doctor_gate(context)
        sink = io.StringIO()
        previous = os.environ.get("HIPPO_CONFIG_ROOT")
        os.environ["HIPPO_CONFIG_ROOT"] = str(context.target_root)
        with redirect_stdout(sink), redirect_stderr(sink):
            try:
                rc = ops.run_doctor(live_probe=False, probe_profiles=False)
            finally:
                if previous is None:
                    os.environ.pop("HIPPO_CONFIG_ROOT", None)
                else:
                    os.environ["HIPPO_CONFIG_ROOT"] = previous
        return {"ok": rc == 0, "status": "passed" if rc == 0 else "failed"}

    def profile_probe(context, profile_id):
        if runtime_kind == "package-default":
            assert install_runtime is not None
            return install_runtime.profile_gate(context, profile_id)
        sink = io.StringIO()
        previous = os.environ.get("HIPPO_CONFIG_ROOT")
        os.environ["HIPPO_CONFIG_ROOT"] = str(context.target_root)
        with redirect_stdout(sink), redirect_stderr(sink):
            try:
                rc = ops.run_doctor(live_probe=False, probe_profiles=True)
            finally:
                if previous is None:
                    os.environ.pop("HIPPO_CONFIG_ROOT", None)
                else:
                    os.environ["HIPPO_CONFIG_ROOT"] = previous
        return {
            "ok": rc == 0,
            "status": "passed" if rc == 0 else "failed",
            "profile_id": profile_id,
        }

    return deployment.InstallRuntime(
        commands=commands,
        rollback_commands=rollback_commands,
        command_runner=runner,
        rollback_runner=rollback_runner,
        doctor_static=doctor_static,
        profile_probe=profile_probe,
        profile_id=str(payload.get("profile_id", "default")),
        command_timeout=float(payload.get("command_timeout", 60.0)),
        drain_timeout=float(payload.get("drain_timeout", 60.0)),
        lock_timeout=float(payload.get("lock_timeout", 30.0)),
        rollback_timeout=float(payload.get("rollback_timeout", 60.0)),
        runner_location=sys.executable,
        rollback_runner_location=sys.executable,
        rollback_target_root=target_root,
        plan_sha256=plan_sha256,
        plan_source=Path(value).resolve(),
        runtime_kind=runtime_kind,
    )


def _upgrade(args: argparse.Namespace) -> int:
    from . import upgrade as upgrade_tx

    try:
        if args.upgrade_command == "plan":
            command_plan = _load_upgrade_command_plan(args.command_plan, upgrade_tx)
            result = upgrade_tx.plan_upgrade(
                args.candidate,
                target_root=args.target_root,
                profile_id=args.profile_id,
                artifact_name=args.artifact_name,
                **command_plan,
            )
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        elif args.upgrade_command == "prepare":
            result = upgrade_tx.prepare_upgrade(args.plan, transaction_root=args.transaction_root)
        elif args.upgrade_command == "apply":
            result = upgrade_tx.apply_upgrade(args.manifest, force=bool(args.force), dry_run=bool(args.dry_run))
        else:
            result = upgrade_tx.rollback_upgrade(args.manifest)
    except upgrade_tx.UpgradeError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _load_upgrade_command_plan(value: str | None, upgrade_tx) -> dict[str, object]:
    if value is None:
        return {}
    try:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise upgrade_tx.UpgradeError("invalid upgrade command plan") from exc
    if not isinstance(payload, dict):
        raise upgrade_tx.UpgradeError("upgrade command plan root must be an object")
    allowed = {
        "phase_commands",
        "rollback_commands",
        "command_timeout",
        "max_commands",
        "allowed_executables",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise upgrade_tx.UpgradeError(
            f"unknown upgrade command plan field: {unknown[0]}"
        )
    return payload


def _config_migrate(args: argparse.Namespace) -> int:
    from . import config_migration

    try:
        if args.migrate_command == "plan":
            canonical = Path(args.canonical) if args.canonical else paths.atomizer_config_path()
            legacy = (
                Path(args.legacy)
                if args.legacy
                else paths.config_path("atomizer.override.yaml")
            )
            result = config_migration.plan_migration(canonical, legacy).as_dict()
        elif args.migrate_command == "apply":
            result = config_migration.apply_migration(
                args.plan,
                resolution=args.resolution,
                dry_run=bool(args.dry_run),
            )
        else:
            result = config_migration.rollback_migration(args.report)
    except config_migration.MigrationError as exc:
        print(
            json.dumps(
                {"status": "blocked", "reason": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    out = getattr(args, "out", None)
    if out:
        output_path = Path(out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


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
    if args.max_load is not None:
        extra += ["--max-load", str(args.max_load)]
    if args.promoter:
        extra += ["--promoter", args.promoter]
    # dream run 的 argparse 對重複旗標 last-wins：extra 的 --promoter/--max-load
    # 覆蓋 run_dream_supervise 內建的 --require-idle --promoter llm 基底。
    return ops.run_dream_supervise(interval=args.interval, extra_argv=extra, once=args.once)


def _locks_cleanup_legacy(args: argparse.Namespace) -> int:
    from paulsha_hippo import ops

    result = ops.cleanup_legacy_locks(Path(args.memory_root), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if (result.get("blocked") or result.get("busy")
            or result.get("unknown") or result.get("unsafe_locks_dir")):
        return 1
    return 0


def _ledger_repair(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from paulsha_hippo.ledger import integrity

    now = args.now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = integrity.repair_ledger_dir(
        Path(args.memory_root), apply=args.apply, now=now
    )
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
