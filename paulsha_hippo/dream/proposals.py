from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json
from typing import List, Any

_CANONICAL_KINDS = {"merge", "supersede", "contradiction"}


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    kind: str
    status: str
    created_ts: str
    subject_slice_ids: List[str]
    detail: dict[str, Any]
    source: str
    config_hash: str


def proposals_dir(memory_root: Path) -> Path:
    """Return path to runtime/proposals under memory_root."""
    return Path(memory_root) / "runtime" / "proposals"


def append(memory_root: Path, proposal: Proposal) -> None:
    """Write a proposal as JSON to runtime/proposals/<proposal_id>.json

    Returns None.
    """
    d = proposals_dir(memory_root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{proposal.proposal_id}.json"
    text = json.dumps(asdict(proposal), sort_keys=True, indent=2)
    # write deterministically
    with path.open("w", encoding="utf-8") as fh:
        fh.write(text)
    return None


def pending(memory_root: Path) -> List[dict[str, Any]]:
    """Return list of proposal dicts whose status == 'pending'."""
    d = proposals_dir(memory_root)
    if not d.exists():
        return []
    out: List[dict[str, Any]] = []
    for p in sorted(d.glob("*.json")):
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError:
            continue
        if data.get("status") == "pending":
            out.append(data)
    return out


def requires_approval(kind: str, *, decay_requires_approval: bool = False) -> bool:
    if kind == "decay":
        return decay_requires_approval
    return kind in _CANONICAL_KINDS
