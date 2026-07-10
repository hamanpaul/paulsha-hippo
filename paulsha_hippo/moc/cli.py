from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import census, search


def run(args: argparse.Namespace) -> int:
    tags = None  # facet tags handled by selector elsewhere; search is lexical
    try:
        hits = search.search(Path(args.memory_root), args.query, project=args.project,
                             limit=args.limit, include_decayed=args.include_decayed)
    except search.SearchIndexError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps({"results": hits}, sort_keys=True, indent=2))
    return 0


def run_index_verify(args: argparse.Namespace) -> int:
    """`hippo index verify`：三方對賬（census × coverage 報表 × DB 反查）。

    coverage 權威來源為 retrieval.db 內的 coverage 表（search.read_coverage()；
    與索引同一次 os.replace 原子發布——派生 JSON 可能因衍生失敗而 stale），
    無此表的舊版 DB 才退回讀 coverage_path() 派生 JSON。DB 反查驗搜尋面
    真相（slice_meta ↔ slices_fts 兩表 multiset 一對一 + FTS integrity-check；
    census.audit_indexed_ids）。exit 0 = 三方一致（indexed IDs == eligible
    IDs）；exit 1 = 不一致或 coverage 報表缺失（尚未跑過 dream/moc pass）。
    """
    memory_root = Path(args.memory_root)
    coverage = search.read_coverage(memory_root)
    if coverage is None:  # 舊版 DB（無 coverage 表）向後相容：讀派生 JSON
        cov_path = search.coverage_path(memory_root)
        if not cov_path.exists():
            print(json.dumps(
                {"error": "coverage report not found; run the dream/moc pass first"}))
            return 1
        coverage = json.loads(cov_path.read_text(encoding="utf-8"))
    result = census.reconcile_index(memory_root, coverage)
    print(json.dumps({
        "ok": result.ok,
        "census_files": result.census_files,
        "eligible": len(result.eligible_ids),
        "indexed": len(result.indexed_ids),
        "problems": result.problems,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1
