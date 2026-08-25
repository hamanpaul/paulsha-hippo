"""issue-136-knowledge-hygiene flags：從 canonical config.yaml best-effort 讀取，缺鍵／壞檔一律預設。"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from paulsha_hippo import paths

_READ_HINTS = ("read", "show")


@dataclass(frozen=True)
class HygieneFlags:
    collapse_same_topic: bool = True
    read_hint: str = "show"
    followups_enabled: bool = True
    episodic_filter: bool = True


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def load_flags(config_path: Path | None = None) -> HygieneFlags:
    path = Path(config_path) if config_path is not None else paths.atomizer_config_path()
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return HygieneFlags()
    data = _mapping(data)
    shortlist = _mapping(data.get("shortlist"))
    followups = _mapping(data.get("followups"))
    hint = shortlist.get("read_hint")
    return HygieneFlags(
        collapse_same_topic=_bool(shortlist.get("collapse_same_topic"), True),
        read_hint=hint if hint in _READ_HINTS else "show",
        followups_enabled=_bool(followups.get("enabled"), True),
        episodic_filter=_bool(data.get("episodic_filter"), True),
    )
