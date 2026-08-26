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

`captured_at` 一律先經 `topic.recency_key` 解析再比大小（真實記憶庫混用 `Z`、
`+08:00` 與 YAML round-trip 後的空白分隔寫法，字串序在它們之間不成立），分三態：

- **缺值或無法解析**：被壓到時間地板（`datetime.min`）。地板上的兩則 note 不是
  「同時」，只是同樣不知道時間，所以任何一端在地板上就兩層都不配對。
- **嚴格較舊**：正常候選，依理由分 auto/review。
- **完全相等**：`distilled_from` 相同者是同一場 session 蒸出的兄弟（publish 當下
  已經判過彼此關係），跳過；不同者是兩個版本但時間分不出先後，一律進 review
  （reason `equal-captured_at`），方向以 slice_id 字典序決定（大者當新）——任意
  但決定性，讓人在報表上改方向，而不是每跑一次換一個方向。

auto 只吃「嚴格較舊」那一態，所以它產出的關係是嚴格 recency 偏序、天生無環也無
自連；`apply_pairs` 另外對手改過的報表再驗一次（self-link 與可達性成環都拒絕）。
已經寫過的配對會被跳過，apply -> dry-run 回報 0 -> 再 apply 為 no-op。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from paulsha_hippo import topic
from paulsha_hippo.ledger import lifecycle, relations
from paulsha_hippo.moc import frontmatter_io as fio

#: 寫進 relations ledger 的 `atomizer_config_hash` 欄位，標示這條邊來自本 backfill
#: 而不是 atomizer 的 publish transaction。
CONFIG_HASH = "link-supersedes"

#: `fold_lifecycle` 判定為「已退場」的狀態。退場的 note 兩側都不參與配對：
#: 當舊者是因為「已經 decay 過的東西沒必要再標一次前身」，當新者是因為一個已經
#: 退場的 note 沒有資格宣稱自己取代了還活著的 note。
_DEAD_STATES = ("decayed", "archived")

#: `topic.recency_key` 對缺值/無法解析的 captured_at 回傳的時間地板。地板上的
#: note 之間沒有可比的先後，也不算「同時」，一律不配對。
_TIME_FLOOR = datetime.min

#: 時間戳完全相等、但來自不同 session 的配對理由（永遠只進 review）。
_EQUAL_TIME_REASON = "equal-captured_at"


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
            # related 原樣留著，等 `_resolve_related` 拿到全體 slice_id 才解得開。
            "related": [str(r) for r in (fm.get("related") or []) if isinstance(r, str)],
            "captured_at": str(fm.get("captured_at") or ""),
            "checksum": str(fm.get("checksum") or ""),
            "distilled_from": str(fm.get("distilled_from") or ""),
            "supersedes": [str(s) for s in (fm.get("supersedes") or [])],
            "path": path,
        })
    return notes


def _dead(root: Path) -> set[str]:
    """已 decay/archive 的 slice_id 集合。

    讀不到或解不開 ledger 時退回空集合（掃描不該因為 ledger 壞掉就整個停擺），但
    要在 stderr 講一聲：空集合的意思是「沒有任何 note 退場過」，而那正好會讓已經
    decay 的 note 重新變成候選——靜悄悄吞掉這個差別會讓報表看起來莫名其妙變長。
    """
    ledger = root / "runtime" / "ledger" / "lifecycle.jsonl"
    try:
        events = lifecycle.read_events(ledger)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"warning: link-supersedes: 讀不到 lifecycle ledger（{ledger}）：{exc}；"
              "本次掃描視為沒有任何 decayed/archived note", file=sys.stderr)
        return set()
    return {
        record_id
        for record_id, state in lifecycle.fold_lifecycle(events).items()
        if state.get("last_state") in _DEAD_STATES
    }


def _resolve_related(notes: list[dict[str, Any]]) -> None:
    """把每則 note 的 `related` 解析成 slice_id 集合，結果放進 ``_related``。

    `moc/linker.py:63-72` 實際寫進 frontmatter 的是 `[[<title-slug>--<slice_id>]]`
    （entity 連結則是 `[[<ENTITY>]]`），不是裸 slice_id。所以解析要兩條路：先看
    整串是不是既存的 slice_id（人手寫的舊格式），再用 `--` 尾綴去對——認不出來的
    （entity 連結、指向已刪除 note 的死連結）就丟掉，related tier 只承認兩端都在
    這批 note 裡的互指。
    """
    ids = {note["slice_id"] for note in notes}
    for note in notes:
        resolved: set[str] = set()
        for raw in note["related"]:
            target = raw.strip()
            if target.startswith("[[") and target.endswith("]]"):
                target = target[2:-2].strip()
            # `[[target|display]]` 的顯示名不參與比對。
            target = target.split("|", 1)[0].strip()
            if target in ids:
                resolved.add(target)
            elif "--" in target and target.rsplit("--", 1)[-1] in ids:
                resolved.add(target.rsplit("--", 1)[-1])
        note["_related"] = resolved


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
        old["slice_id"] in new["_related"] and new["slice_id"] in old["_related"])


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
    if old["slice_id"] in new["_related"] and new["slice_id"] in old["_related"]:
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

    掃描順序是 ``(recency_key, slice_id)``，內圈只看排在自己前面的 note，所以
    「新 -> 舊」的方向對嚴格較新的配對來自時間、對時間相等的配對來自 slice_id
    字典序（大者當新），兩者都是決定性的。
    """
    root = Path(memory_root)
    families = tuple(tuple(str(m) for m in fam) for fam in families)
    notes = _load(root)
    dead = _dead(root)
    # 每則 note 只算一次同主題判定需要的衍生值；掃描本身是 O(n²) 配對，把
    # tokenize/parse/wikilink 解析留在內圈會讓真實記憶庫（數千則）的一次 dry-run
    # 慢到不可用。
    _resolve_related(notes)
    for note in notes:
        note["_rk"] = topic.recency_key(note["captured_at"])
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
            # decayed 兩側都擋：退場的 note 既不值得再標前身，也沒資格宣稱自己
            # 取代了還活著的 note。
            if old["slice_id"] in dead or new["slice_id"] in dead:
                continue
            if old["slice_id"] in new["supersedes"]:
                continue
            # 任一端沒有可用的時間就不配對；地板上的並列不是「同時」。
            if old["_rk"] == _TIME_FLOOR or new["_rk"] == _TIME_FLOOR:
                continue
            if old["_rk"] > new["_rk"]:  # 排序後不該發生，留著當不變式
                continue
            tied = old["_rk"] == new["_rk"]
            # 同一場 session 蒸出的兄弟 note 共用 captured_at；它們之間的關係在
            # publish 當下就決定過了，backfill 不該再插手。
            if tied and old["distilled_from"] == new["distilled_from"]:
                continue
            # checksum 相等即同一份內容，沒有「取代」可言。前綴 `old["checksum"] and`
            # 曾讓「兩邊都缺 checksum」直接短路失效（`"" and ...` 為假），把同一份
            # 內容的兩個副本當成不同版本配對。缺 checksum 是「無從判斷內容是否相同」
            # ——守門一律跳過，寧可漏配也不要憑空造出一組取代關係。
            if old["checksum"] == new["checksum"]:
                continue
            if old["_fkey"] != new["_fkey"]:
                continue
            if new["_names"] & old["_names"]:
                if tied:
                    # 同名同時戳不同 session：是兩個版本，但誰取代誰得由人決定。
                    fuzzy.append((new, old, _EQUAL_TIME_REASON))
                    continue
                same_title = topic.canonical_title(new["title"]) == topic.canonical_title(old["title"])
                exact.append((new, old, "exact-title" if same_title else "alias"))
                continue
            if not _can_be_fuzzy(new, old):
                continue
            reason = _fuzzy_reason(new, old, families)
            if reason:
                fuzzy.append((new, old, _EQUAL_TIME_REASON if tied else reason))

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


def apply_pairs(memory_root: Path | str, pairs: list[dict[str, Any]], *,
                now: str) -> tuple[int, list[dict[str, str]]]:
    """把配對寫進新者的 ``supersedes`` 並補一條 relations edge。

    回傳 ``(寫入筆數, 被跳過的配對)``；每筆跳過是
    ``{"new", "old", "reason"}``，reason ∈ ``unknown-new``／``self-link``／
    ``unknown-old``／``already-linked``／``would-cycle``。`--accept` 吃的是人手改過
    的報表：只回一個筆數的話，打錯 id、自連、會成環三種情況都只呈現為
    「applied: 0」，人無從判斷是自己改錯還是本來就 no-op。

    只走 `frontmatter_io.update()`（parse-equivalent、body 逐位元不變、atomic
    write）。已含該前身、自連、指向不存在的 slice、或會造成環的配對一律跳過，
    所以重跑同一批配對必定回 0（並把每筆記成 ``already-linked``）。
    """
    root = Path(memory_root)
    by_id = {n["slice_id"]: n for n in _load(root)}
    written = 0
    skipped: list[dict[str, str]] = []

    def _skip(new_id: str, old_id: str, reason: str) -> None:
        skipped.append({"new": new_id, "old": old_id, "reason": reason})

    for pair in pairs:
        new_id = str(pair.get("new") or "")
        old_id = str(pair.get("old") or "")
        new = by_id.get(new_id)
        if not new:
            _skip(new_id, old_id, "unknown-new")
            continue
        if new_id == old_id:
            _skip(new_id, old_id, "self-link")
            continue
        if not old_id or old_id not in by_id:
            _skip(new_id, old_id, "unknown-old")
            continue
        if old_id in new["supersedes"]:
            _skip(new_id, old_id, "already-linked")
            continue
        if _would_cycle(by_id, new_id, old_id):
            _skip(new_id, old_id, "would-cycle")
            continue
        # 原有順序原樣保留、新前身接在最後：supersedes 是「這則 note 取代過誰」
        # 的累積紀錄，重排會讓每次 backfill 都在既有 note 上製造無謂 diff。
        merged = new["supersedes"] + [old_id]
        fio.update(new["path"], {"supersedes": merged})
        new["supersedes"] = merged
        relations.append_edge(root, type="supersedes", frm=f"slice:{new_id}",
                              to=f"slice:{old_id}", now=now, config_hash=CONFIG_HASH)
        written += 1
    return written, skipped


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


def apply_accepted(memory_root: Path | str, report: Path | str, *,
                   now: str) -> tuple[int, list[dict[str, str]]]:
    """套用報表中 ``accept: true`` 的配對；回傳與 `apply_pairs` 同形的
    ``(寫入筆數, 被跳過的配對)``。"""
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
