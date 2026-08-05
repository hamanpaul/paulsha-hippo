"""Module for normalizing non-string tags in knowledge slices."""

from __future__ import annotations

from pathlib import Path
from paulsha_hippo.atomizer.slice_frontmatter import normalize_tags
from paulsha_hippo.moc import frontmatter_io as fio


def normalize_tags_migration(root_dir: Path | str, apply: bool = False) -> tuple[dict, list[str]]:
    """Scan knowledge slices for non-string tags and normalize them.

    Returns (summary_dict, warnings_list).
    """
    root = Path(root_dir)
    knowledge = root / "knowledge"
    search_root = knowledge if knowledge.exists() else root

    scanned = 0
    pending_details: list[dict] = []
    warnings: list[str] = []
    updated = 0

    if search_root.exists():
        for path in sorted(search_root.rglob("*.md")):
            if path.name.endswith("-moc.md"):
                continue
            try:
                content = path.read_text(encoding="utf-8")
                fm, _body = fio.read(content)
            except (OSError, UnicodeDecodeError) as exc:
                warnings.append(f"Failed to read {path}: {exc}")
                continue

            # 無條件過濾（repo 慣例，比照 rekey.py/linker.py）：即使 knowledge
            # 子目錄不存在而 fallback 掃 root，也只碰 memory_layer == "knowledge"
            # 的 slice，--memory-root 打錯時不會改寫 inbox、episodic 或一般文件
            # （issue #109 review）。
            if fm.get("memory_layer") != "knowledge":
                continue

            scanned += 1

            raw_tags = fm.get("tags")
            has_non_string = False
            if isinstance(raw_tags, list):
                has_non_string = any(not isinstance(t, str) for t in raw_tags)
            elif raw_tags is not None:
                has_non_string = True

            if has_non_string:
                norm_tags = normalize_tags(raw_tags)
                try:
                    rel_path = str(path.relative_to(root))
                except ValueError:
                    rel_path = str(path)

                pending_details.append({
                    "path": rel_path,
                    "normalized_tags": norm_tags,
                })

                if apply:
                    try:
                        fio.update(path, {"tags": norm_tags})
                        updated += 1
                    except Exception as exc:
                        warnings.append(f"Failed to update {path}: {exc}")

    summary = {
        "scanned": scanned,
        "pending": len(pending_details),
        "updated": updated,
        "details": pending_details,
    }
    return summary, warnings

