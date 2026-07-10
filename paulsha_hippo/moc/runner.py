from __future__ import annotations

from pathlib import Path
from typing import Any

from . import faceout, linker, moc_builder, naming, search


def run_moc(memory_root: Path, now: str) -> dict[str, Any]:
    warnings: list[str] = []
    warnings.extend(naming.reconcile(memory_root, now))
    try:
        weights, linker_warnings = linker.materialize_links(memory_root)
        warnings.extend(linker_warnings)
    except Exception as exc:  # core-state corruption (relations) -> degrade
        warnings.append(f"linker degraded: {exc}")
        weights = {}
    moc_builder.build_mocs(memory_root, now)
    faceout.mark_faceout(memory_root)
    index_stats: dict[str, dict[str, float | int]] = {}
    try:
        build_stats = search.build_index(memory_root, weights)
        index_stats = {
            project: {
                "indexed": project_stats.indexed,
                "excluded": project_stats.excluded,
                "exclude_rate": project_stats.exclude_rate,
            }
            for project, project_stats in build_stats.per_project.items()
        }
        warnings.extend(build_stats.warnings)
        indexed = True
    except Exception as exc:
        warnings.append(f"search index skipped: {exc}")
        indexed = False
    return {"renamed": True, "linked": len(weights), "mocs": True,
            "faceout": True, "indexed": indexed, "warnings": warnings,
            "index_stats": index_stats}
