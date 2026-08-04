from __future__ import annotations

from pathlib import Path
from typing import Any

from . import entity_hub, faceout, linker, moc_builder, naming, search


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
    try:
        hub_stats, hub_warnings = entity_hub.sync_entity_hubs(memory_root, now)
        warnings.extend(hub_warnings)
        # actions/structural 清單可達數百項，回傳/落 ledger 只留計數
        hub_summary = {k: v for k, v in hub_stats.items()
                       if k not in ("actions", "structural")}
    except Exception as exc:  # fail-soft：hub 層壞損不中止 MOC pass
        warnings.append(f"entity hub sync degraded: {exc}")
        hub_summary = {}
    faceout.mark_faceout(memory_root)
    index_stats: dict[str, dict[str, float | int]] = {}
    index_coverage: dict[str, Any] = {}
    try:
        report = search.build_index(memory_root, weights)
        index_stats = report["per_project"]
        index_coverage = {key: report[key] for key in search.COVERAGE_KEYS}
        warnings.extend(report["warnings"])
        indexed = True
    except Exception as exc:
        warnings.append(f"search index skipped: {exc}")
        indexed = False
    return {"renamed": True, "linked": len(weights), "mocs": True,
            "entity_hubs": hub_summary,
            "faceout": True, "indexed": indexed, "warnings": warnings,
            "index_stats": index_stats, "index_coverage": index_coverage}
