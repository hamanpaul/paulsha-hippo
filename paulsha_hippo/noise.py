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


# --- episodic demotion (#136 fix 4，review round 1 收緊強弱訊號): session-state句
# 非 deletion-grade ---
# 命中只把 memory_layer 從 knowledge 降層為 episodic（不刪檔），與 classify_noise
# （刪檔）刻意分離：episodic 筆記仍在磁碟上，只是被 MOC index／wakeup／janitor
# 依 `memory_layer != "knowledge"` 排除在檢索池外（見 moc/search.py）。
#
# review round 1 finding：brief 原版 SESSION_STATE_RE 對「目前狀態」「下一步」
# 「handoff」這類 bare word 沒有語境限制，會誤觸發耐久技術敘述（例：「目前狀態機的
# 初始化流程」「韌體升級的下一步是驗證 CRC」「handoff register 在 CC2674 上」）。
# 故拆成 strong／weak 兩級：
#   - strong：只有描述 session/commit 自身狀態時才通的措辭，一行命中即算
#     session-state 行（不需佐證）。
#   - weak：單獨出現在耐久技術文件裡也合理的字，需同一行內有 ≥2 個「不同」weak
#     pattern 互相佐證才算一行命中（同一 pattern 重複出現幾次都只算 1 個佐證，
#     見 review round 2 finding／_weak_hit_count）；1 行 body 只接受 strong 命中，
#     weak 訊號組合在單行下不成立（統計意義不足，且更容易被單一常見詞誤觸發）。
_STRONG_STATE_RE = re.compile(
    r"尚未 ?(?:commit|push|合併|merge)"        # 尚未 commit／push／合併／merge
    r"|待 ?(?:push|commit|合併)"                # 待 push／commit／合併
    r"|session ?結束(?:時|前|後)"                # session 結束時／前／後
    r"|本次 ?session"                           # 本次 session
    r"|session-handoff"                         # session-handoff（連字號複合詞）
    r"|session ?交接"                           # session 交接
    r"|交接狀態"                                 # 交接狀態
    r"|handoff ?狀態"                           # handoff 狀態
    r"|目前狀態[：:]"                           # 冒號分隔的狀態標頭（「目前狀態：」）
    r"|本次修改僅限"                             # 本次修改僅限……（commit 訊息式範圍陳述）
    r"|\bnot yet (?:committed|pushed|merged)\b"
    # round 2b：需要冒號／行尾分隔符才算強訊號，比照 `目前狀態：`——否則會誤觸發
    # 「handoff status register」這類硬體暫存器複合名詞（真實 CC2674 案例，
    # round 2 report 記錄為在該輪 scope 內無法修的第三個誤降級）。
    r"|\bhandoff (?:status|state|note)\b(?:\s*[：:]|$)"
    r"|\bsession (?:ended|end|handoff)\b",
    re.IGNORECASE,
)
# weak 訊號各自獨立編譯，供逐行計算「不同 pattern 命中數」使用（見 _weak_hit_count）。
# round 2b：移除裸字 `status`——英文硬體／韌體敘述常見「status register」「status
# field」，過於通用，診斷語料的真正正例沒有一個是靠裸字 status 撐起來的（見
# task-13-report.md round 2b fix report）。`handoff` 維持 weak。
_WEAK_STATE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (r"目前狀態", r"下一步", r"\bhandoff\b", r"目前", r"尚未", r"待辦")
)
# Title 規則維持 strong（單獨命中即整篇降層）；英文替代項加上 \b，避免比對到更長
# 英文單字的子字串（中文「狀態$」本來就用 $ 錨定到字尾，不受影響）。
# review round 2 (minor)：`^session-handoff\b` 是冗餘 alternative——"session-handoff"
# 本身已含 "handoff"，"-" 前後皆非 word char，`\bhandoff\b` 單獨即可命中，故移除。
_STATE_TITLE_RE = re.compile(r"\bhandoff\b|狀態$", re.IGNORECASE)
EPISODIC_RATIO = 0.5

_FENCE_LINE = re.compile(r"^(?:```|~~~)")


def _episodic_content_lines(body: str) -> list[str]:
    """`_content_lines`，但先整段剔除 fenced code block（``` / ~~~，含未閉合)。

    只用於 episodic_reason：程式碼片段裡的字面文字（如註解掉的 commit 指令）不該
    被當成 session 狀態陳述；未閉合的 fence 視為從開啟處起全部都是程式碼直到結尾。
    不動 classify_noise 共用的 `_content_lines`，避免影響其他分類器行為。
    """
    kept: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE_LINE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        kept.append(line)
    return _content_lines("\n".join(kept))


def _weak_hit_count(line: str) -> int:
    """該行內命中的「不同」weak pattern 數（distinct pattern indices，非 span 數）。

    review round 2 finding：舊版用「不重疊 span 數」計算，同一個 pattern 在同一行
    內重複出現（如「目前目前都還好」「status status」）會各自產生不重疊 span，被
    誤算成 2 次命中，湊出 session-state 判定。正確判準是「≥2 個不同 pattern」互相
    佐證，同一 pattern 出現幾次都只算 1 次；故直接對每個 pattern 判斷「該行是否至
    少命中一次」（不看次數、不看位置），再加總命中的 pattern 數。

    round 2b 更新：round 2 report 記錄的第三個殘留案例（「HANDOFF register
    （handoff status register）」，`\bhandoff\b` 與 `\bstatus\b` 兩個不同 weak
    pattern 皆命中，且同時撞上 strong 的 `\bhandoff (?:status|state|note)\b`）已在
    round 2b 解決：`status` 已從 `_WEAK_STATE_PATTERNS` 移除（過於通用，見常數旁
    註解），且 strong 的 handoff status/state/note 現在要求冒號／行尾分隔符，兩者
    合力使這行不再命中 weak 也不再命中 strong。詳見 task-13-report.md round 2b
    fix report。
    """
    return sum(1 for pat in _WEAK_STATE_PATTERNS if pat.search(line))


def _line_is_session_state(line: str) -> bool:
    """ratio 規則（≥2 行 body）下，單行是否算 session-state：strong 一擊即中；
    weak 需同一行 ≥2 個不同 pattern 互相佐證。"""
    if _STRONG_STATE_RE.search(line):
        return True
    return _weak_hit_count(line) >= 2


def episodic_reason(title: object, body: str) -> str | None:
    """session 狀態句偵測：非 deletion-grade——命中只降層 episodic，不刪。

    Precision over recall（review round 1）：
    - 標題命中 `_STATE_TITLE_RE` → 整篇強訊號，直接降層。
    - body 只有 1 行 content line 時，只接受 strong 命中；weak 訊號組合在單行下
      統計意義不足，一律不降層（`_content_lines` 需先剔除 fenced code block）。
    - body ≥2 行 content line 時，走原本的 ratio 規則（session-state 行數 /
      content 行數 ≥ EPISODIC_RATIO），但「是否算 session-state 行」改用
      strong-one-hit / weak-two-distinct-hits 判定，而非舊版單一 bare-word regex。
    """
    if _STATE_TITLE_RE.search(str(title or "").strip()):
        return "title:session-state"
    lines = _episodic_content_lines(body or "")
    if not lines:
        return None
    if len(lines) == 1:
        return "body:session-state:1/1" if _STRONG_STATE_RE.search(lines[0]) else None
    hits = sum(1 for line in lines if _line_is_session_state(line))
    if hits and hits / len(lines) >= EPISODIC_RATIO:
        return f"body:session-state:{hits}/{len(lines)}"
    return None


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
