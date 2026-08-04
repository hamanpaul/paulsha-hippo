# paulsha_hippo/moc/entity_hub.py
"""Entity hub 同步（#107）：mentions 物化斷鏈的常態維護。

linker 把 relations ledger 的 ``mentions`` 邊物化成筆記 frontmatter 的
``[[EntityName]]`` wiki-link，但 entity 沒有對應頁面時連結一寫出來就是斷鏈；
``related:`` 每輪重物化、MOC 每輪重生成，修在筆記或 MOC 上都撐不過下一輪
dream cycle。本模組維護 knowledge 層 ``entities`` 子目錄的 entity hub 頁
作為持久解析目標：

- hub 頁 frontmatter 為 ``memory_layer: entity``——census 歸類
  ``pool:non-knowledge-layer``，linker / moc_builder / index 全部略過，
  不會被任何既有 pass 覆蓋。
- 缺頁補 stub（``entity_kind: unclassified``），既有頁只重刷「反向連結」
  段落（提及 slice 全數消失時刷成 0 篇，不留死連結）；人工或 agent 補的
  分類欄位（``entity_kind`` / ``canonical_moc`` / 描述文字）原樣保留。
- ``alias_of`` 頁視為別名：其 mentions 歸戶到 canonical 頁的反向連結
  （標註經由變體），別名頁本身只需存在。
- 含 ``#`` 的 entity（如 ``PR #54``）：wiki-link 語意中 ``#`` 是標題錨點，
  ``[[PR #54]]`` 解析為頁面 ``PR`` + 錨點 ``54``，故於前綴頁維護
  ``## <錨點>`` 段落（每輪整段重刷，slice 改名/新增 mentions 都會進場）
  承接深層連結；字面檔名頁照建，涵蓋非錨點語意的解析器。
- 兩個 pass 都只寫自家頁：路徑已存在但非本模組管理的 entity 頁一律跳過。

回報分兩軌（#101 教訓：每輪必再現的 warning 會讓 dream 永久 partial）：

- ``warnings``：暫時性 I/O 失敗（單檔讀寫錯誤）——值得讓該輪標 partial，
  比照 #16 fail-soft，單點失敗不中止整輪。
- ``stats["structural"]``：結構性、無自癒路徑的資料狀態（entity 名組不出
  合法路徑、外來檔佔位、alias 環）——每輪重現屬預期，記入 stats 供 CLI
  與 run_moc 摘要觀測，不進 warnings、不污染 dream 的 clean 判定。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..ledger import relations
from . import frontmatter_io as fio
from .naming import NAME_MAX_BYTES

HUB_DIRNAME = "entities"
_BACKLINK_HEADING = "## 反向連結"
_STUB_NOTE = "（entity-hub-sync 自動建立的 stub；分類與描述待人工或 agent 補齊。）"
# 反向連結段落：從標題行吃到下一個 H2 或檔尾，重刷時整段替換
_BACKLINK_SECTION = re.compile(r"^## 反向連結.*?(?=^## |\Z)", re.M | re.S)


def _yaml_quote(value: str) -> str:
    """雙引號 YAML scalar；跳脫規則對齊 frontmatter_io._scalar（#139）——
    換行/回車必須跳脫，否則 round-trip 後欄位值折行變形，_load_hubs 會
    認不得自己建的頁。"""
    return '"' + (value.replace("\\", "\\\\").replace('"', '\\"')
                  .replace("\n", "\\n").replace("\r", "\\r")) + '"'


def _escape_label(text: str) -> str:
    """比照 moc_builder.alias_link：中和會破壞 ``[[target|label]]`` 的字元。"""
    return text.replace("|", "｜").replace("]", "］").strip()


def _slice_meta(memory_root: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """slice_id -> {stem, title, project}，只收 knowledge slices。"""
    mapping: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    knowledge = memory_root / "knowledge"
    if not knowledge.exists():
        return mapping, warnings
    hub_root = knowledge / HUB_DIRNAME
    for path in sorted(knowledge.rglob("*.md")):
        if hub_root in path.parents:
            continue
        try:
            fm, _ = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"{path.name}: entity-hub skipped ({exc})")
            continue
        if fm.get("memory_layer") != "knowledge":
            continue
        sid = fm.get("slice_id")
        if not sid:
            continue
        title = str(fm.get("title") or fm.get("atom_title") or "").strip() or path.stem
        mapping[str(sid)] = {"stem": path.stem, "title": title,
                             "project": str(fm.get("project", "_unknown"))}
    return mapping, warnings


def _entity_mentions(memory_root: Path) -> dict[str, list[str]]:
    """entity 名 -> 提及它的 slice_id 序列（ledger 順序、去重）。"""
    mentions: dict[str, list[str]] = {}
    for edge in relations.read_edges(memory_root):
        if edge.get("type") != "mentions":
            continue
        frm = str(edge.get("from", ""))
        to = str(edge.get("to", ""))
        if not (frm.startswith("slice:") and to.startswith("entity:")):
            continue
        name = to[len("entity:"):].strip()
        sid = frm[len("slice:"):]
        if not name or not sid:
            continue
        bucket = mentions.setdefault(name, [])
        if sid not in bucket:
            bucket.append(sid)
    return mentions


def _load_hubs(hub_root: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """既有 hub 頁：entity 名 -> {path, kind, alias_of}。"""
    hubs: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    if not hub_root.exists():
        return hubs, warnings
    for path in sorted(hub_root.rglob("*.md")):
        try:
            fm, _ = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"{path.name}: entity-hub unreadable ({exc})")
            continue
        if fm.get("memory_layer") != "entity":
            continue
        name = str(fm.get("entity") or "").strip()
        if not name:
            continue
        hubs[name] = {"path": path, "kind": str(fm.get("entity_kind", "")),
                      "alias_of": (str(fm.get("alias_of") or "").strip() or None)}
    return hubs, warnings


def _hub_path(hub_root: Path, name: str) -> Path | None:
    """entity 名 -> hub 檔路徑；含 ``/`` 的名稱映成巢狀路徑，拒絕跳脫成分。"""
    parts = [p for p in name.strip().lstrip("/").split("/") if p not in ("", ".", "..")]
    if not parts:
        return None
    parts[-1] += ".md"
    if any(len(p.encode("utf-8")) > NAME_MAX_BYTES for p in parts):
        return None
    return hub_root.joinpath(*parts)


def _split_anchor(name: str) -> tuple[str, str] | None:
    """``PR #54`` -> ``("PR", "54")``；無 ``#`` 或前綴/錨點為空則 None。"""
    if "#" not in name:
        return None
    prefix, _, anchor = name.partition("#")
    prefix, anchor = prefix.rstrip(), anchor.strip()
    if not prefix or not anchor:
        return None
    return prefix, anchor


def _backlink_lines(slice_ids: list[str], slices: dict[str, dict[str, str]],
                    via: dict[str, str]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for sid in slice_ids:
        meta = slices.get(sid)
        if meta is None or meta["stem"] in seen:
            continue
        seen.add(meta["stem"])
        suffix = f"（經 {via[sid]}）" if sid in via else ""
        lines.append(f"- [[{meta['stem']}|{_escape_label(meta['title'])}]]"
                     f" — {meta['project']}{suffix}")
    return lines


def _backlink_section(lines: list[str]) -> str:
    return "\n".join([f"{_BACKLINK_HEADING}（{len(lines)} 篇筆記提及）", *lines]) + "\n"


def _with_backlinks(text: str, lines: list[str]) -> str:
    """重刷（或補上）反向連結段落，其餘內容原樣保留。

    段落已存在而 lines 為空時刷成 0 篇——提及 slice 全數消失（prune/dedupe）
    後不可殘留死連結；段落不存在且 lines 為空則不添加。
    """
    has_section = _BACKLINK_SECTION.search(text) is not None
    if not lines and not has_section:
        return text
    section = _backlink_section(lines)
    if has_section:
        return _BACKLINK_SECTION.sub(lambda _: section, text, count=1)
    if not text.endswith("\n"):
        text += "\n"
    return text + "\n" + section


def _stub_text(name: str, kind: str, now: str, lines: list[str],
               note: str = _STUB_NOTE) -> str:
    parts = ["---", "memory_layer: entity", f"entity: {_yaml_quote(name)}",
             f"entity_kind: {kind}", f"generated_ts: {now}",
             "generated_by: entity-hub-sync", "---",
             f"# {name}", "", note, ""]
    text = "\n".join(parts)
    if lines:
        text += "\n" + _backlink_section(lines)
    return text


def _anchor_section_re(anchor: str) -> "re.Pattern[str]":
    """錨點段落：``## <anchor>`` 標題行吃到下一個 H2 或檔尾。"""
    return re.compile(rf"^##[ \t]+{re.escape(anchor)}[ \t]*\n.*?(?=^## |\Z)",
                      re.M | re.S)


def _anchor_section(name: str, anchor: str, lines: list[str]) -> str:
    parts = [f"## {anchor}", f"**{name}** — 編號條目（entity-hub-sync 自動維護）。", *lines]
    return "\n".join(parts) + "\n"


def sync_entity_hubs(memory_root: Path, now: str, *,
                     apply: bool = True) -> tuple[dict[str, Any], list[str]]:
    """同步 entity hub 層，回傳 (stats, warnings)。

    - ``warnings`` 只裝暫時性 I/O 失敗；結構性跳過（見模組 docstring）記在
      ``stats["structural"]``（清單）與 ``stats["skipped_structural"]``（計數）。
    - ``stats["actions"]`` 為本輪（將）執行的動作清單，dry-run 亦填；呼叫端
      落 ledger 或 run_moc 回傳時應剝除 ``actions``/``structural`` 只留計數。
    - ``apply=False`` 為 dry-run：不寫任何檔案，只回報待辦。
    """
    warnings: list[str] = []
    slices, w = _slice_meta(memory_root)
    warnings.extend(w)
    mentions = _entity_mentions(memory_root)
    hub_root = memory_root / "knowledge" / HUB_DIRNAME
    hubs, w = _load_hubs(hub_root)
    warnings.extend(w)
    slice_stems = {meta["stem"] for meta in slices.values()}

    # 別名歸戶：canonical 反向連結 = 本名 mentions + 各變體 mentions（標註經由）
    merged: dict[str, list[str]] = {}
    via: dict[str, dict[str, str]] = {}
    for name, sids in mentions.items():
        info = hubs.get(name)
        if info and info.get("alias_of"):
            continue
        merged.setdefault(name, []).extend(sids)
    for name, sids in mentions.items():
        info = hubs.get(name)
        canonical = info.get("alias_of") if info else None
        if not canonical:
            continue
        bucket = merged.setdefault(canonical, [])
        vmap = via.setdefault(canonical, {})
        for sid in sids:
            if sid not in bucket:
                bucket.append(sid)
                vmap.setdefault(sid, name)

    stats: dict[str, Any] = {"entities": len(mentions), "created": 0, "updated": 0,
                             "anchor_sections": 0, "skipped_slice_collision": 0,
                             "skipped_structural": 0, "structural": [], "actions": []}

    def _record(action: str, name: str, path: Path) -> None:
        stats[action] += 1
        stats["actions"].append({"action": action, "entity": name,
                                 "path": str(path.relative_to(memory_root))})

    def _skip(name: str, reason: str) -> None:
        stats["skipped_structural"] += 1
        stats["structural"].append({"entity": name, "reason": reason})

    def _write(path: Path, text: str) -> None:
        if apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    created_this_run: set[Path] = set()

    # ---- pass 1：每個 canonical entity 的（字面）hub 頁 ----
    # 迭代歸戶後的 merged（含「只有變體被提及」的 canonical），
    # 別名頁本身只需存在（其 mentions 已折入 canonical 的反向連結）。
    for name, sids in merged.items():
        try:
            if name in slice_stems:
                stats["skipped_slice_collision"] += 1
                continue  # 已有同名 knowledge 筆記，連結本就解析得到
            info = hubs.get(name)
            if info and info.get("alias_of"):
                # 資料矛盾：canonical 目標自己也標成別名——不跟鏈，結構性跳過
                _skip(name, "alias 環：canonical 目標本身也是 alias 頁")
                continue
            path = info["path"] if info else _hub_path(hub_root, name)
            if path is None:
                _skip(name, "entity 名組不出合法路徑")
                continue
            lines = _backlink_lines(sids, slices, via.get(name, {}))
            if info is None:
                if path.exists():
                    _skip(name, f"{path.name} 已存在但非 entity 頁")
                    continue
                _write(path, _stub_text(name, "unclassified", now, lines))
                created_this_run.add(path)
                _record("created", name, path)
                continue
            old = path.read_text(encoding="utf-8")
            new = _with_backlinks(old, lines)
            if new != old:
                _write(path, new)
                _record("updated", name, path)
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"entity-hub: {name!r} 同步失敗（{exc}）")

    # ---- pass 2：含 # 的 entity 於前綴頁維護錨點段落（每輪整段重刷）----
    for name, sids in mentions.items():
        split = _split_anchor(name)
        if split is None:
            continue
        prefix, anchor = split
        try:
            if prefix in slice_stems:
                continue  # 錨點會落在既有筆記頁上，不動 knowledge 檔
            info = hubs.get(prefix)
            path = info["path"] if info else _hub_path(hub_root, prefix)
            if path is None:
                _skip(name, "前綴組不出合法路徑")
                continue
            lines = _backlink_lines(sids, slices, {})
            section = _anchor_section(name, anchor, lines)
            if not path.exists() and path not in created_this_run:
                note = (f"編號型條目消歧義頁：`[[{prefix} #N]]` 形式的連結"
                        "會落到本頁對應編號的段落。")
                text = _stub_text(prefix, "disambiguation", now, [], note=note)
                _write(path, text + "\n" + section)
                created_this_run.add(path)
                _record("created", prefix, path)
                stats["anchor_sections"] += 1
                continue
            if not apply and path in created_this_run:
                stats["anchor_sections"] += 1  # dry-run：頁未真的落地，僅計數
                continue
            if info is None and path not in created_this_run:
                # pass 1 同款防護：外來檔（或 frontmatter 壞損的頁）不寫
                _skip(name, f"前綴頁 {path.name} 非 entity 頁")
                continue
            old = path.read_text(encoding="utf-8")
            pattern = _anchor_section_re(anchor)
            if pattern.search(old):
                new = pattern.sub(lambda _: section, old, count=1)
            else:
                if not old.endswith("\n"):
                    old += "\n"
                new = old + "\n" + section
            if new != old:
                _write(path, new)
                stats["anchor_sections"] += 1
                stats["actions"].append({"action": "anchor", "entity": name,
                                         "path": str(path.relative_to(memory_root))})
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"entity-hub: {name!r} 錨點段落同步失敗（{exc}）")

    return stats, warnings
