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


def format_shortlist(hits: list[dict]) -> str:
    """Render hits ({title, summary, path}) as an injected shortlist block. [] -> ''."""
    if not hits:
        return ""
    lines = [_SHORTLIST_HINT]
    for h in hits:
        title = (h.get("title") or "").strip() or "(untitled)"
        summary = (h.get("summary") or "").strip()
        path = h.get("path") or ""
        lines.append(f"- [{title}] — {summary} — {path}")
    return "\n".join(lines)
