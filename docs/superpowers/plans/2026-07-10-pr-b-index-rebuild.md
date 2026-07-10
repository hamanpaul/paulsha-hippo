# PR-B 索引可靠性（#16）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修復 #16 索引失效鏈——slugify 以 UTF-8 byte 上限截斷檔名（消滅 `ENAMETOOLONG` 中止整輪 MOC 的根因）、naming/linker 對單一壞 slice fail-soft、`build_index()` 改 temp DB + atomic replace、產出 coverage 六欄報表、以獨立三方對賬驗證強不變量 `indexed IDs == eligible IDs`。

**Architecture:** 全部變更收在 `paulsha_hippo/moc/` 一個 pass 鏈上：`naming.reconcile`（rename）→ `linker.materialize_links`（wikilink 物化）→ `search.build_index`（sqlite FTS 索引 + coverage 報表）；新增 `moc/census.py` 作為與上述掃描邏輯分離實作的獨立驗證面（filesystem census × coverage 報表 × index DB 反查），並以 `hippo index verify` 子命令暴露給恢復序列（spec §4.6）。

**Tech Stack:** Python 3.12 標準庫（`sqlite3`／`os`／`re`／`json`／`dataclasses`）、pytest（現行 `unittest.TestCase` + pytest function 混用風格）。

**對應 spec：** `docs/superpowers/specs/2026-07-10-all-issues-resolution-design.md` §3.2（另受 §6 workflow、§7 合規約束）。分支：`feature/16-index-rebuild`，PR body `Closes #16`。

## Global Constraints

（自 spec §7 與跨批次契約逐字轉抄；每個 Task 的要求隱含包含本節）

- **stdlib-only 零新依賴**：本批次所有新代碼只用 Python 標準庫；不得新增第三方套件。
- **zh-tw**：PR 標題／內文、commit message、issue comment 一律 zh-tw（conventional-commit 格式）。
- **tier: shareable（R-21）**：所有新增文件（含本 plan、測試、報表輸出）不得含個人絕對路徑、機敏標記。
- **changelog.d 碎片**：本 PR 必附 changelog.d 碎片（repo 現行慣例，格式見 `changelog.d/fix-dream-service-interpreter.md`）。
- **`python3 -m policy_check --repo .` 零 failure**（完成前必跑）。
- **conventional-commit**：`fix(moc): ...`／`feat(moc): ...`／`test(moc): ...`。
- **R-18/R-22**：behavior 變更同步 README／docs 引用（本批：`hippo index verify` 進 README 日常命令列）。
- **R-19**：新測試全部進 CI 覆蓋（`tests.yml` 已自動跑 pytest，測試放 `tests/` 即涵蓋）。
- **禁 commit main**；分支 `feature/16-index-rebuild`。
- **不 bump `VERSION`**（spec 非目標）。
- **驗收動態計算，不寫死歷史數字**（spec §1 runtime 快照已漂移）。

## 跨批次共享介面契約（本批相關部分；偏離即 bug）

- **契約 #6（本批產出）**：`build_index()` 回傳 dict，頂層必含六鍵 `{"scanned","invalid_frontmatter","pool_excluded","noise_excluded","eligible","indexed"}`；`pool_excluded`／`noise_excluded` 兩鍵值為 `{reason:str: count:int}`。本 plan 另加 `per_project`、`warnings` 兩個 repo 內部附加鍵（`runner.run_moc` 消費），不屬跨批次契約面。
- **契約 #3（本批消費，運行時假設）**：global dream lock `<memory_root>/runtime/locks/dream.lock` 由 PR-A 在 dream run 入口整輪持有——PR-B 的 index writer 由它序列化，**代碼零耦合**（本批不 import、不取鎖）。
- **契約 #9**：runtime 恢復序列／收口不在本 plan 內（workflow 主編排執行）；本 plan 只交付工具（`hippo index verify`、`census.reconcile_index`）與測試。

## 檔案地圖

| 動作 | 路徑 | 職責 |
|---|---|---|
| Modify | `paulsha_hippo/moc/naming.py` | slugify byte-bound、`slice_filename()`、reconcile fail-soft |
| Modify | `paulsha_hippo/retitle.py` | 檔名組裝改走 `slice_filename()` |
| Modify | `paulsha_hippo/moc/linker.py` | per-slice fail-soft、回傳 warnings |
| Modify | `paulsha_hippo/moc/search.py` | temp DB + atomic replace、coverage 六欄、coverage JSON 落盤 |
| Modify | `paulsha_hippo/moc/runner.py` | 消費新簽名、輸出 `index_coverage` |
| Create | `paulsha_hippo/moc/census.py` | 獨立 filesystem census + 三方對賬 |
| Modify | `paulsha_hippo/moc/cli.py` | `run_index_verify()` handler |
| Modify | `paulsha_hippo/cli.py` | `hippo index verify` 子命令 |
| Modify | `README.md` | 日常命令列補 `hippo index verify` |
| Test | `tests/test_moc_naming.py`、`tests/test_retitle.py`、`tests/test_moc_linker.py`、`tests/test_moc_search.py`、`tests/test_moc_runner.py`、`tests/test_search_scoped_corpus.py`、`tests/test_moc_census.py`（新） | |
| Create | `changelog.d/fix-16-index-rebuild.md` | changelog 碎片 |
| Modify | `CHANGELOG.md` | `[Unreleased]` 段（R-09 gate；碎片供 release 彙整） |

---

### Task 1: slugify UTF-8 byte-bound + `slice_filename()` + retitle 接線

**Files:**
- Modify: `paulsha_hippo/moc/naming.py:11-39`（`_SLUG_STRIP`／`slugify`／`target_name` 區段）
- Modify: `paulsha_hippo/retitle.py:88`
- Test: `tests/test_moc_naming.py`、`tests/test_retitle.py`

**Interfaces:**
- Consumes: 無（現有 `_title(fm, body)`、`_SLUG_STRIP` 保持不動）
- Produces:
  - `naming.NAME_MAX_BYTES: int = 255`
  - `naming.SLUG_MAX_BYTES_DEFAULT: int = 200`
  - `naming.slugify(title: str, max_bytes: int = SLUG_MAX_BYTES_DEFAULT) -> str`（slug ≤ max_bytes UTF-8 bytes，截斷落在 code-point 邊界）
  - `naming.slice_filename(title: str, slice_id: str) -> str`（`<slug>--<slice_id>.md` 總長 ≤ 255 bytes；`--<slice_id>.md` 尾段永不截斷）
  - `naming.target_name(fm: dict[str, Any], body: str) -> str`（簽名不變，內部改走 `slice_filename`）

- [ ] **Step 0: 確認分支**

```bash
cd $(git rev-parse --show-toplevel)
git rev-parse --abbrev-ref HEAD
```
若不在 `feature/16-index-rebuild`，自 latest main 開分支：

```bash
git fetch origin main && git checkout -b feature/16-index-rebuild origin/main
```

- [ ] **Step 1: 寫失敗測試（naming byte-bound）**

在 `tests/test_moc_naming.py` 的 `NamingTests` class 內（`test_slugify_punctuation_only_falls_back` 之後）加入：

```python
    def test_slugify_bounds_utf8_bytes_default(self):
        # #16 根因：超長 LLM title 未截斷，組出超過 NAME_MAX 的檔名
        slug = naming.slugify("測" * 100)  # 300 bytes
        self.assertLessEqual(len(slug.encode("utf-8")), naming.SLUG_MAX_BYTES_DEFAULT)
        self.assertTrue(slug)

    def test_slugify_truncates_at_codepoint_boundary(self):
        # "測" = 3 bytes：4/5 bytes 預算都只容得下一個完整字元，不得留半個
        self.assertEqual(naming.slugify("測測測", max_bytes=4), "測")
        self.assertEqual(naming.slugify("測測測", max_bytes=5), "測")
        self.assertEqual(naming.slugify("測測測", max_bytes=6), "測測")

    def test_slice_filename_never_exceeds_name_max(self):
        name = naming.slice_filename("記" * 120, "sl-0123456789abcdef")
        self.assertLessEqual(len(name.encode("utf-8")), naming.NAME_MAX_BYTES)
        self.assertTrue(name.endswith("--sl-0123456789abcdef.md"))

    def test_target_name_bounded_with_overlong_title(self):
        fm = {"slice_id": "sl-0123456789abcdef", "title": "超長標題" * 80}
        name = naming.target_name(fm, "body\n")
        self.assertLessEqual(len(name.encode("utf-8")), naming.NAME_MAX_BYTES)
        self.assertTrue(name.endswith("--sl-0123456789abcdef.md"))

    def test_reconcile_renames_overlong_title_without_enametoolong(self):
        # #16 根因情境：超長 title 也要 rename 成功、無 ENAMETOOLONG
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "sl-long.md", "sl-long", title="超長標題" * 80)
            warnings = naming.reconcile(root)
            self.assertEqual(warnings, [])
            renamed = [p for p in (root / "knowledge" / "paulshaclaw").iterdir()
                       if p.name.endswith("--sl-long.md")]
            self.assertEqual(len(renamed), 1)
            self.assertLessEqual(len(renamed[0].name.encode("utf-8")), naming.NAME_MAX_BYTES)
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_moc_naming.py -v
```
Expected: 新增 5 個測試 FAIL——`AttributeError: module 'paulsha_hippo.moc.naming' has no attribute 'SLUG_MAX_BYTES_DEFAULT'`（前兩個）、`has no attribute 'slice_filename'`、`AssertionError`／`OSError: [Errno 36] File name too long`（後兩個）。既有測試全 PASS。

- [ ] **Step 3: 實作 naming.py byte-bound 區段**

把 `paulsha_hippo/moc/naming.py` 的 line 11-20（`_SLUG_STRIP` 註解起、到 `slugify` 結束）替換為：

```python
# Keep Unicode word chars (letters incl. CJK, digits, underscore); fold every run
# of other chars (punctuation, whitespace, symbols) to a single hyphen. Preserving
# CJK is what stops pure-CJK titles collapsing to "untitled" (#151).
_SLUG_STRIP = re.compile(r"[^\w]+", re.UNICODE)

# Linux 檔名上限（ext4/tmpfs NAME_MAX）：以 UTF-8 bytes 計，不是字元數（#16）。
NAME_MAX_BYTES = 255
# 直接呼叫 slugify() 的預設 byte 預算：留足 `--<slice_id>.md` 尾段與餘裕。
SLUG_MAX_BYTES_DEFAULT = 200


def _utf8_truncate(text: str, max_bytes: int) -> str:
    """把字串截到 <= max_bytes 個 UTF-8 bytes，且落在 code-point 邊界。

    UTF-8 自同步：截斷後以 errors="ignore" decode 只會丟掉尾端不完整的
    byte 序列，絕不產生半個字元。max_bytes <= 0 回空字串。
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def slugify(title: str, max_bytes: int = SLUG_MAX_BYTES_DEFAULT) -> str:
    """Convert title to a slug, preserving CJK/Unicode letters; kebab-case ASCII.

    slug 以 UTF-8 bytes 上限截斷（#16：超長 LLM title 曾組出超過 NAME_MAX
    的檔名，令 MOC reconcile rename 觸發 ENAMETOOLONG 中止整輪）。
    """
    slug = _SLUG_STRIP.sub("-", title.strip().lower()).strip("-_")
    slug = _utf8_truncate(slug, max_bytes).rstrip("-_")
    if not slug:
        slug = _utf8_truncate("untitled", max_bytes)
    return slug


def slice_filename(title: str, slice_id: str) -> str:
    """組 `<slug>--<slice_id>.md`，保證總長 <= NAME_MAX_BYTES（UTF-8 bytes）。

    `--<slice_id>.md` 尾段永不截斷（截 id 等於毀 id）；byte 預算全由 slug
    吸收。病態超長 slice_id 令尾段自身超限時這裡不丟例外——組出的名字交由
    呼叫端 rename 時 fail-soft（reconcile 記 warning 跳過，見 Task 2）。
    """
    suffix = f"--{slice_id}.md"
    budget = NAME_MAX_BYTES - len(suffix.encode("utf-8"))
    return f"{slugify(title, max_bytes=budget)}{suffix}"
```

再把 `target_name`（原 line 37-39）替換為：

```python
def target_name(fm: dict[str, Any], body: str) -> str:
    """Generate target filename: <slug>--<slice_id>.md（UTF-8 總長 <= NAME_MAX_BYTES）"""
    return slice_filename(_title(fm, body), str(fm["slice_id"]))
```

- [ ] **Step 4: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_moc_naming.py -v
```
Expected: 全部 PASS（含既有 `test_slugify`／`test_slugify_preserves_cjk`／`test_slugify_ascii_unchanged`／`test_slugify_punctuation_only_falls_back`——短 title 行為零變）。

- [ ] **Step 5: 寫失敗測試（retitle 檔名 byte-bound）**

在 `tests/test_retitle.py` 的 `RetitleUntitledTests` class 內加入：

```python
    def test_apply_bounds_overlong_distilled_title_filename(self):
        # 蒸餾出超長 title 時，rename 後檔名必須 byte-bound（走 naming.slice_filename）
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "p", "untitled--sl-long1.md",
                   "## 問題\n某真實技術問題的描述與其根因分析。")
            summary = retitle.retitle_untitled(
                root, now="2026-06-25T00:00:00Z", apply=True,
                distill=lambda b: "長" * 300)
            self.assertEqual(summary["retitled"], 1)
            renamed = [p for p in (root / "knowledge" / "p").iterdir()
                       if p.name.endswith("--sl-long1.md")]
            self.assertEqual(len(renamed), 1)
            self.assertLessEqual(len(renamed[0].name.encode("utf-8")), 255)
```

- [ ] **Step 6: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_retitle.py::RetitleUntitledTests::test_apply_bounds_overlong_distilled_title_filename -v
```
Expected: FAIL——`OSError: [Errno 36] File name too long`（retitle 自組 900-byte 檔名 rename 失敗）。

- [ ] **Step 7: retitle 改走 slice_filename**

`paulsha_hippo/retitle.py:88`，把：

```python
        new_name = f"{_naming.slugify(title)}--{slice_id}.md"
```

改為：

```python
        new_name = _naming.slice_filename(title, slice_id)
```

- [ ] **Step 8: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_retitle.py tests/test_moc_naming.py -v
```
Expected: 全部 PASS。

- [ ] **Step 9: Commit**

```bash
git add paulsha_hippo/moc/naming.py paulsha_hippo/retitle.py tests/test_moc_naming.py tests/test_retitle.py
git commit -m "fix(moc): slugify/檔名以 UTF-8 byte 上限截斷——超長 title 不再 ENAMETOOLONG（#16 根因）"
```

---

### Task 2: `naming.reconcile` 對單一壞 slice fail-soft

**Files:**
- Modify: `paulsha_hippo/moc/naming.py:80-122`（`reconcile` 全函式）
- Test: `tests/test_moc_naming.py`

**Interfaces:**
- Consumes: Task 1 的 `target_name()`（bounded）
- Produces: `naming.reconcile(memory_root: Path, now: str | None = None) -> list[str]`——**簽名不變**；語意升級：單一 slice 任何例外（讀檔失敗、rename `ENAMETOOLONG`、stat 競態）只 append warning `"<name>: reconcile skipped (<exc>)"` 後續處理其餘 slices，不中止整輪。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_moc_naming.py` 的 `NamingTests` class 內加入：

```python
    def test_reconcile_fail_soft_on_unrenamable_slice(self):
        # 尾段（--<slice_id>.md）永不截斷：病態超長 slice_id 的目標檔名必然
        # 超過 NAME_MAX，rename 觸發 ENAMETOOLONG——單一壞 slice 只記 warning
        # 跳過，整輪照常處理其餘 slices（#16）。
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_sid = "sl-" + "a" * 300
            _write(root, "bad.md", bad_sid, title="bad")
            _write(root, "good.md", "sl-ok", title="Good Note")
            warnings = naming.reconcile(root)
            kdir = root / "knowledge" / "paulshaclaw"
            self.assertTrue((kdir / "good-note--sl-ok.md").exists())
            self.assertTrue((kdir / "bad.md").exists())  # 原檔保留，未半毀
            self.assertTrue(any("bad.md" in w and "reconcile skipped" in w for w in warnings))

    def test_reconcile_fail_soft_on_undecodable_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kdir = root / "knowledge" / "paulshaclaw"
            kdir.mkdir(parents=True)
            (kdir / "broken.md").write_bytes(b"---\nslice_id: sl-x\n\xff\xfe---\nbody\n")
            _write(root, "ok.md", "sl-ok2", title="Still Works")
            warnings = naming.reconcile(root)
            self.assertTrue((kdir / "still-works--sl-ok2.md").exists())
            self.assertTrue(any("broken.md" in w for w in warnings))
```

（`bad.md` 按字典序先於 `good.md` 被處理——證明失敗後迴圈確實續行。）

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_moc_naming.py -k "fail_soft" -v
```
Expected: 2 FAIL——分別為 `OSError: [Errno 36] File name too long` 與 `UnicodeDecodeError` 直接炸出 `reconcile`。

- [ ] **Step 3: 實作 fail-soft reconcile**

把 `paulsha_hippo/moc/naming.py` 的 `reconcile`（原 line 80-122）整段替換為：

```python
def reconcile(memory_root: Path, now: str | None = None) -> list[str]:
    """Rename slices to <title>--<slice_id>.md and dedup by slice_id. Returns warnings.

    Fail-soft（#16）：單一壞 slice（讀檔失敗、rename 失敗如 ENAMETOOLONG、
    stat 競態）只記 warning 跳過，不中止整輪 MOC pass。

    ``now`` is the moc pass's injected logical timestamp; it is stamped on the
    dedup lifecycle traces so they stay deterministic (no wall-clock).
    """
    knowledge = memory_root / "knowledge"
    warnings: list[str] = []
    if not knowledge.exists():
        return warnings
    seen: dict[str, Path] = {}
    for path in sorted(knowledge.rglob("*.md")):
        try:
            _reconcile_one(memory_root, path, seen, warnings, now)
        except Exception as exc:  # fail-soft: 跳過毒 slice，整輪續行
            warnings.append(f"{path.name}: reconcile skipped ({exc})")
    return warnings


def _reconcile_one(
    memory_root: Path,
    path: Path,
    seen: dict[str, Path],
    warnings: list[str],
    now: str | None,
) -> None:
    """單一檔案的 rename + dedup；任何例外交由 reconcile() fail-soft 收掉。"""
    fm, body = frontmatter_io.read(path.read_text(encoding="utf-8"))
    if fm.get("memory_layer") != "knowledge":
        return
    slice_id = fm.get("slice_id")
    if not slice_id:
        warnings.append(f"{path}: missing slice_id; skipped")
        return
    target = path.with_name(target_name(fm, body))
    if path != target:
        if target.exists():
            # Only overwrite if current file is newer
            if path.stat().st_mtime <= target.stat().st_mtime:
                _append_superseded_event(memory_root, slice_id, path, target, ts=now)
                path.unlink()
                return
            _append_superseded_event(memory_root, slice_id, target, path, ts=now)
            target.unlink()
        path.rename(target)
        path = target
    if slice_id in seen:
        other = seen[slice_id]
        if path.resolve() != other.resolve():
            older = other if other.stat().st_mtime <= path.stat().st_mtime else path
            newer = path if older is other else other
            _append_superseded_event(memory_root, slice_id, older, newer, ts=now)
            older.unlink()
            seen[slice_id] = newer
            warnings.append(f"duplicate slice_id {slice_id}; kept {newer.name}")
    else:
        seen[slice_id] = path
```

（除「loop body 抽成 `_reconcile_one` + `continue`→`return`」外，rename/dedup 邏輯逐行保持原樣。）

- [ ] **Step 4: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_moc_naming.py -v
```
Expected: 全部 PASS（含既有 dedup／collision／lifecycle 測試——行為零變）。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/moc/naming.py tests/test_moc_naming.py
git commit -m "fix(moc): naming reconcile 對單一壞 slice fail-soft，不中止整輪（#16）"
```

---

### Task 3: linker 對單一壞 slice fail-soft + runner 接線

**Files:**
- Modify: `paulsha_hippo/moc/linker.py:9-58`（`_slice_files`、`materialize_links` 全函式）
- Modify: `paulsha_hippo/moc/runner.py:12-16`（linker 呼叫段）
- Test: `tests/test_moc_linker.py`

**Interfaces:**
- Consumes: 無
- Produces: `linker.materialize_links(memory_root: Path) -> tuple[dict[str, int], list[str]]`——**回傳型別變更**（原 `dict[str, int]`）：`(link weights, warnings)`。單一 slice 讀寫失敗記 warning 跳過（該 slice 不進 weights）；relations ledger 本身壞掉仍向外拋（runner 以「linker degraded」整體降級，行為不變）。

- [ ] **Step 1: 更新既有測試 + 寫失敗測試**

`tests/test_moc_linker.py` 整檔替換為：

```python
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from paulsha_hippo.lib.lifecycle.schema import compute_checksum, validate_frontmatter
from paulsha_hippo.ledger import relations
from paulsha_hippo.moc import frontmatter_io as fio
from paulsha_hippo.moc import linker


def _slice(root: Path, slice_id: str, title: str) -> Path:
    body = f"body {slice_id}\n"
    path = root / "knowledge" / "p" / f"{title}--{slice_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = (f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: p\n"
          f"artifact_kind: research\ntitle: {title}\nchecksum: {compute_checksum(body)}\n"
          f"phase: research\nversion: 1\ncreated_at: 2026-06-03T00:00:00Z\ncreated_by: c\n"
          f"source_session: s\ngate_required: false\ncaptured_at: 2026-06-03T00:00:00Z\n"
          f"source_agent: c\nsupersedes: []\ndistilled_from: c:s\n---\n{body}")
    path.write_text(fm, encoding="utf-8")
    return path


class LinkerTests(unittest.TestCase):
    def test_bidirectional_related_and_entity_links_in_frontmatter_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _slice(root, "sl-a", "alpha")
            b = _slice(root, "sl-b", "beta")
            relations.append_edge(root, type="relates_to", frm="slice:sl-a", to="slice:sl-b", now="t", config_hash="h")
            relations.append_edge(root, type="mentions", frm="slice:sl-a", to="entity:MTK", now="t", config_hash="h")
            weights, warnings = linker.materialize_links(root)
            self.assertEqual(warnings, [])
            fm_a, body_a = fio.read(a.read_text(encoding="utf-8"))
            fm_b, _ = fio.read(b.read_text(encoding="utf-8"))
            self.assertIn("[[beta--sl-b]]", fm_a["related"])
            self.assertIn("[[MTK]]", fm_a["related"])
            self.assertIn("[[alpha--sl-a]]", fm_b["related"])  # bidirectional
            self.assertNotIn("[[", body_a)                      # never in body
            self.assertTrue(validate_frontmatter(frontmatter=fm_a, body=body_a).ok)  # checksum intact
            self.assertEqual(fm_a.get("aliases"), ["alpha"])
            self.assertEqual(weights["sl-a"], 2)

    def test_single_bad_slice_fails_soft(self):
        # #16：單一 slice 寫入失敗只記 warning 跳過，其餘照常物化
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "alpha")
            b = _slice(root, "sl-b", "beta")
            relations.append_edge(root, type="relates_to", frm="slice:sl-a", to="slice:sl-b", now="t", config_hash="h")
            real_update = fio.update

            def flaky_update(path, updates):
                if path.name.startswith("alpha--"):
                    raise OSError(28, "No space left on device")
                real_update(path, updates)

            with mock.patch("paulsha_hippo.moc.linker.fio.update", side_effect=flaky_update):
                weights, warnings = linker.materialize_links(root)
            self.assertNotIn("sl-a", weights)
            self.assertIn("sl-b", weights)
            self.assertTrue(any("alpha--sl-a.md" in w for w in warnings))
            fm_b, _ = fio.read(b.read_text(encoding="utf-8"))
            self.assertIn("[[alpha--sl-a]]", fm_b["related"])  # 好 slice 照常完成

    def test_undecodable_file_skipped_with_warning(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-a", "alpha")
            kdir = root / "knowledge" / "p"
            (kdir / "broken.md").write_bytes(b"---\nslice_id: sl-bad\n\xff\xfe---\nbody\n")
            weights, warnings = linker.materialize_links(root)
            self.assertIn("sl-a", weights)
            self.assertNotIn("sl-bad", weights)
            self.assertTrue(any("broken.md" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_moc_linker.py -v
```
Expected: 3 FAIL——既有測試 tuple unpack 拿到 dict keys（`assertEqual(warnings, [])` 失敗）；fail-soft 測試 `OSError`；undecodable 測試 `UnicodeDecodeError`。

- [ ] **Step 3: 實作 linker fail-soft**

`paulsha_hippo/moc/linker.py` 整檔替換為：

```python
from __future__ import annotations

from pathlib import Path

from ..ledger import relations
from . import frontmatter_io as fio


def _slice_files(memory_root: Path) -> tuple[dict[str, Path], list[str]]:
    """slice_id -> path, for memory_layer: knowledge files；附 per-file warnings。

    Fail-soft（#16）：讀不動的檔（權限、壞編碼）記 warning 跳過，
    不讓單一壞檔中止整輪 linker。
    """
    mapping: dict[str, Path] = {}
    warnings: list[str] = []
    knowledge = memory_root / "knowledge"
    if not knowledge.exists():
        return mapping, warnings
    for path in sorted(knowledge.rglob("*.md")):
        try:
            fm, _ = fio.read(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"{path.name}: linker skipped ({exc})")
            continue
        if fm.get("memory_layer") != "knowledge":
            continue
        sid = fm.get("slice_id")
        if sid:
            mapping[str(sid)] = path
    return mapping, warnings


def materialize_links(memory_root: Path) -> tuple[dict[str, int], list[str]]:
    """物化 [[wikilink]] 到 frontmatter，回傳 (link weights, warnings)。

    Fail-soft（#16）：單一 slice 讀寫失敗只記 warning 跳過（該 slice 不進
    weights），其餘 slices 照常物化；relations ledger 本身壞掉仍向外拋
    （core-state corruption 由 runner 整體降級）。
    """
    files, warnings = _slice_files(memory_root)
    # build bidirectional adjacency
    related: dict[str, set[str]] = {sid: set() for sid in files}
    for edge in relations.read_edges(memory_root):
        etype = edge.get("type")
        frm = str(edge.get("from", ""))
        to = str(edge.get("to", ""))
        if etype == "relates_to" and frm.startswith("slice:") and to.startswith("slice:"):
            a, b = frm[len("slice:"):], to[len("slice:"):]
            if a in related:
                related[a].add(f"slice:{b}")
            if b in related:
                related[b].add(f"slice:{a}")
        elif etype == "mentions" and frm.startswith("slice:") and to.startswith("entity:"):
            a = frm[len("slice:"):]
            if a in related:
                related[a].add(to)  # entity:<NAME>

    weights: dict[str, int] = {}
    for sid, path in files.items():
        try:
            fm, body = fio.read(path.read_text(encoding="utf-8"))
            links: list[str] = []
            for node in sorted(related.get(sid, set())):
                if node.startswith("slice:"):
                    target = files.get(node[len("slice:"):])
                    if target is not None:
                        links.append(f"[[{target.stem}]]")
                elif node.startswith("entity:"):
                    links.append(f"[[{node[len('entity:'):]}]]")
            title = fm.get("title") or path.stem.rsplit("--", 1)[0]
            fio.update(path, {"title": title, "aliases": [title], "related": links})
        except Exception as exc:  # fail-soft: 單一壞 slice 不中止 linker
            warnings.append(f"{path.name}: linker skipped ({exc})")
            continue
        weights[sid] = len(links)
    return weights, warnings
```

- [ ] **Step 4: 更新 runner 接線**

`paulsha_hippo/moc/runner.py` 的 line 12-16，把：

```python
    try:
        weights = linker.materialize_links(memory_root)
    except Exception as exc:  # core-state corruption (relations) -> degrade
        warnings.append(f"linker degraded: {exc}")
        weights = {}
```

改為：

```python
    try:
        weights, linker_warnings = linker.materialize_links(memory_root)
        warnings.extend(linker_warnings)
    except Exception as exc:  # core-state corruption (relations) -> degrade
        warnings.append(f"linker degraded: {exc}")
        weights = {}
```

- [ ] **Step 5: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_moc_linker.py tests/test_moc_runner.py tests/test_dream_cli_moc_warnings.py -v
```
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add paulsha_hippo/moc/linker.py paulsha_hippo/moc/runner.py tests/test_moc_linker.py
git commit -m "fix(moc): linker 對單一壞 slice fail-soft 並回傳 warnings（#16）"
```

---

### Task 4: `build_index()` temp DB + atomic replace（廢除先 unlink）

**Files:**
- Modify: `paulsha_hippo/moc/search.py:52-147`（`build_index` 全函式）與 imports
- Test: `tests/test_moc_search.py`

**Interfaces:**
- Consumes: 無
- Produces: `search.build_index(...)` 簽名與回傳（本 task 仍為 `BuildIndexStats`）不變；**行為變更**：寫入 `runtime/indexes/retrieval.db.tmp`，成功 commit 後 `os.replace` 到 `retrieval.db`；任何失敗時舊 DB 完整保留、tmp 清掉。移除原 line 56-57 的「先 unlink 現有 DB」。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_moc_search.py` 的 `SearchTests` class 內加入：

```python
    def test_build_index_failure_preserves_existing_db(self):
        # #16：建索引中途失敗，舊 DB 必須完整保留（廢除先 unlink）
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            search.build_index(root, link_weights={})
            before = search.search(root, "alpha", project=None, limit=5, include_decayed=True)
            self.assertEqual([h["slice_id"] for h in before], ["sl-1"])

            _slice(root, "sl-2", "proj", "beta", "beta body")
            with mock.patch(
                "paulsha_hippo.moc.search.retrieval_set.active_records",
                side_effect=RuntimeError("boom mid-build"),
            ), self.assertRaises(RuntimeError):
                search.build_index(root, link_weights={})

            after = search.search(root, "alpha", project=None, limit=5, include_decayed=True)
            self.assertEqual([h["slice_id"] for h in after], ["sl-1"])  # 舊 DB 未損毀
            self.assertFalse((search.index_path(root).parent / "retrieval.db.tmp").exists())

    def test_build_index_success_replaces_db_atomically(self):
        # 守護測試（舊實作下也綠）：成功重建後新內容可查、tmp 不殘留
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            search.build_index(root, link_weights={})
            _slice(root, "sl-2", "proj", "beta", "beta body")
            search.build_index(root, link_weights={})
            hits = search.search(root, "beta", project=None, limit=5, include_decayed=True)
            self.assertEqual([h["slice_id"] for h in hits], ["sl-2"])
            self.assertFalse((search.index_path(root).parent / "retrieval.db.tmp").exists())
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_moc_search.py -k "preserves_existing_db or replaces_db_atomically" -v
```
Expected: `test_build_index_failure_preserves_existing_db` FAIL（舊實作先 unlink，失敗後留下殘缺新 DB，`after` 查無 `sl-1`）；`test_build_index_success_replaces_db_atomically` PASS（守護用，紅燈以第一個為準）。

- [ ] **Step 3: 實作 temp DB + atomic replace**

`paulsha_hippo/moc/search.py`：

(a) imports 區（line 3-7）加入 `os`：

```python
import logging
import os
import sqlite3
```

(b) `build_index` 開頭（原 line 54-57）：

```python
    path = index_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
```

改為：

```python
    path = index_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    if tmp_path.exists():  # 上一次 crash 殘留的半成品
        tmp_path.unlink()
```

(c) 連線行（原 line 63-65；**注意** `conn = sqlite3.connect(path)` 在檔內出現兩次——`search()` 那次不得動，以下列含前文的錨點鎖定 `build_index` 這次），把：

```python
    stats = BuildIndexStats()

    conn = sqlite3.connect(path)
```

改為：

```python
    stats = BuildIndexStats()

    conn = sqlite3.connect(tmp_path)
```

(d) 函式收尾（原 line 144-147），把：

```python
        conn.commit()
        return stats
    finally:
        conn.close()
```

改為：

```python
        conn.commit()
    except BaseException:
        conn.close()
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    conn.close()
    os.replace(tmp_path, path)  # atomic：讀者永遠只看到完整 DB
    return stats
```

（即：`finally: conn.close()` 改為顯式雙路徑——失敗路徑 close + 清 tmp + re-raise；成功路徑 close 後 `os.replace` 再 return。try 區內部邏輯——CREATE TABLE、flush_batch、project_corpus、掃描迴圈、exclude-rate warnings——本 task 一行不動。）

- [ ] **Step 4: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_moc_search.py tests/test_search_scoped_corpus.py tests/test_moc_e2e.py -v
```
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/moc/search.py tests/test_moc_search.py
git commit -m "fix(moc): build_index 改 temp DB + atomic replace，失敗保留舊索引（#16）"
```

---

### Task 5: coverage 六欄報表——`build_index()` 回傳 dict + 原子落盤 + runner `index_coverage`

**Files:**
- Modify: `paulsha_hippo/moc/search.py`（全檔重排：移除 `BuildIndexStats`、`build_index` 回傳 dict、新增 `coverage_path`/`COVERAGE_KEYS`/`_write_coverage`）
- Modify: `paulsha_hippo/moc/runner.py`（全檔，消費 dict 報表）
- Modify: `tests/test_search_scoped_corpus.py:61-65,80-89`（`stats.per_project[...]` → dict 取值）
- Test: `tests/test_moc_search.py`、`tests/test_moc_runner.py`

**Interfaces:**
- Consumes: Task 4 的 temp-DB 寫入流程
- Produces:（**跨批次契約 #6**）
  - `search.build_index(memory_root: Path, link_weights: dict[str, int], doc_corpus: object | None = None) -> dict[str, object]`——頂層六鍵 `scanned:int / invalid_frontmatter:int / pool_excluded:dict[str,int] / noise_excluded:dict[str,int] / eligible:int / indexed:int`，附加鍵 `per_project: dict[str, dict[str, float | int]]`（`{"indexed","excluded","exclude_rate"}`）與 `warnings: list[str]`
  - `search.COVERAGE_KEYS: tuple[str, ...] = ("scanned", "invalid_frontmatter", "pool_excluded", "noise_excluded", "eligible", "indexed")`
  - `search.coverage_path(memory_root: Path) -> Path` = `<memory_root>/runtime/indexes/retrieval.coverage.json`（build 成功後原子落盤，內容恰為六鍵）
  - `runner.run_moc(memory_root: Path, now: str) -> dict`——新增 `"index_coverage"` key（六鍵 dict；index 失敗時 `{}`）
- **掃描檔案唯一去向（分割規則，census 必須逐字對齊）**：
  1. `read_text` 失敗（`OSError`/`UnicodeDecodeError`）→ `invalid_frontmatter`（附 warning）
  2. `fio.read` 得空 frontmatter（無 frontmatter／壞 YAML／非 dict）→ `invalid_frontmatter`
  3. `memory_layer != "knowledge"` → `pool_excluded["non-knowledge-layer:<layer 或 none>"]`（moc 檔、迷路 inbox 檔的去向）
  4. `memory_layer == "knowledge"` 但缺 `slice_id` → `invalid_frontmatter`
  5. `pool_exclude_reason(fm)` 非 None → `pool_excluded[<reason>]`
  6. `classify_noise(...).is_noise` → `noise_excluded[<verdict.reason>]`
  7. 其餘 → `eligible`（= 實際插入 index 的列）
  - `indexed` 於 commit 後以 `SELECT COUNT(*) FROM slice_meta` 從 DB 讀回（不回抄迴圈計數）。
  - 恆等式：`scanned == invalid_frontmatter + Σpool_excluded + Σnoise_excluded + eligible`。

- [ ] **Step 1: 寫失敗測試（search 三個 + runner 一個）**

`tests/test_moc_search.py`：檔頭 import 區加 `import json`（放在 `import sqlite3` 之前）。`SearchTests` class 內加入：

```python
    def test_build_index_returns_six_column_coverage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            k = root / "knowledge" / "proj"
            k.mkdir(parents=True)
            _slice(root, "sl-good", "proj", "good-note", "真實 知識 內容")
            (k / "rev.md").write_text(
                "---\nmemory_layer: knowledge\nslice_id: sl-rev\nproject: proj\n"
                "title: PR Review\nartifact_kind: review\n---\nreview body\n",
                encoding="utf-8")
            (k / "echo.md").write_text(
                "---\nmemory_layer: knowledge\nslice_id: sl-echo\nproject: proj\n"
                "title: X\nartifact_kind: report\n---\n## CWD\n/tmp\n",
                encoding="utf-8")
            (k / "badyaml.md").write_text("---\ntitle: [unclosed\n---\nbody\n", encoding="utf-8")
            (k / "nosid.md").write_text(
                "---\nmemory_layer: knowledge\nproject: proj\ntitle: t\n---\nbody\n",
                encoding="utf-8")
            (root / "knowledge" / "wiki-moc.md").write_text(
                "---\nmemory_layer: moc\nmoc_kind: wiki\n---\n# Wiki\n", encoding="utf-8")

            report = search.build_index(root, link_weights={})

            self.assertEqual(report["scanned"], 6)
            self.assertEqual(report["invalid_frontmatter"], 2)  # badyaml + nosid
            self.assertEqual(report["pool_excluded"],
                             {"non-knowledge-layer:moc": 1, "review-record": 1})
            self.assertEqual(report["noise_excluded"], {"structural-echo:CWD": 1})
            self.assertEqual(report["eligible"], 1)
            self.assertEqual(report["indexed"], 1)

    def test_build_index_persists_coverage_json_atomically(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            report = search.build_index(root, link_weights={})
            cov_path = search.coverage_path(root)
            self.assertTrue(cov_path.exists())
            cov = json.loads(cov_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(cov),
                {"scanned", "invalid_frontmatter", "pool_excluded",
                 "noise_excluded", "eligible", "indexed"})
            self.assertEqual(cov["eligible"], cov["indexed"])
            self.assertEqual(cov["indexed"], report["indexed"])

    def test_build_index_report_keeps_per_project_and_warnings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "proj", "alpha", "alpha body")
            report = search.build_index(root, link_weights={})
            self.assertEqual(report["per_project"]["proj"],
                             {"indexed": 1, "excluded": 0, "exclude_rate": 0.0})
            self.assertEqual(report["warnings"], [])
```

`tests/test_moc_runner.py` 的 `RunnerTests` class 內加入：

```python
    def test_run_moc_reports_index_coverage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _slice(root, "sl-1", "Alpha")
            result = runner.run_moc(root, now="2026-06-03T00:00:00Z")
            cov = result["index_coverage"]
            self.assertEqual(cov["eligible"], 1)
            self.assertEqual(cov["indexed"], 1)
            self.assertGreaterEqual(cov["scanned"], 2)  # slice + build_mocs 產生的 moc 檔
            self.assertGreaterEqual(sum(cov["pool_excluded"].values()), 1)
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_moc_search.py -k coverage -v
python3 -m pytest tests/test_moc_runner.py::RunnerTests::test_run_moc_reports_index_coverage -v
```
Expected: search 三個 FAIL（`TypeError: 'BuildIndexStats' object is not subscriptable`／`AttributeError: ... has no attribute 'coverage_path'`）；runner 測試 FAIL（`KeyError: 'index_coverage'`）。

- [ ] **Step 3: 重寫 search.py（完整最終版）**

`paulsha_hippo/moc/search.py` 整檔替換為：

```python
# paulshaclaw/memory/moc/search.py
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import instruction_corpus
from ..importer.config import default_projects_path, load_projects_config
from ..ledger import lifecycle
from ..ledger import retrieval_set
from ..noise import classify_noise, pool_exclude_reason
from . import frontmatter_io as fio

INDEX_WRITE_BATCH_SIZE = 100
LOGGER = logging.getLogger("paulsha_hippo.moc.search")

# 跨批次契約 #6：build_index() 回傳 dict 的六個 coverage 鍵。
COVERAGE_KEYS = ("scanned", "invalid_frontmatter", "pool_excluded",
                 "noise_excluded", "eligible", "indexed")


@dataclass
class ProjectIndexStats:
    indexed: int = 0
    excluded: int = 0

    @property
    def exclude_rate(self) -> float:
        total = self.indexed + self.excluded
        if total == 0:
            return 0.0
        return self.excluded / total


class SearchIndexError(Exception):
    """Raised when the search index is missing or unusable."""


def index_path(memory_root: Path) -> Path:
    return memory_root / "runtime" / "indexes" / "retrieval.db"


def coverage_path(memory_root: Path) -> Path:
    """build_index() 成功後原子落盤的六欄 coverage 報表（三方對賬的比對基準）。"""
    return memory_root / "runtime" / "indexes" / "retrieval.coverage.json"


def _project_roots(memory_root: Path) -> dict[str, tuple[str, ...]]:
    config = load_projects_config(default_projects_path(memory_root))
    return {project.slug: project.roots for project in config.projects}


def _write_coverage(memory_root: Path, coverage: dict[str, object]) -> None:
    cov_path = coverage_path(memory_root)
    tmp = cov_path.with_name(cov_path.name + ".tmp")
    tmp.write_text(
        json.dumps(coverage, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, cov_path)


def build_index(memory_root: Path, link_weights: dict[str, int],
                doc_corpus: "object | None" = None) -> dict[str, object]:
    """建 retrieval index（temp DB + atomic replace）並回傳 coverage 報表。

    回傳 dict（跨批次契約 #6）：六欄 ``scanned / invalid_frontmatter /
    pool_excluded / noise_excluded / eligible / indexed``（excluded 兩欄為
    {reason: count}），外加 repo 內部鍵 ``per_project``（exclude-rate 觀測）
    與 ``warnings``。同一份六欄 coverage 會原子落盤到 ``coverage_path()``，
    供 `hippo index verify` 三方對賬（census.py）。

    掃描檔案唯一去向（census._fate 必須逐字對齊）：讀檔失敗/壞 frontmatter
    → invalid_frontmatter；memory_layer != knowledge →
    pool_excluded[non-knowledge-layer:<layer>]；缺 slice_id →
    invalid_frontmatter；pool_exclude_reason → pool_excluded[<reason>]；
    classify_noise → noise_excluded[<reason>]；其餘 eligible（=實際入索引）。
    """
    path = index_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    if tmp_path.exists():  # 上一次 crash 殘留的半成品
        tmp_path.unlink()
    # 先讀 projects.yaml/建語料，全部成功後才開 sqlite 連線——否則 scoping-config
    # 壞掉會洩漏連線（reviewer Important）。
    project_roots = _project_roots(memory_root)
    corpus_by_project: dict[str, object] = {}
    empty_corpus = instruction_corpus.corpus_for_roots(())
    per_project: dict[str, ProjectIndexStats] = {}
    warnings: list[str] = []
    pool_excluded: dict[str, int] = {}
    noise_excluded: dict[str, int] = {}
    coverage: dict[str, object] = {
        "scanned": 0, "invalid_frontmatter": 0, "pool_excluded": pool_excluded,
        "noise_excluded": noise_excluded, "eligible": 0, "indexed": 0,
    }

    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("CREATE VIRTUAL TABLE slices_fts USING fts5("
                     "slice_id UNINDEXED, project, title, tags, body, tokenize='unicode61')")
        conn.execute("CREATE TABLE slice_meta (slice_id TEXT PRIMARY KEY, project TEXT, "
                     "captured_at TEXT, active INTEGER, link_weight INTEGER, path TEXT)")
        knowledge = memory_root / "knowledge"
        events = lifecycle.read_events(memory_root)

        def flush_batch(rows: list[tuple[str, str, str, str, str, str, str]]) -> None:
            if not rows:
                return
            active = set(
                retrieval_set.active_records(
                    memory_root,
                    [row[0] for row in rows],
                    events=events,
                )
            )
            conn.executemany(
                "INSERT INTO slices_fts VALUES (?,?,?,?,?)",
                [(sid, project, title, tags, body)
                 for sid, project, title, tags, body, _captured_at, _path in rows],
            )
            conn.executemany(
                "INSERT INTO slice_meta VALUES (?,?,?,?,?,?)",
                [
                    (sid, project, captured_at, 1 if sid in active else 0,
                     link_weights.get(sid, 0), fpath)
                    for sid, project, _title, _tags, _body, captured_at, fpath in rows
                ],
            )

        def project_corpus(project: str) -> object:
            cached = corpus_by_project.get(project)
            if cached is not None:
                return cached
            if project in project_roots:
                corpus = instruction_corpus.corpus_for_roots(project_roots[project])
            elif doc_corpus is not None and not project_roots:
                corpus = doc_corpus
            else:
                corpus = empty_corpus
            corpus_by_project[project] = corpus
            return corpus

        rows: list[tuple[str, str, str, str, str, str, str]] = []
        if knowledge.exists():
            for fpath in sorted(knowledge.rglob("*.md")):
                coverage["scanned"] += 1
                try:
                    fm, body = fio.read(fpath.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as exc:
                    coverage["invalid_frontmatter"] += 1
                    warnings.append(f"index: unreadable {fpath.name} ({exc})")
                    continue
                if not fm:
                    coverage["invalid_frontmatter"] += 1
                    continue
                layer = fm.get("memory_layer")
                if layer != "knowledge":
                    reason = f"non-knowledge-layer:{layer or 'none'}"
                    pool_excluded[reason] = pool_excluded.get(reason, 0) + 1
                    continue
                sid = fm.get("slice_id")
                if not sid:
                    coverage["invalid_frontmatter"] += 1
                    continue
                reason = pool_exclude_reason(fm)
                if reason is not None:
                    pool_excluded[reason] = pool_excluded.get(reason, 0) + 1
                    continue
                project = str(fm.get("project", ""))
                project_stats = per_project.setdefault(project, ProjectIndexStats())
                verdict = classify_noise(fm, body, doc_corpus=project_corpus(project))
                if verdict.is_noise:
                    noise_excluded[verdict.reason] = noise_excluded.get(verdict.reason, 0) + 1
                    project_stats.excluded += 1
                    continue
                coverage["eligible"] += 1
                project_stats.indexed += 1
                rows.append((str(sid), project, str(fm.get("title", "")),
                             " ".join(fm.get("tags", []) if isinstance(fm.get("tags"), list) else []),
                             body, str(fm.get("captured_at", "")), str(fpath)))
                if len(rows) >= INDEX_WRITE_BATCH_SIZE:
                    flush_batch(rows)
                    rows.clear()
        flush_batch(rows)
        for project, project_stats in sorted(per_project.items()):
            if project_stats.exclude_rate <= 0.40:
                continue
            warning = (
                f"search index project {project}: indexed={project_stats.indexed} "
                f"excluded={project_stats.excluded} exclude_rate={project_stats.exclude_rate:.2f}"
            )
            LOGGER.warning(warning)
            warnings.append(warning)
        conn.commit()
        # indexed 從 DB 讀回而非回抄迴圈計數——報表對 DB 的最小自檢
        coverage["indexed"] = conn.execute(
            "SELECT COUNT(*) FROM slice_meta").fetchone()[0]
    except BaseException:
        conn.close()
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    conn.close()
    os.replace(tmp_path, path)  # atomic：讀者永遠只看到完整 DB
    _write_coverage(memory_root, coverage)
    return {
        **coverage,
        "per_project": {
            project: {"indexed": stats.indexed, "excluded": stats.excluded,
                      "exclude_rate": stats.exclude_rate}
            for project, stats in sorted(per_project.items())
        },
        "warnings": warnings,
    }


def search(memory_root: Path, query: str, *, project: str | None, limit: int,
           include_decayed: bool) -> list[dict]:
    path = index_path(memory_root)
    if not path.exists():
        raise SearchIndexError("search index not built; run the dream/moc pass first")
    conn = sqlite3.connect(path)
    try:
        sql = ("SELECT f.slice_id, m.project, f.title, bm25(slices_fts) AS bm, "
               "m.link_weight, m.active, m.path "
               "FROM slices_fts f JOIN slice_meta m ON m.slice_id = f.slice_id "
               "WHERE slices_fts MATCH ?")
        params: list[object] = [query]
        if project:
            sql += " AND m.project = ?"
            params.append(project)
        if not include_decayed:
            sql += " AND m.active = 1"
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        raise SearchIndexError(f"search failed: {exc}") from exc
    finally:
        conn.close()
    # rank: lower bm25 is better; add link_weight boost. (recency omitted for determinism in MVP test.)
    ranked = sorted(rows, key=lambda r: (r[3] - 0.1 * (r[4] or 0)))
    return [{"slice_id": r[0], "project": r[1], "title": r[2], "score": r[3], "path": r[6]}
            for r in ranked[:limit]]
```

（`BuildIndexStats` dataclass 刪除——唯一消費者是 `runner.run_moc`，下一步同步改。`ProjectIndexStats` 留作內部累計。）

- [ ] **Step 4: 重寫 runner.py（完整最終版）**

`paulsha_hippo/moc/runner.py` 整檔替換為：

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import faceout, linker, moc_builder, naming, search


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
            "faceout": True, "indexed": indexed, "warnings": warnings,
            "index_stats": index_stats, "index_coverage": index_coverage}
```

- [ ] **Step 5: 更新 `tests/test_search_scoped_corpus.py` 兩處取值**

line 61-65 改為：

```python
    report = search.build_index(memory_root, link_weights={})

    project_stats = report["per_project"]["proj-x"]
    assert project_stats["excluded"] == 0
    assert project_stats["indexed"] == 1
```

line 80-89 改為：

```python
    with caplog.at_level(logging.WARNING, logger="paulsha_hippo.moc.search"):
        report = search.build_index(memory_root, link_weights={})

    project_stats = report["per_project"]["proj-a"]
    assert project_stats["indexed"] == 1
    assert project_stats["excluded"] == 1
    assert project_stats["exclude_rate"] == 0.5
    assert any("proj-a" in record.message and "exclude_rate=0.50" in record.message
               for record in caplog.records)
    assert any("proj-a" in warning and "exclude_rate=0.50" in warning for warning in report["warnings"])
```

- [ ] **Step 6: 跑測試確認 PASS + 全套回歸**

```bash
python3 -m pytest tests/test_moc_search.py tests/test_moc_runner.py tests/test_search_scoped_corpus.py tests/test_dream_cli_moc_warnings.py -v
python3 -m pytest tests/ -q
```
Expected: 目標檔全 PASS；全套 pytest 無 FAIL（`dream run` 的 `passes.moc.summary` 經 orchestrator 自動帶上 `index_coverage`，無需改 `dream/cli.py`）。

- [ ] **Step 7: Commit**

```bash
git add paulsha_hippo/moc/search.py paulsha_hippo/moc/runner.py tests/test_moc_search.py tests/test_moc_runner.py tests/test_search_scoped_corpus.py
git commit -m "feat(moc): 索引 coverage 六欄報表——build_index 回傳契約 dict 並原子落盤（#16）"
```

---

### Task 6: census 三方對賬模組 + `hippo index verify` + README

**Files:**
- Create: `paulsha_hippo/moc/census.py`
- Modify: `paulsha_hippo/moc/cli.py`（新增 `run_index_verify`）
- Modify: `paulsha_hippo/cli.py:163`（`search_p` 區塊之後插入 `index` 子命令）、`paulsha_hippo/cli.py:327`（`_search` handler 之後插入 `_index_verify`）
- Modify: `README.md:27`（日常命令列——四批共用錨行，rebase 後不得整行覆蓋，見 Step 7 合併規則）
- Test: `tests/test_moc_census.py`（新檔）

**Interfaces:**
- Consumes: Task 5 的 `search.coverage_path()`、`search.index_path()`、`search.SearchIndexError`、coverage 六鍵 dict；canonical 分類函式 `fio.read`/`pool_exclude_reason`/`classify_noise`（單一分類真相）
- Produces:
  - `census.CensusEntry`（frozen dataclass：`path: str, slice_id: str | None, memory_layer: str | None`）
  - `census.filesystem_census(memory_root: Path) -> list[CensusEntry]`——純檔案枚舉（`os.walk` + 最小 line-based 欄位抽取，**不走 rglob、不走 yaml**，與 index/coverage 掃描邏輯分離實作）
  - `census.indexed_ids(memory_root: Path) -> set[str]`——自建好的 DB 反查 `slice_meta`
  - `census.reconcile_index(memory_root: Path, coverage: Mapping[str, Any], doc_corpus: object | None = None) -> ReconcileResult`——三方對賬
  - `census.ReconcileResult`（dataclass：`ok: bool, problems: list[str], census_files: int, eligible_ids: set[str], indexed_ids: set[str]`）
  - CLI：`hippo index verify --memory-root <path>`（stdout JSON；exit 0 = 三方一致、exit 1 = 不一致或 coverage 報表缺失）

- [ ] **Step 1: 寫失敗測試（新檔 `tests/test_moc_census.py`）**

```python
"""三方對賬（#16）：filesystem census × coverage 報表 × index DB 反查。"""

from __future__ import annotations

import io
import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from paulsha_hippo import cli
from paulsha_hippo.moc import census, runner, search


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_mixed_tree(root: Path) -> None:
    """1 eligible + 1 pool(review) + 1 noise(echo) + 2 invalid（壞 YAML、壞編碼）+ 1 moc。"""
    k = root / "knowledge" / "proj"
    _write(k / "good--sl-good.md",
           "---\nslice_id: sl-good\nmemory_layer: knowledge\nproject: proj\n"
           "title: 索引良品\ntags: [t]\ncaptured_at: 2026-07-10T00:00:00Z\n---\n真實 知識 內容\n")
    _write(k / "rev--sl-rev.md",
           "---\nslice_id: sl-rev\nmemory_layer: knowledge\nproject: proj\n"
           "title: PR Review\nartifact_kind: review\n---\nreview body\n")
    _write(k / "echo--sl-echo.md",
           "---\nslice_id: sl-echo\nmemory_layer: knowledge\nproject: proj\n"
           "title: X\nartifact_kind: report\n---\n## CWD\n/tmp\n")
    _write(k / "badyaml.md", "---\ntitle: [unclosed\n---\nbody\n")
    k.joinpath("broken.md").write_bytes(b"---\nslice_id: sl-broken\n\xff\xfe---\nbody\n")
    _write(root / "knowledge" / "wiki-moc.md",
           "---\nmemory_layer: moc\nmoc_kind: wiki\n---\n# Wiki\n")


class CensusTests(unittest.TestCase):
    def test_filesystem_census_enumerates_ids_without_yaml(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            entries = census.filesystem_census(root)
            self.assertEqual(len(entries), 6)
            by_name = {Path(e.path).name: e for e in entries}
            self.assertEqual(by_name["good--sl-good.md"].slice_id, "sl-good")
            self.assertEqual(by_name["good--sl-good.md"].memory_layer, "knowledge")
            self.assertEqual(by_name["wiki-moc.md"].memory_layer, "moc")
            self.assertIsNone(by_name["wiki-moc.md"].slice_id)
            self.assertIsNone(by_name["broken.md"].slice_id)  # 壞編碼 → 讀不到欄位

    def test_reconcile_index_passes_on_consistent_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            coverage = search.build_index(root, link_weights={})
            result = census.reconcile_index(root, coverage)
            self.assertEqual(result.problems, [])
            self.assertTrue(result.ok)
            self.assertEqual(result.census_files, 6)
            self.assertEqual(result.eligible_ids, {"sl-good"})
            self.assertEqual(result.indexed_ids, {"sl-good"})

    def test_reconcile_index_detects_missing_indexed_id(self):
        # DB 反查與 coverage 宣稱不一致：模擬索引掉行
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            coverage = search.build_index(root, link_weights={})
            conn = sqlite3.connect(search.index_path(root))
            conn.execute("DELETE FROM slice_meta WHERE slice_id = 'sl-good'")
            conn.commit()
            conn.close()
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("eligible but not indexed" in p for p in result.problems))

    def test_reconcile_index_detects_post_build_disk_drift(self):
        # coverage 出爐後磁碟又長出新 eligible 檔 → census/coverage/DB 三方失衡
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            coverage = search.build_index(root, link_weights={})
            _write(root / "knowledge" / "proj" / "late--sl-late.md",
                   "---\nslice_id: sl-late\nmemory_layer: knowledge\nproject: proj\n"
                   "title: 後補檔\n---\n建完索引後才出現的檔\n")
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("census files" in p for p in result.problems))
            self.assertTrue(any("eligible but not indexed" in p for p in result.problems))

    def test_reconcile_index_reports_unreadable_db(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            coverage = search.build_index(root, link_weights={})
            search.index_path(root).unlink()
            result = census.reconcile_index(root, coverage)
            self.assertFalse(result.ok)
            self.assertTrue(any("index unreadable" in p for p in result.problems))


class IndexVerifyCliTests(unittest.TestCase):
    def test_cli_index_verify_ok(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            search.build_index(root, link_weights={})
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["problems"], [])
            self.assertEqual(payload["eligible"], payload["indexed"])

    def test_cli_index_verify_without_coverage_report_errors(self):
        with TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", tmp])
            self.assertEqual(rc, 1)
            payload = json.loads(buf.getvalue())
            self.assertIn("error", payload)

    def test_cli_index_verify_detects_drift(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_mixed_tree(root)
            search.build_index(root, link_weights={})
            _write(root / "knowledge" / "proj" / "late--sl-late.md",
                   "---\nslice_id: sl-late\nmemory_layer: knowledge\nproject: proj\n"
                   "title: 後補檔\n---\n建完索引後才出現的檔\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 1)
            payload = json.loads(buf.getvalue())
            self.assertFalse(payload["ok"])
            self.assertTrue(payload["problems"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認 FAIL**

```bash
python3 -m pytest tests/test_moc_census.py -v
```
Expected: 收集階段即 FAIL——`ImportError: cannot import name 'census' from 'paulsha_hippo.moc'`。

- [ ] **Step 3: 實作 `paulsha_hippo/moc/census.py`（完整新檔）**

```python
"""三方對賬（#16）：filesystem census × coverage 報表 × index DB 反查。

build_index() 的 coverage 報表出自它自己的掃描迴圈，單獨看是同源自證。
本模組提供分離實作的獨立驗證面（spec §3.2 驗收「防同源自證」）：

- ``filesystem_census()``：純檔案枚舉（os.walk + 最小 line-based 欄位抽取，
  不走 rglob、不走 yaml），建立磁碟上的檔案／ID 全集。
- 獨立 fate pass（``_fate``）：另一條迴圈對每個 census 檔案指派唯一去向
  （invalid / pool-excluded(reason) / noise-excluded(reason) / eligible）。
  分類函式重用 canonical 實作（fio.read / pool_exclude_reason /
  classify_noise）——單一分類真相；但掃描與聚合邏輯與 build_index 分離，
  兩邊各自算出的分佈必須相等。
- ``indexed_ids()``：從建好的 retrieval.db 反查 slice_meta。

三方一致（census 檔數 == coverage.scanned、fate 分佈 == coverage 各欄、
eligible ID 集 == indexed ID 集）才算強不變量 ``indexed IDs == eligible IDs``
成立；全部動態計算，不寫死數字。
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import instruction_corpus
from ..importer.config import default_projects_path, load_projects_config
from ..noise import classify_noise, pool_exclude_reason
from . import frontmatter_io as fio
from .search import SearchIndexError, index_path

# 只認 frontmatter 區塊內「頂格」的兩個身份欄位；值去頭尾引號。
_FM_FIELD = re.compile(r"^(slice_id|memory_layer):\s*(.+?)\s*$")


@dataclass(frozen=True)
class CensusEntry:
    path: str
    slice_id: "str | None"
    memory_layer: "str | None"


@dataclass
class ReconcileResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    census_files: int = 0
    eligible_ids: set[str] = field(default_factory=set)
    indexed_ids: set[str] = field(default_factory=set)


def _minimal_fields(text: str) -> "tuple[str | None, str | None]":
    """line-based frontmatter 欄位抽取：只認頂層 slice_id / memory_layer。

    刻意不用 yaml——census 只需要身份欄位，且實作必須與 fio.read 分離；
    若兩者對真實資料產生分歧，reconcile 會以 problems 顯性回報。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        matched = _FM_FIELD.match(line)
        if matched:
            fields.setdefault(matched.group(1), matched.group(2).strip().strip("'\""))
    return fields.get("slice_id"), fields.get("memory_layer")


def filesystem_census(memory_root: Path) -> list[CensusEntry]:
    """純檔案枚舉：os.walk（非 rglob）列出 knowledge/**/*.md 與其身份欄位。"""
    knowledge = memory_root / "knowledge"
    entries: list[CensusEntry] = []
    if not knowledge.exists():
        return entries
    for dirpath, dirnames, filenames in os.walk(knowledge):
        dirnames.sort()
        for fname in sorted(filenames):
            if not fname.endswith(".md"):
                continue
            fpath = Path(dirpath) / fname
            try:
                text = fpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                entries.append(CensusEntry(str(fpath), None, None))
                continue
            sid, layer = _minimal_fields(text)
            entries.append(CensusEntry(str(fpath), sid, layer))
    return entries


def indexed_ids(memory_root: Path) -> set[str]:
    """DB 反查：完成後的 retrieval.db 內實際存在的 slice_id 全集。"""
    path = index_path(memory_root)
    if not path.exists():
        raise SearchIndexError("search index not built; run the dream/moc pass first")
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT slice_id FROM slice_meta")}
    finally:
        conn.close()


def _fate(fpath: Path, corpus_for: "Callable[[str], object]") -> "tuple[str, str | None]":
    """回傳 (fate, slice_id)；fate ∈ {"invalid", "pool:<reason>", "noise:<reason>", "eligible"}。

    分割規則與 build_index 的掃描分支逐字對齊（見 search.build_index docstring）；
    三方對賬靠「兩條獨立迴圈算出同一分佈」保證，不共用同一次掃描。
    """
    try:
        fm, body = fio.read(fpath.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return "invalid", None
    if not fm:
        return "invalid", None
    layer = fm.get("memory_layer")
    if layer != "knowledge":
        return f"pool:non-knowledge-layer:{layer or 'none'}", None
    sid = fm.get("slice_id")
    if not sid:
        return "invalid", None
    reason = pool_exclude_reason(fm)
    if reason is not None:
        return f"pool:{reason}", str(sid)
    verdict = classify_noise(fm, body, doc_corpus=corpus_for(str(fm.get("project", ""))))
    if verdict.is_noise:
        return f"noise:{verdict.reason}", str(sid)
    return "eligible", str(sid)


def reconcile_index(memory_root: Path, coverage: "Mapping[str, Any]",
                    doc_corpus: "object | None" = None) -> ReconcileResult:
    """三方對賬：filesystem census × coverage 報表 × index DB 反查。

    ``coverage`` 為 build_index() 回傳（或 coverage_path() 落盤）的六鍵報表；
    ``doc_corpus`` 需與當初 build_index 的呼叫一致（dream 路徑為 None）。
    """
    problems: list[str] = []
    census = filesystem_census(memory_root)

    # corpus 解析與 build_index 同源（單一分類真相）
    config = load_projects_config(default_projects_path(memory_root))
    project_roots = {project.slug: project.roots for project in config.projects}
    empty_corpus = instruction_corpus.corpus_for_roots(())
    corpus_cache: dict[str, object] = {}

    def corpus_for(project: str) -> object:
        cached = corpus_cache.get(project)
        if cached is not None:
            return cached
        if project in project_roots:
            corpus = instruction_corpus.corpus_for_roots(project_roots[project])
        elif doc_corpus is not None and not project_roots:
            corpus = doc_corpus
        else:
            corpus = empty_corpus
        corpus_cache[project] = corpus
        return corpus

    invalid = 0
    pool: dict[str, int] = {}
    noise: dict[str, int] = {}
    eligible_ids: set[str] = set()
    eligible_count = 0
    for entry in census:
        fate, sid = _fate(Path(entry.path), corpus_for)
        if fate == "invalid":
            invalid += 1
        elif fate.startswith("pool:"):
            reason = fate[len("pool:"):]
            pool[reason] = pool.get(reason, 0) + 1
        elif fate.startswith("noise:"):
            reason = fate[len("noise:"):]
            noise[reason] = noise.get(reason, 0) + 1
        else:
            eligible_count += 1
            if sid in eligible_ids:
                problems.append(f"duplicate slice_id on disk: {sid}")
            eligible_ids.add(str(sid))

    # 對賬一：census（磁碟真相）↔ coverage（build_index 的宣稱）
    if len(census) != coverage.get("scanned"):
        problems.append(
            f"census files {len(census)} != coverage scanned {coverage.get('scanned')}")
    if invalid != coverage.get("invalid_frontmatter"):
        problems.append(
            f"census invalid {invalid} != coverage invalid_frontmatter "
            f"{coverage.get('invalid_frontmatter')}")
    if pool != coverage.get("pool_excluded"):
        problems.append(
            f"census pool-excluded {pool} != coverage {coverage.get('pool_excluded')}")
    if noise != coverage.get("noise_excluded"):
        problems.append(
            f"census noise-excluded {noise} != coverage {coverage.get('noise_excluded')}")
    if eligible_count != coverage.get("eligible"):
        problems.append(
            f"census eligible {eligible_count} != coverage eligible {coverage.get('eligible')}")
    # 互斥完備分割：每個檔恰有唯一去向，總和必等於全集
    if invalid + sum(pool.values()) + sum(noise.values()) + eligible_count != len(census):
        problems.append("fate partition does not sum to census total")

    # 對賬二：eligible ID 集（磁碟推導）↔ indexed ID 集（DB 反查）
    try:
        in_db = indexed_ids(memory_root)
    except (SearchIndexError, sqlite3.OperationalError) as exc:
        in_db = set()
        problems.append(f"index unreadable: {exc}")
    else:
        missing = sorted(eligible_ids - in_db)
        extra = sorted(in_db - eligible_ids)
        if missing:
            problems.append(f"eligible but not indexed ({len(missing)}): {missing[:5]}")
        if extra:
            problems.append(f"indexed but not eligible ({len(extra)}): {extra[:5]}")
        if len(in_db) != coverage.get("indexed"):
            problems.append(f"db rows {len(in_db)} != coverage indexed {coverage.get('indexed')}")

    return ReconcileResult(ok=not problems, problems=problems, census_files=len(census),
                           eligible_ids=eligible_ids, indexed_ids=in_db)
```

- [ ] **Step 4: 跑 census 測試（CLI 除外）確認 PASS**

```bash
python3 -m pytest tests/test_moc_census.py::CensusTests -v
```
Expected: `CensusTests` 5 個全 PASS；`IndexVerifyCliTests` 仍 FAIL（`SystemExit: 2`，子命令未接線）。

- [ ] **Step 5: CLI 接線**

(a) `paulsha_hippo/moc/cli.py` 整檔替換為：

```python
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
    """`hippo index verify`：三方對賬（census × coverage 落盤報表 × DB 反查）。

    exit 0 = 三方一致（indexed IDs == eligible IDs）；exit 1 = 不一致或
    coverage 報表缺失（尚未跑過 dream/moc pass）。
    """
    memory_root = Path(args.memory_root)
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
```

(b) `paulsha_hippo/cli.py`：在 `search_p.set_defaults(func=_search)`（line 163）之後、`wakeup_p = ...`（line 165）之前插入（遵循 `memory_subparsers.add_parser` 既有模式，跨批次契約 #5）：

```python
    index_p = memory_subparsers.add_parser("index", help="檢索索引維護")
    index_subparsers = index_p.add_subparsers(dest="index_command", required=True)
    index_verify = index_subparsers.add_parser(
        "verify", help="三方對賬：filesystem census × coverage 報表 × index DB 反查")
    index_verify.add_argument("--memory-root", required=True)
    index_verify.set_defaults(func=_index_verify)
```

(c) `paulsha_hippo/cli.py`：在 `_search` handler（line 324-327）之後插入：

```python
def _index_verify(args: argparse.Namespace) -> int:
    from .moc.cli import run_index_verify

    return run_index_verify(args)
```

- [ ] **Step 6: 跑測試確認 PASS**

```bash
python3 -m pytest tests/test_moc_census.py tests/test_cli.py -v
```
Expected: 全部 PASS。

- [ ] **Step 7: README 同步（R-18／R-16）**

`README.md:27`（Usage 段「日常命令：」行；行號為原始 main 快照，一律以行內容定位），把：

```markdown
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo replay`／`hippo bundle`。
```

改為：

```markdown
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo index verify`／`hippo replay`／`hippo bundle`。
```

**合併規則（README 跨批次共用錨行；PR-A Task 12 Step 2／PR-C Task 7 Steps 1-2／PR-F Task 7 Step 3 帶同一條規則）**：若該行已被 sibling 批次改寫（rebase 後與引用的 old 內容不符），不得整行覆蓋——以當前行內容為基底，僅追加本批新增片段（日常命令清單插入本批命令、doctor 註解串接本批說明），保留 sibling 已 merge 的全部新增。本批新增片段只有一個：在命令清單的 `hippo search` 之後插入「／`hippo index verify`」；sibling 已 merge 的命令（`hippo recall`／`hippo usage`／`hippo requeue …`）與其後續補充行（PR-A 蒸餾失敗顯性化／PR-C 維運／PR-F 跨 CLI 消費能力）一律原樣保留，不得覆蓋或刪除。

四批（PR-A/B/C/F）全 merge 後「日常命令」行的收斂目標（merge gate 對照用；`hippo index verify` 與 `hippo usage` 同為 `hippo search` 後插入片段，先後依落地順序可互換，命令齊備即符合收斂；sibling 各自的後續補充行不屬本行、依落地順序緊隨其後）：

```markdown
日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo recall`（跨 CLI 任務相關檢索）／`hippo search`／`hippo index verify`／`hippo usage`（漏斗報表；`mark-applied` 回報 applied）／`hippo replay`／`hippo bundle`／`hippo requeue <session-key>|--all-parked`（parked session 修復後重排）。
```

- [ ] **Step 8: Commit**

```bash
git add paulsha_hippo/moc/census.py paulsha_hippo/moc/cli.py paulsha_hippo/cli.py README.md tests/test_moc_census.py
git commit -m "feat(moc): census 三方對賬模組 + hippo index verify 子命令（#16）"
```

---

### Task 7: 全鏈 E2E 驗收 + changelog 碎片 + CHANGELOG `[Unreleased]` + 收尾驗證

**Files:**
- Test: `tests/test_moc_census.py`（加 `IndexRebuildE2ETests`）
- Create: `changelog.d/fix-16-index-rebuild.md`
- Modify: `CHANGELOG.md`（`[Unreleased]` 段——R-09 以此檔為準，policy_check 的 `_unreleased_has_bullet_entry` 只檢查 `## [Unreleased]` 下有 bullet，與 changelog.d 完全無關）

**Interfaces:**
- Consumes: Task 1-6 全部產出（`runner.run_moc` 的 `index_coverage`、`census.reconcile_index`、CLI）
- Produces: #16 驗收證據（spec §3.2 驗收清單全項的自動化對應）＋R-09 CHANGELOG `[Unreleased]` entry＋changelog.d 碎片（repo 慣例兩者並存——碎片供 release 彙整，`[Unreleased]` 供 R-09 gate）

- [ ] **Step 1: 寫全鏈 E2E 驗收測試**

在 `tests/test_moc_census.py` 末尾（`IndexVerifyCliTests` 之後、`if __name__` 之前）加入：

```python
class IndexRebuildE2ETests(unittest.TestCase):
    def test_overlong_title_poison_noise_full_chain(self):
        """#16 全鏈驗收：超長 title 正常收編、壞 slice fail-soft 不中止整輪、
        excluded 各有去向、三方對賬全綠（動態計算，不寫死數字）。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            k = root / "knowledge" / "proj"
            _write(k / "long.md",
                   "---\nslice_id: sl-long\nmemory_layer: knowledge\nproject: proj\n"
                   "artifact_kind: research\ntitle: " + "超長標題" * 80 + "\n"
                   "captured_at: 2026-07-10T00:00:00Z\n---\n超長標題 slice 的真實內容\n")
            k.joinpath("broken.md").write_bytes(b"---\nslice_id: sl-broken\n\xff\xfe---\nbody\n")
            _write(k / "rev--sl-rev.md",
                   "---\nslice_id: sl-rev\nmemory_layer: knowledge\nproject: proj\n"
                   "title: PR Review\nartifact_kind: review\n---\nreview body\n")
            _write(k / "echo--sl-echo.md",
                   "---\nslice_id: sl-echo\nmemory_layer: knowledge\nproject: proj\n"
                   "title: X\nartifact_kind: report\n---\n## CWD\n/tmp\n")

            result = runner.run_moc(root, now="2026-07-10T00:00:00Z")

            # 超長 title：rename 成功、byte-bound、無 ENAMETOOLONG
            renamed = [p.name for p in k.iterdir() if p.name.endswith("--sl-long.md")]
            self.assertEqual(len(renamed), 1)
            self.assertLessEqual(len(renamed[0].encode("utf-8")), 255)
            # 壞 slice fail-soft：整輪未中止、索引照建、warning 有記
            self.assertTrue(result["indexed"])
            self.assertTrue(any("broken.md" in w for w in result["warnings"]))
            # 強不變量（三方對賬版）：indexed IDs == eligible IDs
            verdict = census.reconcile_index(root, result["index_coverage"])
            self.assertEqual(verdict.problems, [])
            self.assertTrue(verdict.ok)
            self.assertIn("sl-long", verdict.indexed_ids)
            self.assertNotIn("sl-rev", verdict.indexed_ids)     # pool-excluded 有去向
            self.assertNotIn("sl-broken", verdict.indexed_ids)  # invalid 有去向
            # CLI 全鏈（讀 coverage 落盤 + DB 反查）
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["index", "verify", "--memory-root", str(root)])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(buf.getvalue())["ok"])
```

- [ ] **Step 2: 跑 E2E 確認 PASS**

```bash
python3 -m pytest tests/test_moc_census.py -v
```
Expected: 全部 PASS（此為驗收測試——若 FAIL 表示 Task 1-6 有 bug，回頭以 systematic-debugging 修正該 task，不得改弱斷言）。

- [ ] **Step 3: 新增 changelog 碎片**

建立 `changelog.d/fix-16-index-rebuild.md`：

```markdown
### Fixed
- `slugify()`/`target_name()` 檔名以 UTF-8 byte 上限（NAME_MAX 255）截斷且落在 code-point 邊界，`--<slice_id>.md` 尾段永不截斷——超長 LLM title 不再於 MOC reconcile rename 觸發 `ENAMETOOLONG` 中止整輪、導致 reindex 從未執行（#16 根因）。
- MOC naming/linker 對單一壞 slice fail-soft：記 warning 跳過該 slice，不中止整輪 MOC pass。
- `build_index()` 改 temp DB + atomic replace，廢除「先 unlink 現有 DB」：建索引中途失敗時舊索引完整保留。

### Added
- 索引 coverage 六欄報表（`scanned / invalid_frontmatter / pool_excluded(by reason) / noise_excluded(by reason) / eligible / indexed`）：`build_index()` 回傳並原子落盤 `runtime/indexes/retrieval.coverage.json`；`dream run` 輸出新增 `index_coverage`。
- `hippo index verify`：三方對賬（獨立 filesystem census × coverage 報表 × index DB 反查），驗證強不變量 `indexed IDs == eligible IDs`；供 runtime 恢復序列驗證使用。
```

- [ ] **Step 4: 更新 `CHANGELOG.md` `[Unreleased]` 段（R-09 gate 以此檔為準；比照 PR-A Task 12 Step 2）**

把 Step 3 碎片同內容的 bullet 併入 `CHANGELOG.md` 的 `## [Unreleased]`——`### Fixed` 三條 bullet 併入**既有** `### Fixed` 標題下（現行 line 10-11，`install service` interpreter bullet 之後；標題已存在只追加 bullet，不重複建標題——R-04 格式）：

```markdown
- `slugify()`/`target_name()` 檔名以 UTF-8 byte 上限（NAME_MAX 255）截斷且落在 code-point 邊界，`--<slice_id>.md` 尾段永不截斷——超長 LLM title 不再於 MOC reconcile rename 觸發 `ENAMETOOLONG` 中止整輪、導致 reindex 從未執行（#16 根因）。
- MOC naming/linker 對單一壞 slice fail-soft：記 warning 跳過該 slice，不中止整輪 MOC pass。
- `build_index()` 改 temp DB + atomic replace，廢除「先 unlink 現有 DB」：建索引中途失敗時舊索引完整保留。
```

`### Added` 兩條以新標題插入 `### Fixed` 段之後、`## [0.1.0]` 之前：

```markdown
### Added
- 索引 coverage 六欄報表（`scanned / invalid_frontmatter / pool_excluded(by reason) / noise_excluded(by reason) / eligible / indexed`）：`build_index()` 回傳並原子落盤 `runtime/indexes/retrieval.coverage.json`；`dream run` 輸出新增 `index_coverage`。
- `hippo index verify`：三方對賬（獨立 filesystem census × coverage 報表 × index DB 反查），驗證強不變量 `indexed IDs == eligible IDs`；供 runtime 恢復序列驗證使用。
```

（rebase 後若 `[Unreleased]` 已有其他批次的相同 `### Fixed`／`### Added` 標題，同樣把 bullet 併入既有標題下，不重複建標題——R-04 格式。R-09 的 `_unreleased_has_bullet_entry` 只認 `CHANGELOG.md` 的 `[Unreleased]` bullet，changelog.d 碎片本身**不**滿足 R-09。）

- [ ] **Step 5: 全套測試 + policy 檢查**

```bash
python3 -m pytest tests/ -q
python3 -m policy_check --repo .
```
Expected: pytest 全綠（0 failed）；policy_check 無任何 FAIL 項（R-09 由 Step 4 的 `[Unreleased]` bullet 滿足——碎片供 release 彙整，本身不滿足 R-09；README 已同步、無個人絕對路徑進 repo 檔案）。

- [ ] **Step 6: Commit**

```bash
git add tests/test_moc_census.py changelog.d/fix-16-index-rebuild.md CHANGELOG.md
git commit -m "test(moc): #16 全鏈 E2E 驗收——超長 title/壞 slice/三方對賬 + changelog 碎片與 CHANGELOG [Unreleased]"
```

---

## 驗收對照（spec §3.2 → 測試）

| spec 驗收項 | 對應測試 |
|---|---|
| 超長 title fixture：byte-bound、rename 成功、無 ENAMETOOLONG | `test_reconcile_renames_overlong_title_without_enametoolong`、`test_slice_filename_never_exceeds_name_max`、E2E |
| 單一壞 slice：跳過 + warning、其餘正常索引 | `test_reconcile_fail_soft_on_*`、`test_single_bad_slice_fails_soft`、E2E |
| 建索引中途注入失敗：舊 DB 未損毀 | `test_build_index_failure_preserves_existing_db` |
| 三方對賬（防同源自證；census 分離實作、DB 反查、互斥完備分割） | `CensusTests` 全部 + `IndexVerifyCliTests` |
| 實際 runtime 重建後 `indexed == eligible`（三方對賬版） | 工具面：`hippo index verify`（恢復序列 §4.6 由 workflow 執行，契約 #9 不在本 plan） |

## PR 收尾（workflow merge gate 執行，此處僅記格式）

- Branch：`feature/16-index-rebuild`；PR title：`fix(moc): 索引可靠性——slugify byte-bound、fail-soft、atomic reindex、三方對賬`；PR body：`Closes #16` + `.github/pull_request_template.md` checklist 全勾；全程 zh-tw。
