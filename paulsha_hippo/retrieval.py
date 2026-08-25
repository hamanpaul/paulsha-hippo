# paulshaclaw/memory/retrieval.py
"""Pure retrieval helpers (no IO): FTS query sanitization + shortlist formatting."""
from __future__ import annotations

import re

# alnum/underscore runs, or contiguous CJK runs
_TOKEN = re.compile(r"[0-9A-Za-z_]+|[一-鿿]+")

# High-frequency words that would OR-match almost any slice (a bare hit-presence
# gate + OR matching is otherwise low-precision). Dropping them keeps the shortlist
# anchored on content tokens. (An absolute bm25 score threshold is deferred — it is
# query-length dependent and needs real read-data to tune; see design A4.)
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "its", "for", "on",
    "with", "as", "at", "be", "by", "this", "that", "these", "those", "from", "are",
    "was", "were", "you", "your", "we", "our", "me", "my", "i", "do", "does", "did",
    "how", "what", "why", "when", "where", "which", "who", "can", "could", "should",
    "would", "will", "please", "help", "let", "get", "make", "use", "using", "want",
})
_CJK_STOPWORDS = frozenset({
    "的", "了", "和", "是", "我", "你", "他", "她", "它", "嗎", "呢", "吧", "啊",
    "怎麼", "如何", "幫我", "請", "這個", "那個", "一下", "可以", "要", "把",
})

_SHORTLIST_HINT = "> 與當前任務相關的記憶（相關項用 Read 開啟下列絕對路徑取全文）："
_SHORTLIST_HINT_SHOW = ("> 與當前任務相關的記憶（相關項執行 `{cmd} <slice_id>` 取精簡全文，"
                        "比 Read 省約 70% token）：")


def to_fts_query(prompt: str) -> str:
    """Build a safe FTS5 MATCH query from arbitrary prompt text.

    Extracts alnum/CJK tokens, drops 1-char latin tokens and high-frequency
    stopwords, quotes each surviving token as an FTS5 string literal (neutralizing
    operators), and OR-joins them. Empty or content-less input returns "" (caller
    treats as 'do not search', so trivial/stopword-only prompts inject nothing).
    """
    if not prompt:
        return ""
    toks: list[str] = []
    for t in _TOKEN.findall(prompt):
        if len(t) < 2 and t.isascii():
            continue
        if t.lower() in _STOPWORDS or t in _CJK_STOPWORDS:
            continue
        toks.append(t)
    if not toks:
        return ""
    return " OR ".join(f'"{t}"' for t in toks)


def format_shortlist(hits: list[dict], *, hint: str = "read", show_command: str = "") -> str:
    """Render hits ({title, summary, path, slice_id}) as an injected shortlist block. [] -> ''.

    hint=="show" and show_command given: hint line 建議 `show_command <slice_id>`（省 token）
    and each row carries its slice_id suffix so agents can copy it straight into the
    command. Otherwise (default): unchanged legacy "Read 開啟絕對路徑" wording.
    """
    if not hits:
        return ""
    lines = [_SHORTLIST_HINT_SHOW.format(cmd=show_command) if hint == "show" and show_command else _SHORTLIST_HINT]
    for h in hits:
        title = (h.get("title") or "").strip() or "(untitled)"
        summary = (h.get("summary") or "").strip()
        path = h.get("path") or ""
        line = f"- [{title}] — {summary} — {path}"
        if h.get("slice_id"):
            line += f" — {h['slice_id']}"
        lines.append(line)
    return "\n".join(lines)
