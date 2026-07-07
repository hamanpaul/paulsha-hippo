from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import search


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
