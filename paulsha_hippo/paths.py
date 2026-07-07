"""單一權威路徑 resolver（spec §5.3 路徑契約，防 split-brain）。

所有表面（CLI、hooks、systemd/supervisor 服務）一律經此解析 memory root。
優先序（高→低）：
  1. 呼叫端顯式參數（CLI 旗標層，呼叫端自行傳入）
  2. ``HIPPO_<NAME>`` env
  3. ``PSC_<NAME>`` env（deprecated——讀到即 stderr 警告一次）
  4. ``~/.config/paulsha-hippo/config.yaml`` 的對應鍵（僅 memory_root）
  5. path-split 契約預設（``~/.agents/memory`` 等——與 paulshaclaw 零資料遷移）

lib 隔離：本模組屬 hippo 本體（非 lib），lib/** 不得 import 之。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PathPart = str | os.PathLike[str]

_warned_psc: set[str] = set()


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return Path(value).expanduser()


def _dual_env(hippo_name: str, psc_name: str) -> Path | None:
    hippo = _env_path(hippo_name)
    if hippo is not None:
        return hippo
    psc = _env_path(psc_name)
    if psc is not None:
        if psc_name not in _warned_psc:
            _warned_psc.add(psc_name)
            print(
                f"[paulsha-hippo] {psc_name} 已 deprecated，請改用 {hippo_name}（本次沿用）",
                file=sys.stderr,
            )
        return psc
    return None


def _config_yaml_memory_root() -> Path | None:
    cfg = hippo_config_root() / "config.yaml"
    if not cfg.is_file():
        return None
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("memory_root:"):
                value = stripped.split(":", 1)[1].strip().strip("\"'")
                if value:
                    return Path(value).expanduser()
    except OSError:
        return None
    return None


def home_root() -> Path:
    return Path.home()


def hippo_config_root() -> Path:
    override = _env_path("HIPPO_CONFIG_ROOT")
    if override is not None:
        return override
    return home_root() / ".config" / "paulsha-hippo"


def agents_root() -> Path:
    return _dual_env("HIPPO_AGENTS_ROOT", "PSC_AGENTS_ROOT") or home_root() / ".agents"


def agents_path(*parts: PathPart) -> Path:
    return agents_root().joinpath(*parts)


def memory_root() -> Path:
    env = _dual_env("HIPPO_MEMORY_ROOT", "PSC_MEMORY_ROOT")
    if env is not None:
        return env
    from_config = _config_yaml_memory_root()
    if from_config is not None:
        return from_config
    return agents_path("memory")


def memory_path(*parts: PathPart) -> Path:
    return memory_root().joinpath(*parts)


def notes_root() -> Path:
    return _dual_env("HIPPO_NOTES_ROOT", "PSC_NOTES_ROOT") or home_root() / "notes"


def copilot_root() -> Path:
    env = _dual_env("HIPPO_COPILOT_ROOT", "PSC_COPILOT_ROOT")
    if env is not None:
        return env
    legacy_base = _env_path("PSC_CONFIG_ROOT")
    if legacy_base is not None:
        if legacy_base.name == "paulshaclaw" and legacy_base.parent.name == ".config":
            return legacy_base.parents[1] / ".copilot"
        return legacy_base / ".copilot"
    return home_root() / ".copilot"


def extra_corpus_root() -> Path | None:
    return _dual_env("HIPPO_EXTRA_CORPUS_ROOT", "PSC_EXTRA_CORPUS_ROOT")


def config_root() -> Path:
    """paulshaclaw 相容 config 目錄（state/secret 檔沿用，零遷移）。"""
    legacy = _env_path("PSC_CONFIG_ROOT")
    if legacy is not None:
        if legacy.name == "paulshaclaw" and legacy.parent.name == ".config":
            return legacy
        return legacy / ".config" / "paulshaclaw"
    return home_root() / ".config" / "paulshaclaw"


def config_path(*parts: PathPart) -> Path:
    return config_root().joinpath(*parts)


def projects_config_path(memory_root_value: str | Path | None = None) -> Path:
    """projects.yaml 定位——沿 paulshaclaw facade 契約（legacy 優先序不變）。"""
    legacy_base = _env_path("PSC_CONFIG_ROOT")
    if legacy_base is not None:
        if legacy_base.name == "paulshaclaw" and legacy_base.parent.name == ".config":
            base = legacy_base.parents[1]
        else:
            base = legacy_base
        return base / ".agents" / "config" / "projects.yaml"
    if memory_root_value is not None:
        return Path(memory_root_value).expanduser().parent / "config" / "projects.yaml"
    return agents_path("config", "projects.yaml")


def resolution_report() -> dict[str, str]:
    """doctor 用：各表面實際解析結果 + 雙 root 衝突偵測素材。"""
    report = {
        "memory_root": str(memory_root()),
        "agents_root": str(agents_root()),
        "hippo_config_root": str(hippo_config_root()),
        "legacy_config_root": str(config_root()),
    }
    hippo_env = _env_path("HIPPO_MEMORY_ROOT")
    psc_env = _env_path("PSC_MEMORY_ROOT")
    if hippo_env is not None and psc_env is not None and hippo_env != psc_env:
        report["conflict"] = f"HIPPO_MEMORY_ROOT={hippo_env} != PSC_MEMORY_ROOT={psc_env}"
    return report
