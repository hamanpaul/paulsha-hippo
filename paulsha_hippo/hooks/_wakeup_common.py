#!/usr/bin/env python3
"""Shared helpers for session-start and precompact hooks.

Provides utilities for:
- Memory root resolution
- Logging to hooks.log
- Reading stdin payloads
- Computing wake-up briefs
- Writing queue payloads
- Triggering the importer
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def memory_root() -> Path:
    """Resolve memory root from PSC_MEMORY_ROOT env var or default."""
    env = os.environ.get("PSC_MEMORY_ROOT", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".agents" / "memory"


def log_warn(root: Path, tool: str, msg: str) -> None:
    """Log a warning message to hooks.log."""
    try:
        log_path = root / "log" / "hooks.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"WARN {tool}: {msg}\n")
    except Exception:
        pass


def read_payload(root: Path, tool: str) -> dict:
    """Read and parse JSON payload from stdin. Fail-open on errors."""
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        log_warn(root, tool, f"failed to read stdin: {exc}")
        return {}

    try:
        payload: dict = json.loads(raw) if raw.strip() else {}
        return payload
    except json.JSONDecodeError as exc:
        log_warn(root, tool, f"failed to parse stdin JSON: {exc}")
        return {}


def compute_brief(root: Path, cwd: str | None) -> str:
    """Compute wake-up brief for the given cwd.
    
    Returns empty string if project cannot be resolved or brief is empty.
    """
    try:
        from paulsha_hippo.importer.project_resolver import resolve_project
        from paulsha_hippo.wakeup.builder import build_brief
    except ImportError as exc:
        log_warn(root, "wakeup", f"failed to import resolver or builder: {exc}")
        return ""

    try:
        project = resolve_project(cwd=cwd, memory_root=str(root))
        if project in ("_unknown", ""):
            return ""

        now_iso = datetime.now(timezone.utc).isoformat()
        brief = build_brief(root, project, now=now_iso)
        return brief
    except Exception as exc:
        log_warn(root, "wakeup", f"failed to build brief: {exc}")
        return ""


def sanitize_id(value: str) -> str:
    """Replace path separators and colons with __."""
    return re.sub(r"[/\\:]+", "__", value)


_TOOL_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def validate_tool(value: str) -> str:
    """Validate a tool attribution token that gets embedded in runtime file names.

    tool 會直接進入 `runtime/wakeup/<tool>__<sid>.offered.json` 檔名，而
    `hippo recall --tool` 是外部輸入：只接受單一 path-safe token（英數開頭，
    其後限英數與 `. _ -`），拒絕路徑分隔符、`:`、`..`、前導 `.` 等任何可讓
    路徑逃出 memory root 的形態（traversal）。合法即原值返回，否則 ValueError。
    """
    if not _TOOL_TOKEN_RE.fullmatch(value):
        raise ValueError(
            f"invalid tool {value!r}: expected a path-safe token matching "
            "[A-Za-z0-9][A-Za-z0-9._-]* (no path separators, no leading '.')"
        )
    return value


# offered-map 檔名的單欄位編碼安全直通集：英數＋'.'＋'-'。刻意排除 '_'，使編碼後的
# 欄位不含任何 '_'——如此 '__' 可作為 tool⟷session_id 的「不可能出現在任一欄位內」的
# 無歧義分隔符（見 offered_map_path 的單射論證）。
_OFFERED_TOKEN_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-"
)


def _enc_offered_token(value: str) -> str:
    """單射、path-safe、且不含 '_' 的單欄位（tool 或 session_id）編碼。

    對不在 [A-Za-z0-9.-] 的字元逐 UTF-8 byte percent-encode（含 '_'→%5F、路徑分隔符、
    ':'、'%' 本身→%25），故輸出零 '_'、亦無 '/'、'\\'、':'（path-safe）。percent-encoding
    可逆 ⇒ 對每個欄位單射。常見 token（純英數＋'.'＋'-'，如 UUID／`claude-code`）原樣直通，
    使無特殊字元的 (tool, session) 檔名與舊 `{tool}__{sanitize_id(sid)}` 方案逐 byte 相容。
    """
    out: list[str] = []
    for ch in value:
        if ch in _OFFERED_TOKEN_SAFE:
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
    return "".join(out)


def offered_map_path(root: Path, tool: str, session_id: str) -> Path:
    """Per-session offered-map 路徑——writer 與 post-tool readers 共用的唯一構點。

    tool 可能來自外部輸入（`hippo recall --tool`）：先 validate_tool 為 path-safe token。
    tool 與 session_id 各自以 _enc_offered_token 單射編碼後以 '__' 銜接。因編碼後兩欄位皆
    不含 '_'，'__' 為無歧義分隔符 ⇒ (tool, session_id) → 檔名為**單射**：杜絕舊
    `{tool}__{sanitize_id(sid)}` 的非單射撞名——tool 含 '_'（`a`+`b__c` 與 `a__b`+`c` 舊制
    同映 `a__b__c`）或 session 經舊 sanitize 折疊（`a/b`、`a:b`、`a\\b`、`a__b` 舊制同映
    `a__b`）造成的跨 session/tool offered 汙染。resolve 後再確認落點 parent 仍是
    runtime/wakeup（防 symlink 偷渡與 sanitizer 迴歸；編碼已消除路徑分隔符，此為第二層防護）。
    """
    wk_dir = root / "runtime" / "wakeup"
    fname = (f"{_enc_offered_token(validate_tool(tool))}__"
             f"{_enc_offered_token(session_id)}.offered.json")
    path = wk_dir / fname
    if path.resolve().parent != wk_dir.resolve():
        raise ValueError(f"offered map path escapes runtime/wakeup: {path}")
    return path


def hippo_invocation(root: Path) -> list[str]:
    """Return an argv prefix that can invoke the hippo CLI in this deployment."""
    venv_python = root / "hooks" / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), "-m", "paulsha_hippo"]
    return ["python3", "-m", "paulsha_hippo"]


def format_recall_command(root: Path, tool: str, session_id: str, cwd: str | None) -> str:
    """組出顯式 recall 指令字串（tool/session-id 歸因已填、--prompt 留說明佔位）。"""
    import shlex

    argv = hippo_invocation(root) + [
        "recall",
        "--memory-root",
        str(root),
        "--tool",
        tool,
        "--session-id",
        session_id,
    ]
    if cwd:
        argv += ["--cwd", str(cwd)]
    return " ".join(shlex.quote(a) for a in argv) + ' --prompt "<當前任務描述>"'


def recall_guidance_hint(root: Path, tool: str, session_id: str, cwd: str | None) -> str:
    """無 prompt-time hook 平台的顯式 recall 指引（capability matrix: recall-capable）。"""
    return (
        "本平台不會在每次 prompt 自動浮現任務相關記憶；需要任務相關記憶時，執行：\n"
        f"`{format_recall_command(root, tool, session_id, cwd)}`\n"
        "再用 Read 開啟輸出清單中的絕對路徑取全文。"
    )


def write_queue_payload(
    root: Path,
    tool: str,
    session_id: str,
    payload: dict,
    capture_scope: str,
) -> Path | None:
    """Write one uniquely identified capture without overwriting the same session.

    Returns Path on success, or None on failure.
    """
    try:
        queue_payload = dict(payload)
        queue_payload["tool"] = tool
        queue_payload["session_id"] = session_id
        queue_payload["capture_scope"] = capture_scope
        capture_id = uuid.uuid4().hex
        queue_payload["capture_id"] = capture_id

        queue_dir = root / "runtime" / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{tool}__{sanitize_id(session_id)}__{capture_id}.json"
        queue_path = queue_dir / filename
        tmp_path = queue_dir / f".{filename}.tmp"
        tmp_path.write_text(
            json.dumps(queue_payload, sort_keys=True, indent=2), encoding="utf-8"
        )
        tmp_path.replace(queue_path)
        return queue_path
    except Exception as exc:
        log_warn(root, tool, f"failed to write queue: {exc}")
        return None


def fire_importer(root: Path, tool: str, queue_path: Path) -> None:
    """Fire-and-forget trigger the importer in the background."""
    venv_python = root / "hooks" / ".venv" / "bin" / "python"
    if not venv_python.exists():
        log_warn(root, tool, f"venv not found at {venv_python}; queue written but importer not triggered")
        return
    try:
        subprocess.Popen(
            [
                str(venv_python), "-m", "paulsha_hippo.importer.cli",
                "ingest", "--queue-item", str(queue_path),
                "--memory-root", str(root),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        log_warn(root, tool, f"importer trigger failed: {exc}")


def compute_brief_and_record(
    root: Path,
    tool: str,
    session_id: str,
    cwd: str | None,
    *,
    recall_guidance: bool = False,
) -> str:
    """SessionStart 極簡 orientation；不再前置引用前言、不再寫 session-wide offered。

    recall_guidance=True：無 prompt-time hook 的平台改注入顯式 recall 指引
    （不假裝 SessionStart orientation 等同 task retrieval）。
    """
    try:
        from paulsha_hippo.importer.project_resolver import resolve_project
        from paulsha_hippo.wakeup.builder import build_orientation
    except ImportError as exc:
        log_warn(root, tool, f"failed to import resolver or builder: {exc}")
        return ""
    try:
        project = resolve_project(cwd=cwd, memory_root=str(root))
        if project in ("_unknown", ""):
            return ""
        if not recall_guidance:
            return build_orientation(root, project)
        return build_orientation(
            root,
            project,
            retrieval_hint=recall_guidance_hint(root, tool, session_id, cwd),
        )
    except Exception as exc:
        log_warn(root, tool, f"failed to build orientation: {exc}")
        return ""
