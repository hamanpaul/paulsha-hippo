#!/usr/bin/env python3
"""驗證 harness：importer trivial-session gate 候選判準的離線回測。

用法：
    python3 tools/validate_trivial_gate.py [--memory-root PATH] [--limit N] [--json-out PATH]

方法（見 openspec/AGENTS 或 PR 描述的完整脈絡）：
  1. 掃 ``<memory_root>/archive/queue/2026-*/*--written--*.json``——importer 對每個
     session 第一次成功寫入 inbox 時歸檔的 queue payload 副本（每個 session_id 恰一份，
     已於本次調查驗證：23,909 個 written 檔案、23,909 個相異 session_key、0 筆重複）。
  2. 用 **importer 自己的 normalization 路徑**（``paulsha_hippo.importer.pipeline._extract``
     → 對應 adapter 的 ``extract()`` → ``sanitize_session``）從 payload/transcript 重建
     NormalizedSession——不重寫任何解析邏輯。故意跳過 ``title.apply()``：它只填
     ``session_title``/``title_source``，不影響 gate 判準讀的欄位，且會觸發外部 LLM
     CLI／cache 側效應，離線回測不需要也不應該付這個成本。
  3. 以 ``paulsha_hippo.ledger.processing.fold_states()`` 的「每 session 最終 state」
     當 ground truth（``<tool>:<session_id>`` 鍵，與 importer 的
     ``logical_session_key()`` 同型）。
  4. Transcript 可用性顯式判定（``_transcript_available``）：claude-code／codex 檢查
     ``transcript_path`` 指向的檔案現在是否存在；copilot-cli 檢查
     ``~/.copilot/session-state/<id>/events.jsonl`` 或
     ``history-session-state/session_<id>_*.json`` 現在是否存在。transcript 已不存在時，
     ``_extract`` 會靜默回退成「payload-only」重建（欄位看起來像空的，但那是資料失真，
     不是該 session 原本就空）——這類列一律標記 ``transcript_available=False``，
     從嚴格的『0 誤殺』分母中排除，只在報表另列一份「無法驗證的 promoted/parked」清單。
  5. 決策鏈優先序：``is_self_capture`` → ``is_empty_session`` → trivial 候選。任何已被
     既有 empty-skip／self-skip 攔下的 session，新 gate 根本不會被問到，因此「風險池」
     （at-risk pool）先扣掉這兩層既有防護命中的列，候選判準只對剩餘的列評分——這是唯一
     忠實反映決策鏈行為的算法；忽略這一步會把「empty-skip 早就擋掉的舊資料」錯記成新
     gate 的戰功或風險。

    2026-07-30 實測：全量 23,909 個 written 檔案中，僅 2 筆（皆為 2026-06-24、
    promoter=identity 的既有已知假訊號，見下方 IDENTITY_PROMOTER_NOTE）已被
    is_empty_session 攔下——對 at-risk pool 影響可忽略。

輸出：每個候選判準的 no-findings 捕獲率、promoted/parked 誤殺數（硬約束：必須為 0）、
transcript 缺失而無法驗證的 promoted/parked session_key 清單。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paulsha_hippo import paths  # noqa: E402
from paulsha_hippo.importer.adapters.base import NormalizedSession  # noqa: E402
from paulsha_hippo.importer.pipeline import (  # noqa: E402
    _extract,
    is_empty_session,
    is_self_capture,
    is_trivial_session,
    logical_session_key,
)
from paulsha_hippo.importer.sanitizer import SanitizationError, sanitize_session  # noqa: E402
from paulsha_hippo.ledger import processing  # noqa: E402

IDENTITY_PROMOTER_NOTE = (
    "既有已知假訊號：2026-06-17～06-25 間 6 筆 promoter=identity 的批次時間戳"
    "（如 2026-06-24T11:06:33.395755Z 一次涵蓋 80 個 session_key），早於 is_empty_session"
    "（#7/#8，commit d4cb659，2026-07-07 才落地）——這批是 atomizer pipeline 開發初期用"
    "identity（無 LLM 判斷、逐段直接 promote）promoter 跑的機制驗證批次，不代表真實內容"
    "價值判斷。其中兩筆（claude-code:a0b59665-…、claude-code:63095331-…）目前重建的"
    "NormalizedSession 完全無內容（turn_count=1、user_prompts=0、touched_files=0、"
    "assistant_summary=''）——換成今天的 importer 一樣會被既有 is_empty_session 攔下，"
    "與本次新 gate 無關。本 harness 仍將全部 identity-promoter 列計入 promoted 分母"
    "（不做排除，維持『0 誤殺』對全體 promoted 生效），只在報表加註哪些命中屬於這批。"
)

def _norm_tool(value: object) -> str:
    text = str(value or "").lower().replace("_", "-")
    if text in {"claude", "claude-code"}:
        return "claude-code"
    if text == "codex":
        return "codex"
    if text in {"copilot", "copilot-cli", "github-copilot-cli"}:
        return "copilot-cli"
    return text


def _transcript_available(payload: dict) -> bool:
    """是否有本次可實際讀到的 transcript／history 來源（見模組 docstring 第 4 點）。"""
    tool = _norm_tool(payload.get("tool"))
    if tool in {"claude-code", "codex"}:
        transcript_path = payload.get("transcript_path")
        return isinstance(transcript_path, str) and bool(transcript_path) and Path(transcript_path).is_file()
    if tool == "copilot-cli":
        config_root = payload.get("psc_config_root") or payload.get("PSC_CONFIG_ROOT") or str(paths.copilot_root())
        session_id = payload.get("sessionId") or payload.get("session_id") or ""
        base = Path(config_root)
        if base.name in {"history-session-state", "session-state"}:
            copilot_root = base.parent
        elif base.name == ".copilot":
            copilot_root = base
        elif base.name == "paulshaclaw" and base.parent.name == ".config":
            copilot_root = base.parents[1] / ".copilot"
        else:
            copilot_root = base / ".copilot"
        if (copilot_root / "session-state" / session_id / "events.jsonl").is_file():
            return True
        history_dir = copilot_root / "history-session-state"
        if history_dir.is_dir() and any(history_dir.glob(f"session_{session_id}_*.json")):
            return True
        return False
    return False


# --------------------------------------------------------------------------
# 候選判準：is_trivial_session 的競爭方案。每個候選是 NormalizedSession -> bool。
#
# BLOCKING（review）：勝出候選必須直接呼叫 pipeline.is_trivial_session ——出貨的
# gate 邏輯是什麼，harness 驗證的就必須是同一份函式呼叫，不得在這裡重新實作一份
# 內容相同但物件不同的判準（會漂移：往後改了 pipeline.py 卻忘了同步改這裡，回測
# 數字就不再反映真實行為）。只有「探索但拒絕」的候選（用來對照、佐證勝出候選為何
# 更安全）才允許 local 定義，因為那些從未打算出貨。
# --------------------------------------------------------------------------

def _max_prompt_len(session: NormalizedSession) -> int:
    prompts = session.get("user_prompts") or []
    return max((len(p) for p in prompts if isinstance(p, str)), default=0)


def candidate_generic_short_single_turn(session: NormalizedSession, *, max_len: int = 100) -> bool:
    """探索但拒絕的候選：短 prompt + 無 touched_files + turn_count<=1。見模組 docstring。"""
    if int(session.get("turn_count", 0)) > 1:
        return False
    if session.get("touched_files"):
        return False
    return _max_prompt_len(session) <= max_len


def candidate_combined(session: NormalizedSession) -> bool:
    return is_trivial_session(session) or candidate_generic_short_single_turn(session)


CANDIDATES: dict[str, Callable[[NormalizedSession], bool]] = {
    "title_gen_signature (pipeline.is_trivial_session)": is_trivial_session,
    "generic_short_single_turn<=100": candidate_generic_short_single_turn,
    "combined(signature OR generic)": candidate_combined,
}


@dataclass
class Row:
    key: str
    state: str | None
    transcript_available: bool
    already_gated: bool  # is_self_capture or is_empty_session already True
    session: NormalizedSession


@dataclass
class CandidateResult:
    name: str
    no_findings_total: int = 0
    no_findings_caught: int = 0
    promoted_total: int = 0
    promoted_missed: list[str] = field(default_factory=list)
    parked_total: int = 0
    parked_missed: list[str] = field(default_factory=list)

    @property
    def capture_rate(self) -> float:
        return self.no_findings_caught / self.no_findings_total if self.no_findings_total else 0.0

    @property
    def hard_constraint_ok(self) -> bool:
        return not self.promoted_missed and not self.parked_missed


def collect_rows(memory_root: Path, *, limit: int | None = None) -> tuple[list[Row], dict[str, int]]:
    ground = processing.fold_states(memory_root)
    queue_dirs = sorted(memory_root.glob("archive/queue/2026-*"))
    files: list[Path] = []
    for d in queue_dirs:
        files.extend(sorted(d.glob("*--written--*.json")))
    if limit is not None:
        files = files[:limit]

    errors: Counter[str] = Counter()
    rows: list[Row] = []
    for f in files:
        try:
            raw_text = f.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
        except (OSError, json.JSONDecodeError):
            errors["payload_load"] += 1
            continue
        if not isinstance(payload, dict):
            errors["payload_not_dict"] += 1
            continue
        avail = _transcript_available(payload)
        try:
            result = _extract(f)
        except Exception as exc:  # noqa: BLE001 broad: harness must not crash on bad archives
            errors[f"extract:{type(exc).__name__}"] += 1
            continue
        try:
            session = sanitize_session(dict(result.session))
        except SanitizationError as exc:
            errors[f"sanitize:{type(exc).__name__}"] += 1
            continue
        key = logical_session_key(session)
        already_gated = is_self_capture(session) or is_empty_session(session)
        rows.append(
            Row(
                key=key,
                state=ground.get(key),
                transcript_available=avail,
                already_gated=already_gated,
                session=session,
            )
        )
    return rows, dict(errors)


def evaluate(rows: list[Row]) -> dict[str, CandidateResult]:
    verifiable_at_risk = [r for r in rows if r.transcript_available and not r.already_gated]
    results = {name: CandidateResult(name=name) for name in CANDIDATES}
    for row in verifiable_at_risk:
        for name, fn in CANDIDATES.items():
            res = results[name]
            fires = fn(row.session)
            if row.state == "no-findings":
                res.no_findings_total += 1
                if fires:
                    res.no_findings_caught += 1
            elif row.state == "promoted":
                res.promoted_total += 1
                if fires:
                    res.promoted_missed.append(row.key)
            elif row.state == "parked":
                res.parked_total += 1
                if fires:
                    res.parked_missed.append(row.key)
    return results


def unverifiable_high_value(rows: list[Row]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"promoted": [], "parked": []}
    for row in rows:
        if row.transcript_available:
            continue
        if row.state in out:
            out[row.state].append(row.key)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--memory-root", default=str(paths.memory_root()))
    parser.add_argument("--limit", type=int, default=None, help="只掃前 N 個 written 檔案（開發用）")
    parser.add_argument("--json-out", default=None, help="把完整報表另存成 JSON")
    args = parser.parse_args()

    memory_root = Path(args.memory_root)
    rows, errors = collect_rows(memory_root, limit=args.limit)

    state_counts = Counter(r.state for r in rows)
    verifiable_counts = Counter(r.state for r in rows if r.transcript_available)
    # 只在 transcript 可驗證的列上統計「已被既有 gate 攔下」——non-verifiable 列的
    # is_empty_session=True 常只是「transcript 已消失、extract 退化成空殼」的失真訊號，
    # 不代表這是既有 gate 真的會攔下的內容，混進來會誤導風險池排除的解讀。
    gated_counts = Counter(r.state for r in rows if r.transcript_available and r.already_gated)

    print(f"written 檔案／可重建 session 數：{len(rows)}（載入錯誤：{errors or '無'}）")
    print(f"ground truth 最終 state 分布：{ {str(k): v for k, v in state_counts.items()} }")
    print(f"transcript 可驗證：{ {str(k): v for k, v in verifiable_counts.items()} }")
    print(f"已被既有 self-skip/empty-skip 攔下（風險池排除，僅計 transcript 可驗證列）：{dict(gated_counts)}")

    unverifiable = unverifiable_high_value(rows)
    print(
        "\ntranscript 缺失而無法驗證的 promoted：{} 筆；parked：{} 筆"
        "（不計入硬約束分母，僅列出供人工複查）".format(
            len(unverifiable["promoted"]), len(unverifiable["parked"])
        )
    )
    if unverifiable["parked"]:
        print("  無法驗證的 parked session_key：", unverifiable["parked"])

    results = evaluate(rows)
    print("\n候選判準比較（分母為 transcript 可驗證 且 未被既有 gate 攔下 的風險池）：")
    header = f"{'candidate':32s} {'no-findings 捕獲率':>20s} {'promoted 誤殺':>14s} {'parked 誤殺':>12s} {'硬約束':>8s}"
    print(header)
    for name, res in results.items():
        ok = "PASS" if res.hard_constraint_ok else "FAIL"
        print(
            f"{name:32s} {res.no_findings_caught:>6d}/{res.no_findings_total:<6d} "
            f"({res.capture_rate * 100:5.1f}%)   {len(res.promoted_missed):>10d}    "
            f"{len(res.parked_missed):>9d}   {ok:>8s}"
        )
        if res.promoted_missed:
            print("    promoted 誤殺 session_key：", res.promoted_missed[:20])
        if res.parked_missed:
            print("    parked 誤殺 session_key：", res.parked_missed[:20])

    if args.json_out:
        payload = {
            "written_files": len(rows),
            "errors": errors,
            "state_counts": {str(k): v for k, v in state_counts.items()},
            "verifiable_counts": {str(k): v for k, v in verifiable_counts.items()},
            "already_gated_counts": {str(k): v for k, v in gated_counts.items()},
            "unverifiable_high_value": unverifiable,
            "candidates": {
                name: {
                    "no_findings_total": res.no_findings_total,
                    "no_findings_caught": res.no_findings_caught,
                    "capture_rate": res.capture_rate,
                    "promoted_total": res.promoted_total,
                    "promoted_missed": res.promoted_missed,
                    "parked_total": res.parked_total,
                    "parked_missed": res.parked_missed,
                    "hard_constraint_ok": res.hard_constraint_ok,
                }
                for name, res in results.items()
            },
        }
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\n完整報表已寫入 {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
