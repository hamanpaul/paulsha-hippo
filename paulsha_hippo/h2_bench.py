"""#167：H2 離線 A／B／C benchmark runner 與計分（#148 H2 C 組，2026-09-27 決策紀錄 v4）。

三組都在 #164 的凍結候選集（as-of BM25 top-12）上運作：

- **A**：凍結的 BM25 top-3（不經送出前過濾，等同線上現況）。
- **B**：Claude（``sonnet``）一次看候選的標題＋內文前 800 字元，回 0–3 則。
- **C**：JEV（pin ``jev-1.13.0``）對每則候選問一題 yes／no Choice（同一個 batch request），
  選 yes 者依 BM25 名次取前 3；全部 no 就回 0 則。confidence 只記錄，不參與選擇。

B 與 C 看同一份候選：只含通過送出前掃描（``egress == eligible``）的候選，品質比較才公平；
過濾造成的損失由隱私門檻另外量。C 送出前會用私有字詞清單再掃一次實際 payload，命中就不送，
並計入政策違規檢查。

計分依 v4：Precision@3、每題不相關數、task 命中率、正確回 0 則比例為主，另報注入字元量、
延遲、成本、可送出涵蓋率與被過濾掉的相關候選比例；go 條件見 ``GO_THRESHOLDS``。

單元測試一律注入 fake transport／runner；API key 只從 environment 的 ``TYPESAFE_API_KEY``
讀取，CLI 子行程會移除它。金額以 ``decimal.Decimal`` 計算，落盤為字串。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import statistics
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from paulsha_hippo import h2_offline

__all__ = [
    "ARMS",
    "C_CRITERIA",
    "GO_THRESHOLDS",
    "BenchError",
    "ClaudeFilter",
    "JevClient",
    "build_c_request",
    "decide",
    "load_gold",
    "parse_b_reply",
    "run",
    "score",
    "stability_tasks",
]

ARMS = ("A", "B", "C")
RECORD_SCHEMA = "hippo-h2-run/v1"
MAX_SELECT = 3
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"
JEV_USD_PER_INPUT_MTOK = Decimal("0.042")
_SECRET_ENV = ("TYPESAFE_API_KEY",)
_RETRYABLE = frozenset({429, 529})

#: C 的問法版本。rev0 在開發集上過度字面：gold 判相關的 39 則中 JEV 只說 yes 8 則（例如 task 必須遵守的
#: repo 流程規則被讀成「一般背景」）。依 JEV 官方 literal-reading 建議，把隱含條件寫進 criteria（rev1，
#: 開發集上唯一一次修訂；B 維持與 gold 標註者相同的定義）。
C_REVISION = "rev1"
C_INSTRUCTION_REV0 = ("Would including `candidates[{index}]` materially help an agent plan or execute `task`? "
                      "Judge only from the text shown.")
C_CRITERIA_REV0 = {
    "yes": "The memory contains a directly applicable constraint, known failure, prior decision, or validated "
           "experience that would change, confirm, or prevent a concrete decision in this task.",
    "no": "The memory only shares the same repository or topic, is general background, is an execution trace "
          "with no direct use for this task, or is about unrelated work.",
}
C_INSTRUCTION = ("Should an engineer read `candidates[{index}]` before working on `task`? "
                 "Judge only from the text shown.")
C_CRITERIA = {
    "yes": "The note is about the same component, failure, feature, or workflow as the task, or it states a rule, "
           "constraint, known failure, prior decision, or validated result that applies to carrying out this task, "
           "including repository workflow rules the task must follow.",
    "no": "The note is about a different component or problem, or it is general background that would not change "
          "how this task is carried out.",
}
B_SYSTEM = ("You select memory notes for an engineering agent. You have no tools. "
            "Reply with exactly one JSON object and nothing else.")
B_INSTRUCTIONS = (
    "Select the notes (at most 3, possibly none) whose inclusion would materially help an agent plan or execute "
    "this task: a directly applicable constraint, known failure, prior decision, or validated experience that "
    "would change, confirm, or prevent a concrete decision in this task. Do not select notes that only share the "
    "same repository or topic, are general background, are execution traces with no direct use, or concern "
    "unrelated work. Output {\"selected\": [\"<id>\", ...]} using only the ids shown.")

GO_THRESHOLDS = {
    "c_vs_a_hit_rate_max_drop": 0.05,
    "c_vs_a_precision_gain": 0.10,
    "c_vs_a_irrelevant_reduction": 0.30,
    "c_correct_abstain_min": 0.90,
    "c_vs_b_precision_max_gap": 0.05,
    "c_vs_b_hit_rate_max_gap": 0.10,
    "c_vs_b_latency_reduction": 0.70,
    "c_vs_b_cost_reduction": 0.90,
    "egress_violations_max": 0,
    "egress_coverage_min": 0.80,
    "excluded_relevant_max": 0.05,
}


class BenchError(RuntimeError):
    """單次呼叫失敗；``kind`` 為 transport／invalid_output／config。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def cid(candidate: dict) -> str:
    return f"c{candidate['rank']}"


def eligible_candidates(task: dict) -> list:
    return [c for c in task["candidates"] if c["egress"] == "eligible"]


# ---------------------------------------------------------------- C（JEV）

def build_c_request(task: dict) -> dict | None:
    """C 組 request（不含 model）；task 或候選不可送出時回 None。"""
    cands = eligible_candidates(task)
    if task["task_egress"] != "eligible" or not cands:
        return None
    state = {"task": {"title": task["title"], "body": task["body"]},
             "candidates": [{"id": cid(c), "title": c["title"], "excerpt": c["body_view"]} for c in cands]}
    questions = {cid(c): {"type": "choice", "instructions": C_INSTRUCTION.format(index=i),
                          "criteria": dict(C_CRITERIA)} for i, c in enumerate(cands)}
    return {"state": state, "questions": questions}


def egress_violations(payload: dict, deny_terms: Iterable[str]) -> list:
    """送出前再掃一次實際 payload 的 state；回傳命中的規則（空 list 才可送出）。"""
    state = payload["state"]
    texts = [state["task"]["title"], state["task"]["body"]]
    texts += [f"{c['title']}\n{c['excerpt']}" for c in state["candidates"]]
    hits = set()
    for text in texts:
        hits.update(h2_offline.deny_scan(text, tuple(deny_terms)))
    return sorted(hits)


def _urllib_transport(url: str, headers: dict, payload: dict, timeout: float = 60.0) -> tuple:
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}"), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            parsed = json.loads(body or b"{}")
        except json.JSONDecodeError:
            parsed = {"_raw": body[:300].decode("utf-8", "replace")}
        return exc.code, parsed, {k.lower(): v for k, v in (exc.headers or {}).items()}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BenchError("transport", f"TypeSafe 傳輸失敗：{exc}") from exc


class JevClient:
    """TypeSafe System One HTTP client（只用 Choice）。429／529 退避重試，其他錯誤不重試。"""

    def __init__(self, *, transport: Callable | None = None, environ: dict | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 max_attempts: int = 4) -> None:
        self._transport = transport or _urllib_transport
        self._environ = os.environ if environ is None else environ
        self._clock, self._sleep, self._max = clock, sleep, max(1, max_attempts)
        self.model = JEV_MODEL

    def judge(self, request: dict) -> tuple:
        key = self._environ.get("TYPESAFE_API_KEY", "")
        if not key:
            raise BenchError("config", "缺 TYPESAFE_API_KEY（只從 environment 讀取）")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {"model": JEV_MODEL, **request}
        started, attempt = self._clock(), 0
        while True:
            attempt += 1
            status, body, reply_headers = self._transport(JEV_ENDPOINT, headers, payload)
            if status == 200:
                break
            if status not in _RETRYABLE or attempt >= self._max:
                raise BenchError("transport", f"TypeSafe HTTP {status}：{json.dumps(body, ensure_ascii=False)[:300]}")
            try:
                delay = min(max(float(reply_headers.get("retry-after", "")), 0.0), 30.0)
            except ValueError:
                delay = min(2.0 ** (attempt - 1), 30.0)
            self._sleep(delay)
        return body, round((self._clock() - started) * 1000), attempt


def parse_c_reply(task: dict, request: dict, body: dict) -> tuple:
    """驗證 JEV answers；回傳 (選中的 cid（依 BM25 名次、最多 3）, 每題判斷)。"""
    answers = body.get("answers")
    expected = set(request["questions"])
    if not isinstance(answers, dict) or set(answers) != expected:
        raise BenchError("invalid_output", "JEV answers 與問題不一致")
    details = {}
    for key in expected:
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get("choice") not in C_CRITERIA:
            raise BenchError("invalid_output", f"JEV answer {key} 不合法")
        details[key] = {"choice": answer["choice"], "confidence": answer.get("confidence")}
    ranked = [cid(c) for c in eligible_candidates(task)]
    selected = [k for k in ranked if details[k]["choice"] == "yes"][:MAX_SELECT]
    return selected, details


# ---------------------------------------------------------------- B（Claude）

def b_prompt(task: dict) -> str:
    blocks = [f"[{cid(c)}] {c['title']}\n{c['body_view']}" for c in eligible_candidates(task)]
    return (f"TASK (a GitHub issue an agent is about to work on):\n{task['title']}\n{task['body']}\n\n"
            "CANDIDATE MEMORY NOTES (id, title, excerpt):\n\n" + "\n\n".join(blocks) + "\n\n" + B_INSTRUCTIONS)


def parse_b_reply(text: str, allowed: list) -> list:
    text = text.strip()
    start = text.find("{")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:]) if start >= 0 else (None, 0)
    except json.JSONDecodeError as exc:
        raise BenchError("invalid_output", f"B 回覆不是 JSON：{text[:120]!r}") from exc
    selected = value.get("selected") if isinstance(value, dict) else None
    if not isinstance(selected, list) or len(selected) > MAX_SELECT or len(set(selected)) != len(selected) \
            or any(s not in allowed for s in selected):
        raise BenchError("invalid_output", f"B selected 不合法：{selected!r}")
    return [s for s in allowed if s in selected]  # 依 BM25 名次排列，便於比較


def _claude_runner(argv: list, cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _SECRET_ENV}
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=600,
                              stdin=subprocess.DEVNULL, env=env)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise BenchError("transport", f"claude 執行失敗：{exc}") from exc
    if done.returncode != 0:
        raise BenchError("transport", f"claude exit {done.returncode}：{done.stderr.strip()[:300]}")
    return done.stdout


class ClaudeFilter:
    """B 組：Claude CLI 純補全（關閉工具與 MCP、空暫存目錄、取代內建 system prompt）。"""

    def __init__(self, *, model: str = "sonnet", runner: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.model, self._runner, self._clock = model, runner or _claude_runner, clock

    def select(self, task: dict) -> tuple:
        prompt = b_prompt(task)
        with tempfile.TemporaryDirectory(prefix="hippo-h2-b-") as tmp:
            argv = ["claude", "--model", self.model, "--safe-mode", "--disable-slash-commands",
                    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--tools", "",
                    "--no-session-persistence", "--system-prompt", B_SYSTEM, "--output-format", "json", "-p", prompt]
            started = self._clock()
            stdout = self._runner(argv, Path(tmp))
            wall = round((self._clock() - started) * 1000)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BenchError("transport", "claude 輸出不是 JSON") from exc
        if data.get("is_error"):
            raise BenchError("transport", f"claude 回報錯誤：{str(data.get('result'))[:200]}")
        text = str(data.get("result", ""))
        selected = parse_b_reply(text, [cid(c) for c in eligible_candidates(task)])
        model = next(iter(data.get("modelUsage") or {self.model: None}))
        usd = data.get("total_cost_usd")
        cost = str(Decimal(str(usd))) if isinstance(usd, (int, float)) and not isinstance(usd, bool) else None
        return selected, wall, cost, model, hashlib.sha256(prompt.encode("utf-8")).hexdigest(), \
            hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- runner

def stability_tasks(task_ids: list, seed: str = "h2-20260927", n: int = 8) -> list:
    return sorted(task_ids, key=lambda t: hashlib.sha256(f"{seed}|stability|{t}".encode()).hexdigest())[:n]


def _load_records(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(frozen: dict, task_ids: list, arms: Iterable[str], out_path: Path, *, jev: JevClient | None = None,
        claude: ClaudeFilter | None = None, deny_terms: Iterable[str] = (), repeat: int = 0,
        now: Callable[[], str] | None = None, progress: Callable[[dict], None] | None = None) -> dict:
    """對 ``task_ids`` 執行各組並逐筆 append；已有成功紀錄的 (arm, task, repeat) 跳過。"""
    now = now or (lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["arm"], r["task_id"], r["repeat"]) for r in _load_records(out_path) if r.get("error") is None}
    tasks = {t["task_id"]: t for t in frozen["tasks"]}
    deny_terms = tuple(deny_terms)
    stats = {"written": 0, "errors": 0}
    for arm in arms:
        for task_id in task_ids:
            if (arm, task_id, repeat) in done:
                continue
            task = tasks[task_id]
            record = {"schema": RECORD_SCHEMA, "arm": arm, "task_id": task_id, "repeat": repeat,
                      "frozen_digest": frozen["digest"], "started_at": now(), "selected": [], "details": None,
                      "wall_ms": 0, "cost_usd": "0", "model": None, "request_sha256": None,
                      "response_sha256": None, "egress": None, "error": None}
            try:
                if arm == "A":
                    record["selected"] = [cid(c) for c in task["candidates"][:MAX_SELECT]]
                    record["model"] = "bm25-frozen"
                elif arm == "B":
                    if claude is None:
                        raise BenchError("config", "B 組需要 ClaudeFilter")
                    if eligible_candidates(task):
                        sel, wall, cost, model, req_sha, resp_sha = claude.select(task)
                        record.update(selected=sel, wall_ms=wall, cost_usd=cost, model=model,
                                      request_sha256=req_sha, response_sha256=resp_sha)
                    else:
                        record["model"] = "no-eligible-candidate"
                elif arm == "C":
                    if jev is None:
                        raise BenchError("config", "C 組需要 JevClient")
                    request = build_c_request(task)
                    if request is None:
                        record.update(model="not-sent", egress="task-or-candidates-not-eligible")
                    else:
                        hits = egress_violations(request, deny_terms)
                        if hits:
                            record.update(model="not-sent", egress=f"blocked:{','.join(hits)}")
                        else:
                            body, wall, attempts = jev.judge(request)
                            sel, details = parse_c_reply(task, request, body)
                            tokens = (body.get("usage") or {}).get("input_tokens")
                            usd = (Decimal(tokens) * JEV_USD_PER_INPUT_MTOK / Decimal(1_000_000)
                                   if isinstance(tokens, int) and not isinstance(tokens, bool) else None)
                            record.update(selected=sel, details={"c_revision": C_REVISION, "answers": details},
                                          wall_ms=wall, egress="sent",
                                          cost_usd=None if usd is None else str(usd), model=str(body.get("model")),
                                          request_sha256=_sha256(request), response_sha256=_sha256(body))
                else:
                    raise BenchError("config", f"未知 arm：{arm}")
            except BenchError as exc:
                if exc.kind == "config":
                    raise
                record["error"] = {"kind": exc.kind, "message": str(exc)[:300]}
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stats["written"] += 1
            stats["errors"] += record["error"] is not None
            if progress:
                progress(record)
    return stats


# ---------------------------------------------------------------- 計分

def load_gold(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {key: entry["gold"] for key, entry in data["pairs"].items()}


def _chars(task: dict, selected: list) -> int:
    by_id = {cid(c): c for c in task["candidates"]}
    return sum(len(by_id[s]["title"]) + len(by_id[s]["body_view"]) for s in selected)


def _arm_metrics(frozen: dict, gold: dict, task_ids: list, records: dict) -> dict:
    tasks = {t["task_id"]: t for t in frozen["tasks"]}
    selected_total = relevant_selected = irrelevant_selected = 0
    hit_tasks = hit_hits = abstain_tasks = abstain_ok = 0
    walls, costs, chars, missing = [], [], [], 0
    for task_id in task_ids:
        rec = records.get(task_id)
        if rec is None or rec.get("error") is not None:
            missing += 1
            continue
        labels = {cid(c): gold.get(f"{task_id}#{cid(c)}") for c in tasks[task_id]["candidates"]}
        has_relevant = any(v == "relevant" for v in labels.values())
        sel = rec["selected"]
        rel = sum(1 for s in sel if labels.get(s) == "relevant")
        irr = sum(1 for s in sel if labels.get(s) == "irrelevant")
        selected_total += len(sel)
        relevant_selected += rel
        irrelevant_selected += irr
        if has_relevant:
            hit_tasks += 1
            hit_hits += rel > 0
        else:
            abstain_tasks += 1
            abstain_ok += len(sel) == 0
        walls.append(rec["wall_ms"])
        if rec.get("cost_usd") is not None:
            costs.append(Decimal(str(rec["cost_usd"])))
        chars.append(_chars(tasks[task_id], sel))
    scored = len(task_ids) - missing
    return {
        "tasks": len(task_ids),
        "missing": missing,
        "precision_at_3": round(relevant_selected / selected_total, 6) if selected_total else None,
        "irrelevant_per_task": round(irrelevant_selected / scored, 6) if scored else None,
        "task_hit_rate": round(hit_hits / hit_tasks, 6) if hit_tasks else None,
        "correct_abstain_rate": round(abstain_ok / abstain_tasks, 6) if abstain_tasks else None,
        "selected_per_task": round(selected_total / scored, 6) if scored else None,
        "chars_per_task": round(statistics.fmean(chars), 1) if chars else None,
        "median_latency_ms": statistics.median(walls) if walls else None,
        "cost_per_task_usd": str(sum(costs, Decimal(0)) / Decimal(len(costs))) if costs else None,
        "counts": {"selected": selected_total, "relevant": relevant_selected, "irrelevant": irrelevant_selected,
                   "hit_tasks": hit_tasks, "abstain_tasks": abstain_tasks},
    }


def _privacy(frozen: dict, gold: dict, task_ids: list, c_records: dict) -> dict:
    tasks = [t for t in frozen["tasks"] if t["task_id"] in set(task_ids)]
    cands = [c for t in tasks for c in t["candidates"]]
    eligible = sum(1 for c in cands if c["egress"] == "eligible")
    relevant = [(t["task_id"], c) for t in tasks for c in t["candidates"] if gold.get(f"{t['task_id']}#{cid(c)}") == "relevant"]
    excluded_relevant = sum(1 for _t, c in relevant if c["egress"] != "eligible")
    violations = sum(1 for r in c_records.values() if str(r.get("egress") or "").startswith("blocked"))
    return {
        "egress_coverage": round(eligible / len(cands), 6) if cands else None,
        "excluded_relevant_ratio": round(excluded_relevant / len(relevant), 6) if relevant else 0.0,
        "egress_violations": violations,
        "tasks_sent": sum(1 for r in c_records.values() if r.get("egress") == "sent"),
    }


def _stability(records: list, arm: str) -> dict:
    first = {r["task_id"]: r["selected"] for r in records if r["arm"] == arm and r["repeat"] == 0 and r.get("error") is None}
    second = {r["task_id"]: r["selected"] for r in records if r["arm"] == arm and r["repeat"] == 1 and r.get("error") is None}
    common = sorted(set(first) & set(second))
    same = sum(1 for t in common if set(first[t]) == set(second[t]))
    return {"pairs": len(common), "identical_selection": same}


def score(frozen: dict, gold: dict, task_ids: list, records: list) -> dict:
    by_arm = {arm: {r["task_id"]: r for r in records if r["arm"] == arm and r["repeat"] == 0 and r.get("error") is None}
              for arm in ARMS}
    return {
        "arms": {arm: _arm_metrics(frozen, gold, task_ids, by_arm[arm]) for arm in ARMS},
        "privacy": _privacy(frozen, gold, task_ids, by_arm["C"]),
        "stability": {arm: _stability(records, arm) for arm in ("B", "C")},
    }


def _ok(value) -> bool:
    return value is not None


def decide(summary: dict, thresholds: dict | None = None) -> dict:
    t = dict(GO_THRESHOLDS if thresholds is None else thresholds)
    a, b, c = (summary["arms"][x] for x in ARMS)
    p = summary["privacy"]
    reasons = [f"{arm} 缺 {m['missing']} 題" for arm, m in summary["arms"].items() if m["missing"]]
    gates = {}
    gates["c_vs_a_hit_rate"] = _ok(c["task_hit_rate"]) and _ok(a["task_hit_rate"]) and \
        c["task_hit_rate"] >= round(a["task_hit_rate"] - t["c_vs_a_hit_rate_max_drop"], 6)
    precision_gain = _ok(c["precision_at_3"]) and _ok(a["precision_at_3"]) and \
        c["precision_at_3"] >= round(a["precision_at_3"] + t["c_vs_a_precision_gain"], 6)
    irrelevant_cut = _ok(c["irrelevant_per_task"]) and _ok(a["irrelevant_per_task"]) and a["irrelevant_per_task"] > 0 and \
        c["irrelevant_per_task"] <= a["irrelevant_per_task"] * (1 - t["c_vs_a_irrelevant_reduction"])
    gates["c_vs_a_quality"] = bool(precision_gain or irrelevant_cut)
    gates["c_correct_abstain"] = _ok(c["correct_abstain_rate"]) and c["correct_abstain_rate"] >= t["c_correct_abstain_min"]
    gates["c_vs_b_precision"] = _ok(c["precision_at_3"]) and _ok(b["precision_at_3"]) and \
        c["precision_at_3"] >= round(b["precision_at_3"] - t["c_vs_b_precision_max_gap"], 6)
    gates["c_vs_b_hit_rate"] = _ok(c["task_hit_rate"]) and _ok(b["task_hit_rate"]) and \
        c["task_hit_rate"] >= round(b["task_hit_rate"] - t["c_vs_b_hit_rate_max_gap"], 6)
    latency_cut = _ok(c["median_latency_ms"]) and _ok(b["median_latency_ms"]) and b["median_latency_ms"] > 0 and \
        c["median_latency_ms"] <= b["median_latency_ms"] * (1 - t["c_vs_b_latency_reduction"])
    cost_cut = _ok(c["cost_per_task_usd"]) and _ok(b["cost_per_task_usd"]) and \
        Decimal(c["cost_per_task_usd"]) <= Decimal(b["cost_per_task_usd"]) * Decimal(str(1 - t["c_vs_b_cost_reduction"]))
    gates["c_vs_b_efficiency"] = bool(latency_cut or cost_cut)
    gates["privacy_violations"] = p["egress_violations"] <= t["egress_violations_max"]
    gates["privacy_coverage"] = _ok(p["egress_coverage"]) and p["egress_coverage"] >= t["egress_coverage_min"]
    gates["privacy_excluded_relevant"] = p["excluded_relevant_ratio"] <= t["excluded_relevant_max"]
    failed = [name for name, ok in gates.items() if not ok]
    reasons += [f"未過：{name}" for name in failed]
    return {"decision": "go" if not reasons else "no-go", "gates": gates, "reasons": reasons, "thresholds": t,
            "detail": {"precision_gain": bool(precision_gain), "irrelevant_cut": bool(irrelevant_cut),
                       "latency_cut": bool(latency_cut), "cost_cut": bool(cost_cut)}}
