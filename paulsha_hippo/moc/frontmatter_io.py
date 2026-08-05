from __future__ import annotations

import functools
import os
import tempfile
from pathlib import Path
from typing import Any


def read(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body). Body is everything after the closing ---."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text
    block = "".join(lines[1:end])
    body = "".join(lines[end + 1:])
    try:
        import yaml
    except ModuleNotFoundError:
        return {}, body
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        # Malformed frontmatter is a data condition, not a crash: a single poison-pill
        # slice must not abort the whole MOC rebuild. Treat as no metadata (#139).
        return {}, body
    return (data if isinstance(data, dict) else {}), body


def _emit(value: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    if isinstance(value, dict):
        out: list[str] = []
        for key, val in value.items():
            if isinstance(val, (dict, list)):
                out.append(f"{pad}{key}:")
                out.extend(_emit(val, indent + 1))
            else:
                out.append(f"{pad}{key}: {_scalar(val)}")
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                out.append(f"{pad}-")
                out.extend(_emit(item, indent + 1))
            else:
                out.append(f"{pad}- {_scalar(item)}")
        return out
    return [f"{pad}{_scalar(value)}"]


@functools.lru_cache(maxsize=1024)
def _needs_quoting_for_type_fidelity(s: str) -> bool:
    """True 若裸字串 ``s`` 在 YAML round-trip 後型別不再是 ``str``（issue #102）。

    判準用實測不用清單：``yaml.safe_load(s)`` 的型別 != str 即需引號。這一網
    打盡數字樣（``264``/``1.5``/``1e3``）、布林樣（``true``/``no``/``off``）、
    null 樣（``null``/``~``）、空字串等所有 YAML 隱式型別轉換，勝過手寫
    regex 清單且不必隨 YAML 1.1 bool/null 詞彙表增修而補洞。

    lru_cache：janitor/moc 每輪 rewrite 上千檔、大量重複值（tags/project 等），
    快取避免同字串重複 probe（reviewer 建議）。
    """
    try:
        import yaml
    except ModuleNotFoundError:
        # No YAML engine available to probe with; caller's other heuristics
        # (special chars, embedded quotes, etc.) are the only signal left.
        return False
    try:
        parsed = yaml.safe_load(s)
    except Exception:
        # Unparsable as bare YAML（含 YAMLError 之外的解析器極端例外）
        # -> fail toward quoting：加引號恆安全，漏引號才是本 issue 的病灶。
        return True
    return not isinstance(parsed, str)


def _scalar(value: Any) -> str:
    if value is None:
        # YAML 原生 null 必須序列化回 ``null``：str(None) 會寫出 ``None``，
        # 而 PyYAML 不把 ``None`` 當 null 詞彙，下一次 read() 就劣化成字串
        # "None"（issue #109 review；比照 #102/#104 的型別保真精神）。
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    # Quote strings that open a flow collection, carry YAML special chars, or hold
    # embedded quotes/newlines. When quoting, escape so the double-quoted scalar
    # round-trips through yaml.safe_load instead of producing broken YAML (#139).
    needs_quote = (
        s.startswith(("[", "]", "{", "}", "'", '"', "!", "&", "*", "@", "`", "|", ">", "%"))
        or ":" in s
        or "#" in s
        or '"' in s
        or "\n" in s
        or "\r" in s
        or s != s.strip()
    )
    # A str value whose bare form would silently change type on re-parse
    # (numeric-like/bool-like/null-like/empty strings, e.g. "264" -> int 264)
    # must also be quoted (#102). Scoped to actual str inputs: non-str values
    # (int/float/datetime, etc.; None handled above as null) already serialize
    # through the checks above using their str() form and are left as-is.
    if not needs_quote and isinstance(value, str):
        needs_quote = _needs_quoting_for_type_fidelity(s)
    if needs_quote:
        escaped = (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
        return f'"{escaped}"'
    return s


def dump(frontmatter: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, (dict, list)):
            if isinstance(value, list) and not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            lines.extend(_emit(value, 1))
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def update(path: Path, updates: dict[str, Any]) -> None:
    fm, body = read(path.read_text(encoding="utf-8"))
    fm.update(updates)
    # A target may already consume the complete NAME_MAX budget.  Deriving the
    # temporary name from ``path.name`` can then make the update itself fail
    # with ENAMETOOLONG.  A short same-directory name keeps os.replace atomic
    # without truncating the real slice id or final filename.
    fd, tmp_name = tempfile.mkstemp(prefix=".hippo-fm-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(dump(fm, body))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
