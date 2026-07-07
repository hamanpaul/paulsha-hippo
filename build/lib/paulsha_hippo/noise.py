"""Stage 2 knowledge noise classifier (#139 P2). Body-content only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

# importer/frontmatter.render_markdown 的結構段落 heading。importer-exclusive 的段落名
# 永遠不會是合法的獨立知識原子標題，故無條件視為 echo；`Summary` 在真筆記中常見，
# 需「散文行 ≤1」guard 以免誤刪（#139 finding 3）。
_IMPORTER_EXCLUSIVE = ("CWD", "Source", "Prompts", "Touched files", "Referenced artifacts")
_IMPORTER_EXCLUSIVE_FIRST_LINE = {f"## {name}": name for name in _IMPORTER_EXCLUSIVE}
_GUARDED_SECTIONS = {f"## {name}": name for name in ("Summary",)}
# 另一種 session metadata 區塊格式（copilot-cli 等），純元資料、非知識。
_SESSION_META_LINE = re.compile(r"^#{1,6}\s+Session\s+(?:Metadata|Information)\b")

_HEADING_LINE = re.compile(r"^#{1,6}\s")
_LIST_ITEM = re.compile(r"^(?:[-*+]\s|\d+[.)]\s)")

_PLACEHOLDER_PHRASES = ("(無內容)", "尚未收到您的具體需求", "目前尚未收到")
_BARE_PLACEHOLDERS = {"- (none)", "(none)", "(unknown)"}
# A placeholder is deletion-grade noise only when the body *opens* with the
# boilerplate, not when a real note merely quotes it deeper in the text (#139
# finding 3). The importer's empty-session bodies all begin with the phrase.
_PLACEHOLDER_HEAD_WINDOW = 12


def _content_lines(stripped: str) -> list[str]:
    """Prose lines: non-blank lines that are neither markdown headings nor list items."""
    out: list[str] = []
    for line in stripped.splitlines():
        s = line.strip()
        if not s or _HEADING_LINE.match(s) or _LIST_ITEM.match(s):
            continue
        out.append(s)
    return out


def _is_hollow(stripped: str) -> bool:
    """True when nothing but markdown heading lines / blanks remains.

    Content-based (NOT a length threshold): covers真正空白與純標題片段
    （如 `# Session <uuid>`）。
    """
    for line in stripped.splitlines():
        s = line.strip()
        if s and not _HEADING_LINE.match(s):
            return False
    return True


def _structural_echo_section(stripped: str) -> str | None:
    """Return the structural section name iff the body is an importer-template echo.

    importer-exclusive headings (`## CWD/## Source/## Prompts/## Touched files/
    ## Referenced artifacts`) and session-metadata blocks are unconditional echoes —
    those section names never head a real standalone knowledge atom. `## Summary`
    is common in real notes, so it is an echo only when the body carries no
    substantial prose (≤1 prose line), keeping multi-paragraph summaries (#139 finding 3).
    """
    first_line = stripped.splitlines()[0].strip() if stripped else ""

    section = _IMPORTER_EXCLUSIVE_FIRST_LINE.get(first_line)
    if section is not None:
        return section

    if _SESSION_META_LINE.match(first_line):
        return "SessionMetadata"

    guarded = _GUARDED_SECTIONS.get(first_line)
    if guarded is not None and len(_content_lines(stripped)) <= 1:
        return guarded

    return None


def _opens_with_placeholder(stripped: str) -> bool:
    if stripped in _BARE_PLACEHOLDERS:
        return True
    head = stripped[:_PLACEHOLDER_HEAD_WINDOW]
    return any(p in head for p in _PLACEHOLDER_PHRASES)


# --- doc-fragment detection (#147): verbatim overlap with agent-instruction docs ---

# A doc-fragment is a knowledge slice whose body is a verbatim section of an
# agent-instruction document (CLAUDE.md / AGENTS.md / GEMINI.md). The signal is
# deliberately content-overlap (not a bare "## N." heading regex) so a real note
# that merely uses numbered sub-sections is never mis-deleted. Detection needs a
# DocCorpus; without one the rule is inert (back-compat).
_DOC_FRAGMENT_MIN_CONTENT_HITS = 2


def _normalize_line(line: str) -> str:
    """Collapse internal whitespace so verbatim comparison is robust to reflow."""
    return re.sub(r"\s+", " ", line.strip())


def _heading_text(line: str) -> str | None:
    """For a markdown heading line, return its normalized heading text; else None."""
    if not _HEADING_LINE.match(line):
        return None
    return _normalize_line(line.lstrip("#").strip())


@dataclass(frozen=True)
class DocCorpus:
    """Normalized verbatim line/heading sets of agent-instruction documents."""

    headings: frozenset[str]
    lines: frozenset[str]

    def __bool__(self) -> bool:
        return bool(self.lines)


def build_corpus(texts: Iterable[str]) -> DocCorpus:
    """Build a DocCorpus from raw instruction-document texts (pure, no IO)."""
    headings: set[str] = set()
    lines: set[str] = set()
    for text in texts:
        for raw in text.splitlines():
            s = raw.strip()
            if not s:
                continue
            lines.add(_normalize_line(s))
            head = _heading_text(s)
            if head:
                headings.add(head)
    return DocCorpus(frozenset(headings), frozenset(lines))


def _is_doc_fragment(stripped: str, corpus: "DocCorpus | None") -> bool:
    """True iff the body is a verbatim section of the instruction corpus.

    Requires: first non-blank line is a heading whose text is in the corpus, AND
    at least ``_DOC_FRAGMENT_MIN_CONTENT_HITS`` of the following content lines are
    verbatim corpus lines. Trailing session noise appended after the section does
    not matter (we count hits, not a contiguous prefix), since instruction docs
    drift over time and fragments often carry appended chatter.
    """
    if not corpus:
        return False
    content = [s for s in (ln.strip() for ln in stripped.splitlines()) if s]
    if not content:
        return False
    head = _heading_text(content[0])
    if head is None or head not in corpus.headings:
        return False
    hits = 0
    for line in content[1:]:
        if _normalize_line(line) in corpus.lines:
            hits += 1
            if hits >= _DOC_FRAGMENT_MIN_CONTENT_HITS:
                return True
    return False


@dataclass(frozen=True)
class NoiseVerdict:
    is_noise: bool
    reason: str


def classify_noise(
    frontmatter: Mapping[str, object],
    body: str,
    *,
    doc_corpus: "DocCorpus | None" = None,
) -> NoiseVerdict:
    """Classify a knowledge slice as noise using ONLY its body content.

    frontmatter is accepted for interface symmetry but intentionally unused so
    that untitled / no-project slices with real bodies are not mis-dropped.
    Deletion-grade: each rule is shaped to avoid removing real knowledge (#139).

    ``doc_corpus`` (optional) enables doc-fragment detection (#147): when a
    non-empty corpus of agent-instruction documents is supplied, a slice whose
    body is a verbatim section of those docs is classified ``doc-fragment``.
    Omitting it (or passing an empty corpus) leaves prior behavior unchanged.
    """
    del frontmatter
    stripped = body.strip()

    section = _structural_echo_section(stripped)
    if section is not None:
        return NoiseVerdict(True, f"structural-echo:{section}")

    if _opens_with_placeholder(stripped):
        return NoiseVerdict(True, "placeholder")

    if _is_hollow(stripped):
        return NoiseVerdict(True, "empty")

    if _is_doc_fragment(stripped, doc_corpus):
        return NoiseVerdict(True, "doc-fragment")

    return NoiseVerdict(False, "")


def pool_exclude_reason(frontmatter: Mapping[str, object]) -> str | None:
    """Frontmatter-level, NON-deletion pool exclusion (canary/review). Returns a
    reason string to keep a slice out of the retrieval pool, or None to keep it.

    Distinct from classify_noise (body-based, deletion-grade): this only hides a
    slice from search/shortlist; the file is never deleted, so the bar is looser.
    """
    kind = str(frontmatter.get("artifact_kind") or "").strip().lower()
    if kind == "review":
        return "review-record"
    blob = " ".join(str(frontmatter.get(k, "")) for k in
                    ("atom_title", "title", "session_title")).lower()
    if kind == "task" and ("canary" in blob or "smoke" in blob):
        return "canary-fixture"
    if any(is_generic_title(frontmatter.get(k)) for k in ("atom_title", "title")):
        return "generic-title"
    return None

# --- generic-title pool exclusion: normalized exact/prefix match only (#178) ---
_GENERIC_EXACT_TITLES = frozenset(
    {"overview", "problem", "untitled", "review-summary", "report", "task", "todo"}
)
_GENERIC_TITLE_PREFIX = re.compile(r"^(?:report|task|todo)-")


def is_generic_title(title: object) -> bool:
    """True when title normalizes to an exact generic label or allowed prefix.

    Normalization lower-cases, trims, and collapses whitespace/underscores to ``-``.
    Match rules are limited to exact titles in ``_GENERIC_EXACT_TITLES`` or the
    ``report-`` / ``task-`` / ``todo-`` prefix regex. Contains-style matches do
    not count, and falsy/empty inputs return ``False``.
    """
    if not title:
        return False
    normalized = re.sub(r"[\s_]+", "-", str(title).strip().lower())
    if not normalized:
        return False
    return normalized in _GENERIC_EXACT_TITLES or _GENERIC_TITLE_PREFIX.match(normalized) is not None
