"""``hippo show``：knowledge note 的 ref 解析與 agent 精簡視圖渲染。

問題脈絡（issue #136）：knowledge note 79% 是 frontmatter bytes（其中
`distiller` 區塊佔 54%），agent 用 `Read` 讀一筆 note 要多付 ~3x token。
`render_agent_view` 只印人類語意需要的最小 header（不含
`distiller`/`checksum`/`publication_id`/`distilled_from` 等機器歸因欄位）
＋原文 body。
"""
from __future__ import annotations

from pathlib import Path

from paulsha_hippo.moc import frontmatter_io as fio


class ShowError(Exception):
    pass


def resolve_ref(memory_root: Path, ref: str) -> Path:
    """``ref`` 是既存路徑則直接用；否則視為 slice_id，於 knowledge 目錄下唯一比對檔名。"""
    candidate = Path(ref)
    if candidate.is_file():
        return candidate
    hits = (
        sorted((memory_root / "knowledge").rglob(f"*--{ref}.md"))
        if (memory_root / "knowledge").is_dir()
        else []
    )
    if len(hits) != 1:
        raise ShowError(
            f"show: {ref}: {'not found' if not hits else 'ambiguous'} ({len(hits)} match)"
        )
    return hits[0]


def _cites(fm: dict) -> str:
    items = fm.get("cites") if isinstance(fm.get("cites"), list) else []
    return ", ".join(
        f"{c.get('path')}:{c.get('line')}" for c in items if isinstance(c, dict) and c.get("path")
    )


def render_agent_view(path: Path) -> str:
    """精簡 header（人類語意欄位＋六鍵 provenance 中的 commit/commit_source）＋原文 body。"""
    fm, body = fio.read(path.read_text(encoding="utf-8"))
    prov = fm.get("provenance") if isinstance(fm.get("provenance"), dict) else {}
    commit = str(prov.get("commit") or "_unknown")
    if prov.get("commit_source"):
        commit = f"{commit}({prov['commit_source']})"
    header = [
        f"# {fm.get('title') or fm.get('atom_title') or path.stem}",
        " | ".join(
            [
                f"slice_id: {fm.get('slice_id', '')}",
                f"project: {fm.get('project', '')}",
                f"captured_at: {fm.get('captured_at', '')}",
                f"artifact_kind: {fm.get('artifact_kind', '')}",
                f"supersedes: {fm.get('supersedes') or []}",
                f"commit: {commit}",
            ]
        ),
    ]
    cites = _cites(fm)
    if cites:
        header.append(f"cites: {cites}")
    header.append("---")
    return "\n".join(header) + "\n" + body
