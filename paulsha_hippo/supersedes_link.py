"""跨 session 同主題 supersedes backfill（fix 2b，issue #136）。

`atomizer/pipeline.py::_attach_unambiguous_supersedes` 只在 publish 當下看得到
「這一輪的新 slice」與「已經在磁碟上的舊 note」，且只連**恰一個**無歧義的前身。
既存記憶庫裡已經堆了大量跨 session 的同主題重蒸版本，它們當初被舊條件
（`distilled_from` 必須相等）擋在門外，永遠平行存在——janitor 的
`decay_superseded` 因此從未對它們觸發，shortlist 也就一直同時吐新舊兩份。

本模組是那批既存 note 的一次性補寫，分兩層：

- **auto**：同 family ∧（canonical title 或 alias 完全相等）∧ captured_at 嚴格
  較新 ∧ checksum 不同 ∧ 舊者未 decay/archive ∧ 新者尚未連過該舊者，且這組配對
  **雙向唯一**（新者只有這一個較舊候選、舊者也只有這一個較新候選）。雙向唯一是
  刻意的：三份同標題 note（v1 -> v2 -> v3）在單向規則下會讓 v2 自動吃掉 v1，但
  v3 才是真正的現行版本，v2 該不該存活是人的判斷，不是機器的。
- **review**：其餘同主題候選（title Jaccard、tags 交集 ≥ 2、related 互指、或
  tags 交集 ≥ 1 且標題 token 交集 ≥ 2）只寫報表，等人 `--accept` 才落地。

同主題判定一律走 `paulsha_hippo/topic.py`（Task 7/8 的唯一 matcher），本檔不自建
第二套啟發式；跨專案配對只透過 `projects.yaml` 的 `families:` 開通（opt-in），
沒設 families 時不同專案永遠不成對。

安全性：候選要求 `captured_at` 嚴格較新（`topic._recency_key` 的語意：缺值或無法
解析視為最舊，因此永遠不會成為任何配對的一端），所以 scan 產出的關係是嚴格偏序、
天生無環也無自連；`apply_pairs` 另外對手改過的報表再驗一次（self-link 與可達性
成環都拒絕）。已經寫過的配對會被跳過，apply -> dry-run 回報 0 -> 再 apply 為
no-op。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from paulsha_hippo import topic
from paulsha_hippo.ledger import lifecycle, relations
from paulsha_hippo.moc import frontmatter_io as fio

#: 寫進 relations ledger 的 `atomizer_config_hash` 欄位，標示這條邊來自本 backfill
#: 而不是 atomizer 的 publish transaction。
CONFIG_HASH = "link-supersedes"

#: `fold_lifecycle` 判定為「已退場」的狀態；退場的 note 不再是 supersede 目標
#: （已經 decay 過的東西沒必要再標一次前身）。
_DEAD_STATES = ("decayed", "archived")


def _load(root: Path) -> list[dict[str, Any]]:
    """讀 `<root>/knowledge` 下所有 `memory_layer == "knowledge"` 的 note。

    掃描形狀比照 `provenance_backfill` / `episodic_migration`：`-moc.md` 索引頁
    不是獨立原子筆記，一律跳過；讀不到或非 UTF-8 的檔案跳過而不拋例外。
    """
    notes: list[dict[str, Any]] = []
    knowledge = root / "knowledge"
    if not knowledge.is_dir():
        return notes
    for path in sorted(knowledge.rglob("*.md")):
        if path.name.endswith("-moc.md"):
            continue
        try:
            fm, _body = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if fm.get("memory_layer") != "knowledge" or not fm.get("slice_id"):
            continue
        notes.append({
            "slice_id": str(fm["slice_id"]),
            "project": str(fm.get("project") or ""),
            "title": str(fm.get("title") or fm.get("atom_title") or ""),
            "aliases": [str(a) for a in (fm.get("aliases") or []) if isinstance(a, str)],
            "tags": {str(t) for t in (fm.get("tags") or []) if isinstance(t, str)},
            "related": {str(r).strip("[]") for r in (fm.get("related") or []) if isinstance(r, str)},
            "captured_at": str(fm.get("captured_at") or ""),
            "checksum": str(fm.get("checksum") or ""),
            "supersedes": [str(s) for s in (fm.get("supersedes") or [])],
            "path": path,
        })
    return notes


def _dead(root: Path) -> set[str]:
    """已 decay/archive 的 slice_id 集合（讀不到 ledger 時視為空集合）。"""
    try:
        events = lifecycle.read_events(root / "runtime" / "ledger" / "lifecycle.jsonl")
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    return {
        record_id
        for record_id, state in lifecycle.fold_lifecycle(events).items()
        if state.get("last_state") in _DEAD_STATES
    }


def _names(note: dict[str, Any]) -> set[str]:
    """note 的 canonical 名稱集合（title ＋ aliases），空字串不算名字。"""
    return {n for n in (topic.canonical_title(x) for x in [note["title"], *note["aliases"]]) if n}


def _jaccard(new: dict[str, Any], old: dict[str, Any]) -> float:
    """報表用的 token Jaccard 比值；門檻判定本身仍由 `topic.is_same_topic` 決定。"""
    union = new["_tokens"] | old["_tokens"]
    return len(new["_tokens"] & old["_tokens"]) / len(union) if union else 0.0


def _can_be_fuzzy(new: dict[str, Any], old: dict[str, Any]) -> bool:
    """`_fuzzy_reason` 的必要條件預篩（結果等價，只是省算）。

    四個 review 理由沒有一個能在「標題 token 無交集 ∧ tags 無交集 ∧ 非 related
    互指」時成立：Jaccard ≥ 0.6 需要 token 有交集，`tags:n` 與 `tags+title` 需要
    tags 有交集，`related` 需要互指。掃描是 O(n²) 配對，這一關讓絕大多數毫不相干
    的配對不必進到 `topic.is_same_topic` 的重算。
    """
    return bool(new["_tokens"] & old["_tokens"]) or bool(new["tags"] & old["tags"]) or (
        old["slice_id"] in new["related"] and new["slice_id"] in old["related"])


def _fuzzy_reason(new: dict[str, Any], old: dict[str, Any],
                  families: Iterable[Iterable[str]]) -> str | None:
    """非完全同名的同主題理由；不成立回 ``None``。

    兩個引數都是 `scan` 準備過的 note（帶 ``_tokens`` 等衍生欄位）。
    判定順序＝證據強度：`topic.is_same_topic` 的 Jaccard > 共同 tags ≥ 2 >
    related 互指 > 「有共同 tag 且標題 token 交集 ≥ 2」的弱訊號。最後一層是設計
    文件的參考案例（`ot-ti-mirror Flash Layout and Image Artifacts` vs
    `雙目標 Flash Layout`：Jaccard 只有 0.33、共同 tag 只有一個）要能進 review
    的原因，強度最弱所以永遠只出報表、不會進 auto。
    """
    if topic.is_same_topic(new, old, families=families):
        return f"jaccard:{_jaccard(new, old):.2f}"
    shared_tags = new["tags"] & old["tags"]
    if len(shared_tags) >= 2:
        return f"tags:{len(shared_tags)}"
    if old["slice_id"] in new["related"] and new["slice_id"] in old["related"]:
        return "related"
    if shared_tags and len(new["_tokens"] & old["_tokens"]) >= 2:
        return "tags+title"
    return None


def _pair(new: dict[str, Any], old: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "pair_id": f"{new['slice_id']}~{old['slice_id']}",
        "new": new["slice_id"],
        "old": old["slice_id"],
        "new_title": new["title"],
        "old_title": old["title"],
        "project_new": new["project"],
        "project_old": old["project"],
        "captured_at_new": new["captured_at"],
        "captured_at_old": old["captured_at"],
        "reason": reason,
    }


def scan(memory_root: Path | str, *, families: Iterable[Iterable[str]] = ()) -> dict[str, list[dict[str, Any]]]:
    """掃既存 knowledge note，回傳 ``{"auto": [pair...], "review": [pair...]}``。

    純讀取：不寫任何檔案、不碰 ledger。``families`` 是 `projects.yaml` 的
    `families:`（同義專案分組）；不給就等同「不跨專案配對」。
    """
    root = Path(memory_root)
    families = tuple(tuple(str(m) for m in fam) for fam in families)
    notes = _load(root)
    dead = _dead(root)
    # 每則 note 只算一次同主題判定需要的衍生值；掃描本身是 O(n²) 配對，把
    # tokenize/parse 留在內圈會讓真實記憶庫（數千則）的一次 dry-run 慢到不可用。
    for note in notes:
        note["_rk"] = topic._recency_key(note["captured_at"])
        note["_names"] = _names(note)
        note["_tokens"] = topic.title_tokens(note["title"], note["project"])
        note["_fkey"] = topic.family_key(note["project"], families)
    # 決定性掃描順序：先舊後新，同時間戳以 slice_id 破平手。排序後「較舊的候選」
    # 必定落在自己前面，內圈只掃 notes[:i]。
    notes.sort(key=lambda n: (n["_rk"], n["slice_id"]))

    exact: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    fuzzy: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for index, new in enumerate(notes):
        for old in notes[:index]:
            if old["slice_id"] == new["slice_id"]:
                continue
            if old["slice_id"] in dead or old["slice_id"] in new["supersedes"]:
                continue
            # 嚴格較舊才是候選：等值/缺值（_recency_key 視為最舊）一律不成對，
            # 這同時保證整張圖是嚴格偏序 —— 不可能自連，也不可能成環。
            if old["_rk"] >= new["_rk"]:
                continue
            if old["checksum"] and old["checksum"] == new["checksum"]:
                continue
            if old["_fkey"] != new["_fkey"]:
                continue
            if new["_names"] & old["_names"]:
                same_title = topic.canonical_title(new["title"]) == topic.canonical_title(old["title"])
                exact.append((new, old, "exact-title" if same_title else "alias"))
                continue
            if not _can_be_fuzzy(new, old):
                continue
            reason = _fuzzy_reason(new, old, families)
            if reason:
                fuzzy.append((new, old, reason))

    # auto 要求雙向唯一：新者只有這一個較舊候選，舊者也只有這一個較新候選。
    olds_per_new: dict[str, int] = {}
    news_per_old: dict[str, int] = {}
    for new, old, _reason in exact:
        olds_per_new[new["slice_id"]] = olds_per_new.get(new["slice_id"], 0) + 1
        news_per_old[old["slice_id"]] = news_per_old.get(old["slice_id"], 0) + 1

    auto: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for new, old, reason in exact:
        if olds_per_new[new["slice_id"]] == 1 and news_per_old[old["slice_id"]] == 1:
            auto.append(_pair(new, old, reason))
        else:
            review.append(_pair(new, old, reason))
    review.extend(_pair(new, old, reason) for new, old, reason in fuzzy)
    return {"auto": auto, "review": review}


def _would_cycle(by_id: dict[str, dict[str, Any]], new_id: str, old_id: str) -> bool:
    """從 ``old_id`` 沿 supersedes 走是否走得回 ``new_id``（走得回就會成環）。

    `scan` 產出的配對天生無環（嚴格 recency 偏序），但 `--accept` 吃的是人手改過
    的報表，這裡再驗一次才敢寫。
    """
    seen: set[str] = set()
    stack = [old_id]
    while stack:
        current = stack.pop()
        if current == new_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        node = by_id.get(current)
        if node:
            stack.extend(node["supersedes"])
    return False


def apply_pairs(memory_root: Path | str, pairs: list[dict[str, Any]], *, now: str) -> int:
    """把配對寫進新者的 ``supersedes`` 並補一條 relations edge，回傳實際寫入筆數。

    只走 `frontmatter_io.update()`（parse-equivalent、body 逐位元不變、atomic
    write）。已含該前身、自連、指向不存在的 slice、或會造成環的配對一律跳過，
    所以重跑同一批配對必定回 0。
    """
    root = Path(memory_root)
    by_id = {n["slice_id"]: n for n in _load(root)}
    written = 0
    for pair in pairs:
        new_id = str(pair.get("new") or "")
        old_id = str(pair.get("old") or "")
        new = by_id.get(new_id)
        if not new or not old_id or new_id == old_id or old_id not in by_id:
            continue
        if old_id in new["supersedes"] or _would_cycle(by_id, new_id, old_id):
            continue
        merged = sorted(set(new["supersedes"] + [old_id]))
        fio.update(new["path"], {"supersedes": merged})
        new["supersedes"] = merged
        relations.append_edge(root, type="supersedes", frm=f"slice:{new_id}",
                              to=f"slice:{old_id}", now=now, config_hash=CONFIG_HASH)
        written += 1
    return written


def _report_stem(now: str) -> str:
    return "link-supersedes-" + "".join(c for c in now if c.isalnum() or c in "-T")


def write_report(memory_root: Path | str, review_pairs: list[dict[str, Any]], *, now: str) -> Path:
    """寫 review 報表，回傳 `.jsonl` 路徑（同名 `.md` 是給人看的那一份）。

    `.jsonl` 每行是一個 pair ＋ ``"accept": false``；人把要套用的那幾行改成
    ``true`` 之後餵給 `apply_accepted`。報表寫在 `<root>/runtime/reports/` 下，
    不碰任何 knowledge note。
    """
    root = Path(memory_root)
    reports = root / "runtime" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stem = _report_stem(now)
    jsonl = reports / f"{stem}.jsonl"
    jsonl.write_text(
        "".join(json.dumps({**pair, "accept": False}, ensure_ascii=False) + "\n" for pair in review_pairs),
        encoding="utf-8",
    )
    header = ("# link-supersedes review pairs\n\n"
              f"generated: {now}  |  pairs: {len(review_pairs)}\n\n"
              "把 `.jsonl` 中要套用的那幾行的 `\"accept\": false` 改成 `true`，再跑\n"
              "`hippo knowledge link-supersedes --memory-root R --accept <此報表>.jsonl`。\n\n"
              "| pair id | reason | new (supersedes) | old (superseded) | project | captured_at |\n"
              "|---|---|---|---|---|---|\n")
    rows = "".join(
        f"| {p['pair_id']} | {p['reason']} "
        f"| {p['new']}<br>{_md_cell(p['new_title'])} | {p['old']}<br>{_md_cell(p['old_title'])} "
        f"| {p['project_new']} / {p['project_old']} "
        f"| {p['captured_at_new']} / {p['captured_at_old']} |\n"
        for p in review_pairs
    )
    (reports / f"{stem}.md").write_text(header + rows, encoding="utf-8")
    return jsonl


def _md_cell(text: str) -> str:
    """把標題塞進 markdown 表格 cell：跳脫 `|`，摺掉換行。"""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def apply_accepted(memory_root: Path | str, report: Path | str, *, now: str) -> int:
    """套用報表中 ``accept: true`` 的配對，回傳實際寫入筆數。"""
    lines = Path(report).read_text(encoding="utf-8").splitlines()
    accepted = []
    for line in lines:
        if not line.strip():
            continue
        try:
            pair = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(pair, dict) and pair.get("accept") is True:
            accepted.append(pair)
    return apply_pairs(memory_root, accepted, now=now)
