# Ledger 撕裂行修復與 health 假陽性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 append-only ledger 的撕裂行可被偵測、可救回、可隔離，使 `hippo dream run` 不再因一行 60 天前的壞資料永久停在 `partial`，並修正 health `invalid_frontmatter` 因生成的 MOC 索引檔而恆不歸零的假陽性。

**Architecture:** 新增 `paulsha_hippo/ledger/integrity.py` 作為單一責任模組，負責「行分類 / 原子修復 / quarantine 讀寫」三件事，不知道 janitor 與 doctor 的存在。消費端各自接上：`import_log.read_import_records_tolerant` 讀 quarantine 以排除已知壞行、`ops.run_doctor` 印出掃描結果、`cli` 註冊 `ledger repair` 子命令。`ledger/dream.py` 的 health 掃描獨立修一個 MOC 排除條件。

**Tech Stack:** Python 3.12、標準庫（`json` / `hashlib` / `os` / `pathlib`）、pytest、argparse。無新增外部相依。

## Global Constraints

- 語言：PR 標題、內文、commit message、changelog 碎片一律 zh-tw（repo 屬 `github.com/hamanpaul/*`）。
- `VERSION` 維持 `0.1.1`，本 PR 非 release PR，不得變動。
- 必須新增 `changelog.d/64-ledger-torn-line-repair.md` 碎片（R-09）。
- 分支已建立為 `feature/64-ledger-torn-line-repair`；不得直接 commit 到 `main`。
- PR body 必須含 `Closes #64`（R-17）。
- 不得變更 `paulsha_hippo/dream/orchestrator.py` 的 warning → partial 判定語意。
- 所有寫入 ledger 的路徑必須：先備份 → 同目錄 temp → `fsync` → `os.replace`。
- 行雜湊的正規定義（寫入端與讀取端必須一致）：`sha256(line.encode("utf-8"))`，其中 `line` 是 `content.splitlines()` 的元素，**不含結尾換行、不做 strip**。

---

### Task 1: ledger 行分類核心

**Files:**
- Create: `paulsha_hippo/ledger/integrity.py`
- Test: `tests/test_ledger_integrity.py`

**Interfaces:**
- Consumes: 無（本 plan 的第一個任務）
- Produces:
  - `line_sha256(line: str) -> str`
  - `classify_line(line: str) -> tuple[str, str]` — 回傳 `(classification, reason)`，classification 為 `"ok"` / `"recoverable"` / `"unrecoverable"`
  - `NUL: str`、`QUARANTINE_SUFFIX: str`

**設計要點：** `read_import_records_tolerant` 目前把**空白行也計為 bad line**。若分類把空白行當 `ok`，「修完後 janitor 計數歸零」這個不變量就不成立。因此空白行歸類為 `unrecoverable`、reason `blank`，走 quarantine 讓 janitor 停止回報，而不是假裝它正常。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger_integrity.py
import json

from paulsha_hippo.ledger import integrity


def test_classify_ok_line():
    line = json.dumps({"status": "written"}, sort_keys=True)
    assert integrity.classify_line(line) == ("ok", "")


def test_classify_nul_torn_line_is_recoverable():
    payload = json.dumps({"status": "written", "id": "abc"}, sort_keys=True)
    torn = payload[:10] + "\x00" * 577 + payload[10:]
    assert integrity.classify_line(torn) == ("recoverable", "nul-torn")


def test_classify_blank_line_is_unrecoverable_blank():
    assert integrity.classify_line("") == ("unrecoverable", "blank")
    assert integrity.classify_line("   ") == ("unrecoverable", "blank")


def test_classify_truncated_json_is_unrecoverable():
    assert integrity.classify_line('{"status": "writ') == ("unrecoverable", "unparseable")


def test_classify_all_nul_line_is_unrecoverable():
    assert integrity.classify_line("\x00" * 32) == ("unrecoverable", "blank")


def test_line_sha256_is_over_raw_line_without_strip():
    import hashlib

    line = '  {"a": 1}  '
    assert integrity.line_sha256(line) == hashlib.sha256(line.encode("utf-8")).hexdigest()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paulsha_hippo.ledger.integrity'`

- [ ] **Step 3: Write minimal implementation**

```python
# paulsha_hippo/ledger/integrity.py
"""Append-only ledger 的位元組層完好性：撕裂行分類、原子修復、quarantine。

本模組只認識 JSONL 檔案與行，不知道 janitor / doctor / dream 的存在。
消費端自行接上（issue #64）。
"""

from __future__ import annotations

import hashlib
import json

NUL = "\x00"
QUARANTINE_SUFFIX = ".quarantine"


def line_sha256(line: str) -> str:
    """行的正規雜湊：不含結尾換行、不做 strip，寫入端與讀取端共用同一定義。"""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def classify_line(line: str) -> tuple[str, str]:
    """回傳 (classification, reason)。

    classification 為 "ok" / "recoverable" / "unrecoverable"。空白行歸為
    unrecoverable：既有 reader 也把空白行計為 bad line，若視為 ok 則
    「修復後壞行計數歸零」的不變量不成立。
    """
    stripped = line.strip().replace(NUL, "").strip()
    if not stripped:
        return ("unrecoverable", "blank")

    candidate = line.strip()
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        pass
    else:
        return ("ok", "")

    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return ("unrecoverable", "unparseable")
    return ("recoverable", "nul-torn")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ledger/integrity.py tests/test_ledger_integrity.py
git commit -m "feat(ledger): 撕裂行分類核心

Refs #64"
```

---

### Task 2: 檔案與目錄掃描（唯讀）

**Files:**
- Modify: `paulsha_hippo/ledger/integrity.py`
- Test: `tests/test_ledger_integrity.py`

**Interfaces:**
- Consumes: `classify_line`、`line_sha256`（Task 1）
- Produces:
  - `LineFinding` dataclass，欄位 `line_no: int`（1-indexed）、`sha256: str`、`classification: str`、`reason: str`
  - `scan_file(path: Path) -> list[LineFinding]` — 只回傳非 `ok` 的行
  - `scan_ledger_dir(memory_root: Path) -> dict[str, list[LineFinding]]` — key 為檔名（非完整路徑），只含有壞行的檔

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_ledger_integrity.py
import hashlib
from pathlib import Path


def _write_ledger(tmp_path: Path, name: str, lines: list[str]) -> Path:
    path = tmp_path / "runtime" / "ledger" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_scan_file_reports_line_numbers_and_classification(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:4] + "\x00" * 8 + good[4:]
    path = _write_ledger(tmp_path, "import.jsonl", [good, torn, '{"n": '])

    findings = integrity.scan_file(path)

    assert [f.line_no for f in findings] == [2, 3]
    assert findings[0].classification == "recoverable"
    assert findings[0].reason == "nul-torn"
    assert findings[0].sha256 == integrity.line_sha256(torn)
    assert findings[1].classification == "unrecoverable"
    assert findings[1].reason == "unparseable"


def test_scan_file_on_clean_ledger_returns_empty(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", [json.dumps({"n": 1})])
    assert integrity.scan_file(path) == []


def test_scan_file_does_not_modify_the_file(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    path = _write_ledger(tmp_path, "import.jsonl", [good, good[:3] + "\x00" + good[3:]])
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    integrity.scan_file(path)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_scan_ledger_dir_skips_clean_files_and_missing_dir(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    _write_ledger(tmp_path, "clean.jsonl", [good])
    _write_ledger(tmp_path, "import.jsonl", [good[:3] + "\x00" + good[3:]])

    result = integrity.scan_ledger_dir(tmp_path)

    assert set(result) == {"import.jsonl"}
    assert integrity.scan_ledger_dir(tmp_path / "nope") == {}


def test_scan_ledger_dir_ignores_quarantine_sidecars(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    _write_ledger(tmp_path, "import.jsonl", [good])
    _write_ledger(tmp_path, "import.jsonl.quarantine", ["not json at all"])

    assert integrity.scan_ledger_dir(tmp_path) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: FAIL with `AttributeError: module 'paulsha_hippo.ledger.integrity' has no attribute 'scan_file'`

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 paulsha_hippo/ledger/integrity.py（import 區補 dataclass / Path）
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LineFinding:
    line_no: int
    sha256: str
    classification: str
    reason: str


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()


def scan_file(path: Path) -> list[LineFinding]:
    """唯讀掃描單一 JSONL，只回傳非 ok 的行。行號為 1-indexed。"""
    if not path.is_file():
        return []
    findings: list[LineFinding] = []
    for index, line in enumerate(_read_lines(path), start=1):
        classification, reason = classify_line(line)
        if classification == "ok":
            continue
        findings.append(
            LineFinding(
                line_no=index,
                sha256=line_sha256(line),
                classification=classification,
                reason=reason,
            )
        )
    return findings


def ledger_dir(memory_root: Path) -> Path:
    return memory_root / "runtime" / "ledger"


def scan_ledger_dir(memory_root: Path) -> dict[str, list[LineFinding]]:
    """掃描 runtime/ledger/*.jsonl，只回傳有壞行的檔（key 為檔名）。"""
    directory = ledger_dir(memory_root)
    if not directory.is_dir():
        return {}
    result: dict[str, list[LineFinding]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        findings = scan_file(path)
        if findings:
            result[path.name] = findings
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ledger/integrity.py tests/test_ledger_integrity.py
git commit -m "feat(ledger): 撕裂行檔案與目錄掃描

Refs #64"
```

---

### Task 3: quarantine 清單讀寫（fail-closed）

**Files:**
- Modify: `paulsha_hippo/ledger/integrity.py`
- Test: `tests/test_ledger_integrity.py`

**Interfaces:**
- Consumes: `LineFinding`、`line_sha256`、`QUARANTINE_SUFFIX`（Task 1–2）
- Produces:
  - `quarantine_path(ledger_path: Path) -> Path`
  - `read_quarantine(ledger_path: Path) -> frozenset[str] | None` — 回傳已隔離行的 sha256 集合；清單不存在回傳空集合；**清單存在但不可解析回傳 `None`**（fail-closed 訊號）
  - `append_quarantine(ledger_path: Path, findings: list[LineFinding], *, now: str) -> int`

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_ledger_integrity.py
def test_read_quarantine_missing_returns_empty(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", [json.dumps({"n": 1})])
    assert integrity.read_quarantine(path) == frozenset()


def test_append_then_read_quarantine_roundtrip(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", ['{"n": '])
    findings = integrity.scan_file(path)

    written = integrity.append_quarantine(path, findings, now="2026-07-27T00:00:00Z")

    assert written == 1
    assert integrity.read_quarantine(path) == frozenset({findings[0].sha256})
    record = json.loads(integrity.quarantine_path(path).read_text().splitlines()[0])
    assert record["line_no"] == 1
    assert record["sha256"] == findings[0].sha256
    assert record["reason"] == "unparseable"
    assert record["quarantined_at"] == "2026-07-27T00:00:00Z"


def test_append_quarantine_is_idempotent(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", ['{"n": '])
    findings = integrity.scan_file(path)
    integrity.append_quarantine(path, findings, now="2026-07-27T00:00:00Z")

    written = integrity.append_quarantine(path, findings, now="2026-07-27T01:00:00Z")

    assert written == 0
    assert len(integrity.quarantine_path(path).read_text().splitlines()) == 1


def test_read_quarantine_corrupt_returns_none_fail_closed(tmp_path):
    path = _write_ledger(tmp_path, "import.jsonl", ['{"n": '])
    integrity.quarantine_path(path).write_text("這不是 JSON\n", encoding="utf-8")

    assert integrity.read_quarantine(path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'read_quarantine'`

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 paulsha_hippo/ledger/integrity.py
def quarantine_path(ledger_path: Path) -> Path:
    return ledger_path.with_name(ledger_path.name + QUARANTINE_SUFFIX)


def read_quarantine(ledger_path: Path) -> frozenset[str] | None:
    """已隔離行的 sha256 集合。

    清單不存在 → 空集合。清單存在但任一行不可解析 → None，呼叫端必須
    fail-closed（壞行一律照計），不得因清單不可讀而放行。
    """
    path = quarantine_path(ledger_path)
    if not path.is_file():
        return frozenset()
    hashes: set[str] = set()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
        digest = record.get("sha256")
        if not isinstance(digest, str) or not digest:
            return None
        hashes.add(digest)
    return frozenset(hashes)


def append_quarantine(ledger_path: Path, findings: list[LineFinding], *, now: str) -> int:
    """把不可救回行記入 quarantine 清單，回傳實際新增筆數（已存在者不重複記）。"""
    existing = read_quarantine(ledger_path)
    if existing is None:
        raise ValueError(f"quarantine 清單不可解析：{quarantine_path(ledger_path)}")
    rows = [f for f in findings if f.classification == "unrecoverable" and f.sha256 not in existing]
    if not rows:
        return 0
    path = quarantine_path(ledger_path)
    with path.open("a", encoding="utf-8") as handle:
        for finding in rows:
            handle.write(
                json.dumps(
                    {
                        "line_no": finding.line_no,
                        "sha256": finding.sha256,
                        "reason": finding.reason,
                        "quarantined_at": now,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())
    return len(rows)
```

在 import 區補上 `import os`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: PASS（15 passed）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ledger/integrity.py tests/test_ledger_integrity.py
git commit -m "feat(ledger): quarantine 清單讀寫，清單損毀時 fail-closed

Refs #64"
```

---

### Task 4: 原子修復（備份 → temp → fsync → replace）

**Files:**
- Modify: `paulsha_hippo/ledger/integrity.py`
- Test: `tests/test_ledger_integrity.py`

**Interfaces:**
- Consumes: `scan_file`、`append_quarantine`、`NUL`（Task 1–3）
- Produces: `repair_file(path: Path, *, apply: bool, now: str) -> dict` — 回傳 `{"file": str, "recoverable": int, "unrecoverable": int, "quarantined": int, "backup": str | None, "applied": bool}`

**設計要點：** 不可救回行**原樣保留**在檔中，只有可救回行被改寫。無任何可救回行時不寫檔、不備份（冪等）。

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_ledger_integrity.py
def test_dry_run_does_not_touch_file(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" * 5 + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [good, torn])
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = integrity.repair_file(path, apply=False, now="2026-07-27T00:00:00Z")

    assert result["recoverable"] == 1
    assert result["applied"] is False
    assert result["backup"] is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not integrity.quarantine_path(path).exists()


def test_apply_removes_only_nul_and_preserves_every_other_byte(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    other = json.dumps({"n": 2}, sort_keys=True)
    torn = good[:3] + "\x00" * 577 + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [other, torn, other])

    result = integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert result["applied"] is True
    assert json.loads(lines[1]) == {"n": 1}
    assert lines[0] == other and lines[2] == other
    assert "\x00" not in path.read_text(encoding="utf-8")


def test_apply_writes_backup_identical_to_original(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [torn])
    original = path.read_bytes()

    result = integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    backup = Path(result["backup"])
    assert backup.exists()
    assert backup.read_bytes() == original


def test_apply_is_idempotent(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [torn])
    integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")
    after_first = path.read_bytes()
    backups_before = sorted(p.name for p in path.parent.glob("import.jsonl.bak-repair-*"))

    result = integrity.repair_file(path, apply=True, now="2026-07-27T02:00:00Z")

    assert result["recoverable"] == 0
    assert result["backup"] is None
    assert path.read_bytes() == after_first
    assert sorted(p.name for p in path.parent.glob("import.jsonl.bak-repair-*")) == backups_before


def test_apply_keeps_unrecoverable_line_in_place_and_quarantines_it(tmp_path):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    broken = '{"n": '
    path = _write_ledger(tmp_path, "import.jsonl", [torn, broken])

    result = integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == broken
    assert result["quarantined"] == 1
    assert integrity.read_quarantine(path) == frozenset({integrity.line_sha256(broken)})


def test_replace_failure_leaves_original_intact(tmp_path, monkeypatch):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [torn])
    original = path.read_bytes()

    def boom(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr(integrity.os, "replace", boom)

    with pytest.raises(OSError):
        integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    assert path.read_bytes() == original
    backups = list(path.parent.glob("import.jsonl.bak-repair-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_backup_failure_aborts_before_touching_original(tmp_path, monkeypatch):
    good = json.dumps({"n": 1}, sort_keys=True)
    torn = good[:3] + "\x00" + good[3:]
    path = _write_ledger(tmp_path, "import.jsonl", [torn])
    original = path.read_bytes()

    real_write_bytes = Path.write_bytes

    def boom(self, data):
        if ".bak-repair-" in self.name:
            raise OSError("backup failed")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", boom)

    with pytest.raises(OSError):
        integrity.repair_file(path, apply=True, now="2026-07-27T00:00:00Z")

    assert path.read_bytes() == original
```

測試檔頂端需 `import pytest`。備份失敗與替換失敗都讓例外往外拋——這是維護窗口的單次操作，
半修復狀態靜默通過遠比大聲失敗危險。替換失敗時備份保留供人工處置。

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'repair_file'`

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 paulsha_hippo/ledger/integrity.py
def _backup_name(path: Path, now: str) -> str:
    stamp = now.replace(":", "").replace("-", "")
    return f"{path.name}.bak-repair-{stamp}"


def repair_file(path: Path, *, apply: bool, now: str) -> dict:
    findings = scan_file(path)
    recoverable = [f for f in findings if f.classification == "recoverable"]
    unrecoverable = [f for f in findings if f.classification == "unrecoverable"]
    result: dict = {
        "file": str(path),
        "recoverable": len(recoverable),
        "unrecoverable": len(unrecoverable),
        "quarantined": 0,
        "backup": None,
        "applied": False,
    }
    if not apply:
        return result

    if unrecoverable:
        result["quarantined"] = append_quarantine(path, unrecoverable, now=now)

    if not recoverable:
        # 冪等：沒有可救回行就不寫檔、不備份。
        return result

    targets = {f.line_no for f in recoverable}
    lines = _read_lines(path)
    rebuilt = [
        line.replace(NUL, "") if index in targets else line
        for index, line in enumerate(lines, start=1)
    ]

    backup = path.with_name(_backup_name(path, now))
    backup.write_bytes(path.read_bytes())

    tmp = path.with_name(f".{path.name}.repair.tmp")
    with tmp.open("w", encoding="utf-8", errors="surrogateescape") as handle:
        handle.write("\n".join(rebuilt) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)

    result["backup"] = str(backup)
    result["applied"] = True
    return result


def repair_ledger_dir(memory_root: Path, *, apply: bool, now: str) -> list[dict]:
    directory = ledger_dir(memory_root)
    if not directory.is_dir():
        return []
    results = []
    for path in sorted(directory.glob("*.jsonl")):
        outcome = repair_file(path, apply=apply, now=now)
        if outcome["recoverable"] or outcome["unrecoverable"]:
            results.append(outcome)
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: PASS（20 passed）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ledger/integrity.py tests/test_ledger_integrity.py
git commit -m "feat(ledger): 撕裂行原子修復，可救回行救回、不可救回行原地隔離

Refs #64"
```

---

### Task 5: janitor 讀取端排除已隔離行

**Files:**
- Modify: `paulsha_hippo/ledger/import_log.py:65-96`（`read_import_records_tolerant`）
- Test: `tests/test_ledger_integrity.py`

**Interfaces:**
- Consumes: `integrity.read_quarantine`、`integrity.line_sha256`（Task 1、3）
- Produces: `read_import_records_tolerant` 行為變更——已隔離行不計入 `bad_line_count`

**設計要點：** 這是「雙邊隔離」的讀取端。少了這一半，不可救回行仍會讓 janitor 每輪 warn、dream 永久 partial，問題原封不動。

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_ledger_integrity.py
from paulsha_hippo.ledger import import_log


def test_quarantined_bad_line_is_not_counted(tmp_path):
    good = json.dumps({"idempotency_key": "k", "status": "written"}, sort_keys=True)
    broken = '{"n": '
    path = _write_ledger(tmp_path, "import.jsonl", [good, broken])
    integrity.append_quarantine(path, integrity.scan_file(path), now="2026-07-27T00:00:00Z")

    records, bad = import_log.read_import_records_tolerant(path)

    assert bad == 0
    assert len(records) == 1


def test_unquarantined_bad_line_is_still_counted(tmp_path):
    good = json.dumps({"idempotency_key": "k", "status": "written"}, sort_keys=True)
    path = _write_ledger(tmp_path, "import.jsonl", [good, '{"n": '])

    _records, bad = import_log.read_import_records_tolerant(path)

    assert bad == 1


def test_corrupt_quarantine_list_fails_closed(tmp_path):
    good = json.dumps({"idempotency_key": "k", "status": "written"}, sort_keys=True)
    path = _write_ledger(tmp_path, "import.jsonl", [good, '{"n": '])
    integrity.quarantine_path(path).write_text("這不是 JSON\n", encoding="utf-8")

    _records, bad = import_log.read_import_records_tolerant(path)

    assert bad == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ledger_integrity.py -k quarantin -v`
Expected: FAIL — `test_quarantined_bad_line_is_not_counted` 得到 `bad == 1`（尚未接上讀取端）

- [ ] **Step 3: Write minimal implementation**

把 `paulsha_hippo/ledger/import_log.py` 的 `read_import_records_tolerant` 迴圈改為：

```python
def read_import_records_tolerant(import_log_path: Path) -> tuple[list[dict], int]:
    """Read import records while skipping empty or malformed JSONL rows.

    已進入 quarantine 清單的壞行不計入 bad_line_count（issue #64 雙邊隔離的
    讀取端）。清單存在但不可解析時 fail-closed：壞行一律照計。
    """
    if not import_log_path.exists():
        return [], 0

    try:
        content = import_log_path.read_text()
    except FileNotFoundError:
        return [], 0

    if not content.strip():
        return [], 0

    from . import integrity

    quarantined = integrity.read_quarantine(import_log_path)
    if quarantined is None:
        quarantined = frozenset()

    records: list[dict] = []
    bad_line_count = 0
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            if integrity.line_sha256(raw_line) not in quarantined:
                bad_line_count += 1
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if integrity.line_sha256(raw_line) not in quarantined:
                bad_line_count += 1
            continue

        records.append(record)

    return records, bad_line_count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ledger_integrity.py tests/ -k "quarantin or import" -v`
Expected: PASS，且既有 janitor 測試不退步

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ledger/import_log.py tests/test_ledger_integrity.py
git commit -m "fix(janitor): bad-line 計數排除已隔離行，清單損毀時 fail-closed

Refs #64"
```

---

### Task 6: health 掃描排除生成的 MOC 索引檔

**Files:**
- Modify: `paulsha_hippo/ledger/dream.py:176-189`
- Test: `tests/test_ledger_integrity.py`

**Interfaces:**
- Consumes: 無新介面
- Produces: health 的 `invalid_frontmatter` / `generic_title` / `unknown_project` 不再計入 `memory_layer: moc` 的檔案

**設計要點：** 用 frontmatter 欄位而非 `*-moc.md` 檔名判別。`moc/moc_builder.py:_write_moc` 寫入 `memory_layer: moc`，knowledge slice 為 `memory_layer: knowledge`。

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_ledger_integrity.py
from paulsha_hippo.ledger import dream as dream_ledger


def _write_slice(root, name, body="內容", **fm):
    fields = {
        "slice_id": "sl-1",
        "project": "p",
        "checksum": "deadbeef",
        "memory_layer": "knowledge",
    }
    fields.update(fm)
    lines = ["---"] + [f"{k}: {v}" for k, v in fields.items()] + ["---", body]
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_generated_moc_is_not_counted_as_invalid_frontmatter(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "wiki-moc.md").write_text(
        "---\nmemory_layer: moc\nmoc_kind: wiki\n---\n# Wiki MOC\n", encoding="utf-8"
    )

    health = dream_ledger.backlog_census(tmp_path, now="2026-07-27T00:00:00Z")

    assert health["invalid_frontmatter"] == 0


def test_genuinely_broken_slice_is_still_counted(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "broken.md").write_text(
        "---\nmemory_layer: knowledge\n---\n內容\n", encoding="utf-8"
    )

    health = dream_ledger.backlog_census(tmp_path, now="2026-07-27T00:00:00Z")

    assert health["invalid_frontmatter"] == 1
```

包住 knowledge 掃描迴圈的函式是 `backlog_census(memory_root, *, now=None)`（`paulsha_hippo/ledger/dream.py:110`）。

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ledger_integrity.py -k moc -v`
Expected: FAIL — `test_generated_moc_is_not_counted_as_invalid_frontmatter` 得到 `1`

- [ ] **Step 3: Write minimal implementation**

在 `paulsha_hippo/ledger/dream.py` 的 knowledge 掃描迴圈中，讀完 frontmatter 後、`required` 檢查之前插入：

```python
            # 生成的 MOC 索引檔依設計不帶 slice frontmatter，計入會讓這些指標
            # 永遠無法歸零而失去診斷價值（issue #64）。以欄位而非檔名判別。
            if str(frontmatter.get("memory_layer") or "") == "moc":
                continue
            required = ("slice_id", "project", "checksum", "memory_layer")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ledger_integrity.py -k moc -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/ledger/dream.py tests/test_ledger_integrity.py
git commit -m "fix(dream): health 計數排除帶 memory_layer moc 的生成索引檔

Refs #64"
```

---

### Task 7: `hippo ledger repair` 子命令與 doctor 輸出

**Files:**
- Modify: `paulsha_hippo/cli.py`（子命令註冊區約 423-431 行的 `locks` 之後；handler 放在 `_locks_cleanup_legacy` 附近）
- Modify: `paulsha_hippo/ops.py:1303` 附近（doctor 輸出 dream lock 那行之後）
- Test: `tests/test_ledger_integrity.py`

**Interfaces:**
- Consumes: `integrity.repair_ledger_dir`、`integrity.scan_ledger_dir`（Task 2、4）
- Produces: CLI `hippo ledger repair --memory-root <path> [--apply] [--now <iso>]`

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_ledger_integrity.py
from paulsha_hippo import cli


def test_cli_ledger_repair_dry_run_leaves_file_untouched(tmp_path, capsys):
    good = json.dumps({"n": 1}, sort_keys=True)
    path = _write_ledger(tmp_path, "import.jsonl", [good[:3] + "\x00" + good[3:]])
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    code = cli.main(["ledger", "repair", "--memory-root", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload[0]["recoverable"] == 1
    assert payload[0]["applied"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_cli_ledger_repair_apply_fixes_line(tmp_path, capsys):
    good = json.dumps({"n": 1}, sort_keys=True)
    path = _write_ledger(tmp_path, "import.jsonl", [good[:3] + "\x00" + good[3:]])

    code = cli.main([
        "ledger", "repair", "--memory-root", str(tmp_path),
        "--apply", "--now", "2026-07-27T00:00:00Z",
    ])

    capsys.readouterr()
    assert code == 0
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0]) == {"n": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ledger_integrity.py -k cli_ledger -v`
Expected: FAIL — argparse 以 `invalid choice: 'ledger'` 結束

- [ ] **Step 3: Write minimal implementation**

在 `paulsha_hippo/cli.py` 的 `locks` 子命令註冊之後加入：

```python
    ledger_p = memory_subparsers.add_parser("ledger", help="append-only ledger 維運")
    ledger_sub = ledger_p.add_subparsers(dest="ledger_command", required=True)
    ledger_repair = ledger_sub.add_parser(
        "repair",
        help="修復 ledger 撕裂行（僅維護窗口；預設 dry-run）",
    )
    ledger_repair.add_argument("--memory-root", required=True)
    ledger_repair.add_argument("--apply", action="store_true")
    ledger_repair.add_argument("--now", default=None,
                               help="ISO8601 時間戳；未給時取當下 UTC")
    ledger_repair.set_defaults(func=_ledger_repair)
```

handler（放在 `_locks_cleanup_legacy` 之後）：

```python
def _ledger_repair(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from paulsha_hippo.ledger import integrity

    now = args.now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = integrity.repair_ledger_dir(
        Path(args.memory_root), apply=args.apply, now=now
    )
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    return 0
```

doctor 輸出：在 `paulsha_hippo/ops.py` 印出 dream lock 那行（約 1303 行）之後加入：

```python
    from .ledger import integrity

    ledger_findings = integrity.scan_ledger_dir(memory_root)
    if not ledger_findings:
        print("- ledger 完整性：✓ 無撕裂行")
    else:
        for name, findings in ledger_findings.items():
            recoverable = [f for f in findings if f.classification == "recoverable"]
            unrecoverable = [f for f in findings if f.classification == "unrecoverable"]
            lines = ", ".join(str(f.line_no) for f in findings[:10])
            suffix = " …" if len(findings) > 10 else ""
            print(
                f"- ledger 完整性：✗ {name} 可救回 {len(recoverable)}／"
                f"不可救回 {len(unrecoverable)}（行 {lines}{suffix}）"
                "——`hippo ledger repair --memory-root <root> --apply`"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ledger_integrity.py -v`
Expected: PASS（全數通過）

手動驗證 doctor 對真實 memory root 的輸出與耗時：

Run: `time python3 -m paulsha_hippo doctor 2>&1 | grep "ledger 完整性"`
Expected: 指出 `import.jsonl` 有 1 個可救回行（行 230）；若 doctor 總耗時因此顯著增加，改為先以
`b"\x00" in path.read_bytes()` 快篩、只有命中的檔才逐行解析。

- [ ] **Step 5: Commit**

```bash
git add paulsha_hippo/cli.py paulsha_hippo/ops.py tests/test_ledger_integrity.py
git commit -m "feat(cli): 新增 hippo ledger repair 與 doctor ledger 完整性檢查

Refs #64"
```

---

### Task 8: 交付治理

**Files:**
- Create: `changelog.d/64-ledger-torn-line-repair.md`
- Modify: `openspec/changes/issue-64-ledger-torn-line-repair/tasks.md`（勾選已完成項）

**Interfaces:**
- Consumes: Task 1–7 的全部產出
- Produces: 可合併的 PR

- [ ] **Step 1: 寫 changelog 碎片**

```markdown
### Fixed
- ledger 撕裂行（嵌入 NUL 位元組的 JSONL 行）不再讓 janitor 每輪回報壞行、使
  `hippo dream run` 永久停在 `partial`。新增 `hippo doctor` 的 ledger 完整性檢查與
  `hippo ledger repair`（預設 dry-run，`--apply` 才寫入）：可救回的行原地救回、
  不可救回的行原樣保留並記入 quarantine 清單，janitor 的壞行計數排除已隔離行。
- dream health 的 `invalid_frontmatter` 不再把生成的 MOC 索引檔當成缺欄位的 slice，
  該指標先前恆等於 MOC 檔數而無法歸零。
```

- [ ] **Step 2: 跑全套測試**

Run: `python3 -m pytest -q`
Expected: 全數通過，無退步

- [ ] **Step 3: 跑 policy 與 openspec 驗證**

Run: `python3 -m policy_check --repo . && openspec validate issue-64-ledger-torn-line-repair --strict`
Expected: policy 無 failure（既有 R-22 warn 可留）；openspec valid

- [ ] **Step 4: 確認 VERSION 未變**

Run: `git diff main --stat -- VERSION`
Expected: 無輸出

- [ ] **Step 5: Commit 並開 PR**

```bash
git add changelog.d/64-ledger-torn-line-repair.md openspec/changes/issue-64-ledger-torn-line-repair/tasks.md
git commit -m "chore(issue-64): changelog 碎片與 tasks 進度

Refs #64"
git push -u origin feature/64-ledger-torn-line-repair
```

PR body 必須包含 `Closes #64`，並勾滿 `.github/pull_request_template.md` 的 checklist。

---

## 合併後的 runtime 修復（不在 PR diff 內）

PR 合併並重裝部署副本後執行：

```bash
cp ~/.agents/memory/runtime/ledger/import.jsonl \
   ~/.agents/memory/runtime/ledger/import.jsonl.manual-bak-2026-07-27
hippo ledger repair --memory-root ~/.agents/memory              # dry-run，確認只有 line 230
hippo ledger repair --memory-root ~/.agents/memory --apply
hippo requeue claude-code:3c0a8d08-7925-4694-89f1-85a2838bc586 --memory-root ~/.agents/memory
hippo requeue claude-code:66f5eed9-214b-4595-b4bf-835a87496e81 --memory-root ~/.agents/memory
hippo dream run --memory-root ~/.agents/memory
```

驗收：`dream run` 的 `status` 為 `"ok"`、`health.invalid_frontmatter` 為 `0`、inbox 的 119 個
滯留 fragment 被消化。

**注意：** `requeue` 的 session key 格式為 `<tool>:<session_id>`，執行前先以
`hippo doctor` 或 ledger fold 確認兩個 key 的實際字面值。
