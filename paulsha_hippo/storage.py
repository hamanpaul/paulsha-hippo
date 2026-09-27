"""#151：memory store 物理位置檢查——偵測 memory_root 是否落在持續同步／被掃描樹內。

背景：`memory_root` 若（經 symlink 解析後）位於 Obsidian vault 等被 continuous-sync
工具逐檔掃描的目錄內，archive／runtime 等大量小檔會讓同步程序的記憶體與 page
cache 暴增（#151：WSL VM OOM）。建議佈局是 store 放在 vault 外，只把 knowledge
以 symlink 借回 vault（見 docs/storage-layout.md）。

偵測方式（皆可設定）：
  - 祖先目錄含 marker（預設 `.obsidian`／`.stfolder`／`.dropbox`）。
    `HIPPO_SYNC_MARKERS`（逗號分隔）取代預設清單；值為 `none` 時停用 marker 偵測。
  - 明列的同步根目錄：`HIPPO_SYNC_ROOTS`（以 os.pathsep 分隔，疊加於 marker 偵測）。
呼叫端顯式傳入 `markers`／`roots` 時優先於 env。

本模組只讀：僅 stat／resolve，不建立、不修改任何檔案。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence, TextIO

DEFAULT_SYNC_MARKERS: Mapping[str, str] = {
    ".obsidian": "Obsidian vault",
    ".stfolder": "Syncthing folder",
    ".dropbox": "Dropbox folder",
}
CONFIGURED_ROOT_KIND = "configured sync root"

# 會大量累積小檔、不該被同步工具掃描的子樹；relocate（symlink）到他處時需個別檢查。
HEAVY_SURFACES: tuple[str, ...] = ("archive", "runtime", "inbox", "hooks", "log")
# 建議佈局中「刻意借回 vault」的子樹：落在同步樹內只列為資訊，不警示。
BORROWED_SURFACES: tuple[str, ...] = ("knowledge",)

_DISABLE_VALUES = {"none", "off", "-"}


@dataclass(frozen=True)
class SyncContainer:
    root: Path
    kind: str
    marker: str | None = None


@dataclass(frozen=True)
class Exposure:
    surface: str
    path: Path
    resolved: Path
    container: SyncContainer


@dataclass(frozen=True)
class PlacementReport:
    memory_root: Path
    resolved_root: Path
    warnings: list[Exposure] = field(default_factory=list)
    infos: list[Exposure] = field(default_factory=list)


def sync_markers_from_env() -> tuple[str, ...]:
    raw = os.environ.get("HIPPO_SYNC_MARKERS", "").strip()
    if not raw:
        return tuple(DEFAULT_SYNC_MARKERS)
    if raw.lower() in _DISABLE_VALUES:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def sync_roots_from_env() -> tuple[Path, ...]:
    raw = os.environ.get("HIPPO_SYNC_ROOTS", "").strip()
    if not raw:
        return ()
    return tuple(Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip())


def _resolve(path: Path) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):  # RuntimeError：symlink loop（舊版 Python）
        return Path(os.path.abspath(Path(path).expanduser()))


def _marker_label(marker: str) -> str:
    return DEFAULT_SYNC_MARKERS.get(marker, f"sync marker {marker}")


def find_sync_container(
    path: Path,
    *,
    markers: Sequence[str] | None = None,
    roots: Iterable[Path] | None = None,
) -> SyncContainer | None:
    """回傳 `path`（解析 symlink 後）所在的同步／掃描樹；不在任何樹內回 None。"""
    marker_list = tuple(markers) if markers is not None else sync_markers_from_env()
    root_list = tuple(roots) if roots is not None else sync_roots_from_env()
    resolved = _resolve(path)
    for configured in root_list:
        root = _resolve(configured)
        if resolved == root or root in resolved.parents:
            return SyncContainer(root=root, kind=CONFIGURED_ROOT_KIND)
    if not marker_list:
        return None
    for ancestor in (resolved, *resolved.parents):
        for marker in marker_list:
            try:
                present = os.path.lexists(ancestor / marker)
            except OSError:
                present = False
            if present:
                return SyncContainer(root=ancestor, kind=_marker_label(marker), marker=marker)
    return None


def check_placement(
    memory_root: Path,
    *,
    markers: Sequence[str] | None = None,
    roots: Iterable[Path] | None = None,
) -> PlacementReport:
    """檢查 memory_root 與其子樹的實際落點。

    - memory_root 本身（解析後）在同步樹內 → warning（#151 根因形態）。
    - heavy 子樹被 symlink 搬到同步樹內 → warning。
    - knowledge 在同步樹內而 store 本體不在 → info（建議佈局：只借回 knowledge）。
    """
    marker_list = tuple(markers) if markers is not None else sync_markers_from_env()
    root_list = tuple(roots) if roots is not None else sync_roots_from_env()
    memory_root = Path(memory_root).expanduser()
    resolved_root = _resolve(memory_root)
    report = PlacementReport(memory_root=memory_root, resolved_root=resolved_root)

    def _probe(path: Path) -> SyncContainer | None:
        return find_sync_container(path, markers=marker_list, roots=root_list)

    root_container = _probe(memory_root)
    if root_container is not None:
        report.warnings.append(Exposure("memory_root", memory_root, resolved_root, root_container))
    for name in HEAVY_SURFACES:
        candidate = memory_root / name
        if not candidate.is_symlink():
            continue  # 仍在 memory_root 底下：由上面的 memory_root 檢查涵蓋
        container = _probe(candidate)
        if container is not None:
            report.warnings.append(Exposure(name, candidate, _resolve(candidate), container))
    if root_container is None:
        for name in BORROWED_SURFACES:
            candidate = memory_root / name
            if not os.path.lexists(candidate):
                continue
            container = _probe(candidate)
            if container is not None:
                report.infos.append(Exposure(name, candidate, _resolve(candidate), container))
    return report


def _describe(exposure: Exposure) -> str:
    container = exposure.container
    detail = f"，偵測到 {container.marker}" if container.marker else ""
    via = "" if exposure.resolved == _resolve_lexical(exposure.path) else f"（經 symlink 解析為 {exposure.resolved}）"
    return f"{exposure.surface} {exposure.path}{via} 落在 {container.kind}（{container.root}{detail}）內"


def _resolve_lexical(path: Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def placement_lines(report: PlacementReport) -> list[str]:
    """doctor 用的報告行（只報告，不影響 exit code）。"""
    lines: list[str] = []
    if not report.warnings:
        lines.append(f"- storage 位置：✓ store 不在已知同步／掃描樹內（{report.resolved_root}）")
    for exposure in report.warnings:
        lines.append(
            f"- storage 位置：⚠ {_describe(exposure)}；持續同步工具會逐檔掃描整個子樹"
            "（#151 OOM 形態）——建議把 memory_root 移出同步樹、只把 knowledge 以 symlink 借回，"
            "見 docs/storage-layout.md"
        )
    for exposure in report.infos:
        lines.append(f"- storage 位置：knowledge 借回 {exposure.container.kind}"
                     f"（{exposure.container.root}）——建議佈局")
    return lines


def warn_if_exposed(memory_root: Path, *, stream: TextIO | None = None) -> bool:
    """啟動檢查點用：store 落在同步樹內時對 stderr 印一行警示；回傳是否有警示。

    任何偵測失敗都不得中斷呼叫端（best-effort）。
    """
    out = stream if stream is not None else sys.stderr
    try:
        report = check_placement(memory_root)
    except Exception:  # noqa: BLE001 — 啟動檢查點只做提醒，失敗不影響主流程
        return False
    for exposure in report.warnings:
        print(f"[paulsha-hippo] WARN storage：{_describe(exposure)}；"
              "建議移出同步樹（hippo doctor／docs/storage-layout.md，#151）", file=out)
    return bool(report.warnings)
