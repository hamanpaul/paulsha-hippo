"""#167：H2 A／B／C benchmark。只用合成凍結集與 fake transport／runner，不打真網路、不含真實記憶。"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from paulsha_hippo import cli
from paulsha_hippo import h2_bench as B


def _cand(rank, egress="eligible", title=None, body="note body"):
    return {"rank": rank, "slice_id": f"s{rank}", "bm25": -10.0 + rank, "captured_at": "2026-08-01T00:00:00.000000Z",
            "title": title or f"note {rank}", "body_view": body, "egress": egress,
            "egress_reasons": [] if egress == "eligible" else ["private-term"]}


def _task(task_id, n=5, egress_task="eligible", excluded=()):
    return {"task_id": task_id, "split": "hidden-pool", "repo": "github.com/example/widget", "issue": 1,
            "as_of": "2026-09-01T00:00:00Z", "title": f"task {task_id}", "body": "do the thing",
            "fts_query": "", "task_egress": egress_task, "task_egress_reasons": [], "matched": n,
            "excluded_before_rank": {},
            "candidates": [_cand(r, "excluded" if r in excluded else "eligible") for r in range(1, n + 1)],
            "arm_a": [f"s{r}" for r in range(1, min(n, 3) + 1)]}


def _frozen(*tasks):
    return {"format": "hippo-h2-frozen-candidates", "version": 1, "digest": "d" * 64, "tasks": list(tasks)}


class FakeTransport:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers, payload):
        self.calls.append((url, headers, payload))
        return self.replies.pop(0)


def _yes_answers(request, yes):
    return {k: {"type": "choice", "choice": "yes" if k in yes else "no", "confidence": 0.9,
                "probabilities": {"yes": 0.9, "no": 0.1}} for k in request["questions"]}


def _jev(replies, sleeps=None):
    return B.JevClient(transport=FakeTransport(replies), environ={"TYPESAFE_API_KEY": "k-test"},
                       clock=iter([0.0, 0.2] * 100).__next__, sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


def test_c_request_only_sends_eligible_candidates():
    task = _task("t1", excluded=(2,))
    request = B.build_c_request(task)
    ids = [c["id"] for c in request["state"]["candidates"]]
    assert ids == ["c1", "c3", "c4", "c5"] and set(request["questions"]) == set(ids)
    assert "candidates[1]" in request["questions"]["c3"]["instructions"]
    assert request["questions"]["c1"]["criteria"] == B.C_CRITERIA
    assert B.build_c_request(_task("t2", egress_task="excluded")) is None
    assert B.build_c_request(_task("t3", n=2, excluded=(1, 2))) is None


def test_c_selects_yes_by_bm25_rank_and_caps_at_three(tmp_path):
    task = _task("t1", n=6)
    request = B.build_c_request(task)
    body = {"model": "jev-1.13.0", "answers": _yes_answers(request, {"c6", "c2", "c5", "c4"}),
            "usage": {"input_tokens": 1000, "output_tokens": 30}}
    jev = _jev([(200, body, {})])
    out = tmp_path / "r.jsonl"
    B.run(_frozen(task), ["t1"], ["C"], out, jev=jev, deny_terms=("acme",))
    rec = B._load_records(out)[0]
    assert rec["selected"] == ["c2", "c4", "c5"]
    assert rec["cost_usd"] == str(Decimal(1000) * Decimal("0.042") / Decimal(1_000_000))
    assert rec["egress"] == "sent" and rec["model"] == "jev-1.13.0" and len(rec["request_sha256"]) == 64
    url, headers, payload = jev._transport.calls[0]
    assert payload["model"] == "jev-1.13.0" and headers["Authorization"] == "Bearer k-test"


def test_c_abstains_when_all_no(tmp_path):
    task = _task("t1")
    request = B.build_c_request(task)
    body = {"model": "jev-1.13.0", "answers": _yes_answers(request, set()), "usage": {"input_tokens": 10}}
    B.run(_frozen(task), ["t1"], ["C"], tmp_path / "r.jsonl", jev=_jev([(200, body, {})]))
    assert B._load_records(tmp_path / "r.jsonl")[0]["selected"] == []


def test_c_pre_send_scan_blocks_payload_without_calling_api(tmp_path):
    task = _task("t1")
    task["candidates"][0]["body_view"] = "see ACME board notes"
    jev = _jev([])
    B.run(_frozen(task), ["t1"], ["C"], tmp_path / "r.jsonl", jev=jev, deny_terms=("acme",))
    rec = B._load_records(tmp_path / "r.jsonl")[0]
    assert rec["egress"] == "blocked:private-term" and rec["selected"] == [] and jev._transport.calls == []


def test_jev_retries_429_and_records_non_retryable_errors(tmp_path):
    task = _task("t1")
    request = B.build_c_request(task)
    ok = (200, {"model": "jev-1.13.0", "answers": _yes_answers(request, {"c1"}), "usage": {"input_tokens": 5}}, {})
    sleeps = []
    B.run(_frozen(task), ["t1"], ["C"], tmp_path / "a.jsonl", jev=_jev([(429, {}, {"retry-after": "2"}), ok], sleeps))
    assert sleeps == [2.0] and B._load_records(tmp_path / "a.jsonl")[0]["selected"] == ["c1"]
    B.run(_frozen(task), ["t1"], ["C"], tmp_path / "b.jsonl", jev=_jev([(422, {"detail": "bad"}, {})]))
    assert B._load_records(tmp_path / "b.jsonl")[0]["error"]["kind"] == "transport"


def test_missing_key_aborts_run(tmp_path):
    jev = B.JevClient(transport=FakeTransport([]), environ={})
    with pytest.raises(B.BenchError):
        B.run(_frozen(_task("t1")), ["t1"], ["C"], tmp_path / "r.jsonl", jev=jev)


def test_invalid_jev_answers_are_invalid_output(tmp_path):
    task = _task("t1")
    body = {"model": "jev-1.13.0", "answers": {"c1": {"choice": "maybe"}}, "usage": {}}
    B.run(_frozen(task), ["t1"], ["C"], tmp_path / "r.jsonl", jev=_jev([(200, body, {})]))
    assert B._load_records(tmp_path / "r.jsonl")[0]["error"]["kind"] == "invalid_output"


def _claude(reply, cost=0.004):
    calls = []

    def runner(argv, cwd):
        calls.append(argv)
        return json.dumps({"result": reply, "total_cost_usd": cost, "modelUsage": {"claude-sonnet-5": {}}})

    return B.ClaudeFilter(runner=runner, clock=iter([0.0, 1.5] * 100).__next__), calls


def test_b_selects_valid_ids_and_uses_pure_completion_flags(tmp_path):
    task = _task("t1", excluded=(2,))
    claude, calls = _claude('{"selected": ["c4", "c1"]}')
    B.run(_frozen(task), ["t1"], ["B"], tmp_path / "r.jsonl", claude=claude)
    rec = B._load_records(tmp_path / "r.jsonl")[0]
    assert rec["selected"] == ["c1", "c4"] and rec["cost_usd"] == "0.004" and rec["wall_ms"] == 1500
    argv = calls[0]
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--system-prompt") + 1] == B.B_SYSTEM
    assert "[c2]" not in argv[argv.index("-p") + 1]  # 被排除的候選不給 B（與 C 同一份）


@pytest.mark.parametrize("reply", ['{"selected": ["c9"]}', '{"selected": ["c1","c2","c3","c4"]}', "no json", '{"x": 1}'])
def test_b_rejects_invalid_replies(reply):
    with pytest.raises(B.BenchError):
        B.parse_b_reply(reply, ["c1", "c2", "c3", "c4"])


def test_a_is_frozen_top3_and_runner_resumes(tmp_path):
    frozen = _frozen(_task("t1"), _task("t2", n=2))
    out = tmp_path / "r.jsonl"
    assert B.run(frozen, ["t1", "t2"], ["A"], out) == {"written": 2, "errors": 0}
    assert [r["selected"] for r in B._load_records(out)] == [["c1", "c2", "c3"], ["c1", "c2"]]
    assert B.run(frozen, ["t1", "t2"], ["A"], out) == {"written": 0, "errors": 0}


def _gold(frozen, relevant):
    return {f"{t['task_id']}#c{c['rank']}": ("relevant" if (t["task_id"], c["rank"]) in relevant else "irrelevant")
            for t in frozen["tasks"] for c in t["candidates"]}


def _rec(arm, task_id, selected, wall=0, cost="0", egress=None, repeat=0):
    return {"arm": arm, "task_id": task_id, "repeat": repeat, "selected": selected, "wall_ms": wall,
            "cost_usd": cost, "egress": egress, "error": None}


def test_score_hand_computed_and_decide_go():
    frozen = _frozen(_task("t1"), _task("t2"), _task("t3", excluded=(5,)))
    gold = _gold(frozen, {("t1", 2), ("t1", 4), ("t2", 1)})  # t3 沒有相關 → 測 abstain
    records = [
        _rec("A", "t1", ["c1", "c2", "c3"]), _rec("A", "t2", ["c1", "c2", "c3"]), _rec("A", "t3", ["c1", "c2", "c3"]),
        _rec("B", "t1", ["c2", "c4"], 8000, "0.01"), _rec("B", "t2", ["c1"], 7000, "0.01"), _rec("B", "t3", [], 9000, "0.01"),
        _rec("C", "t1", ["c2", "c4"], 300, "0.00004", "sent"), _rec("C", "t2", ["c1"], 400, "0.00004", "sent"),
        _rec("C", "t3", [], 350, "0.00004", "sent"),
    ]
    summary = B.score(frozen, gold, ["t1", "t2", "t3"], records)
    a, c = summary["arms"]["A"], summary["arms"]["C"]
    assert a["precision_at_3"] == round(2 / 9, 6) and a["irrelevant_per_task"] == round(7 / 3, 6)
    assert a["task_hit_rate"] == 1.0 and a["correct_abstain_rate"] == 0.0
    assert c["precision_at_3"] == 1.0 and c["irrelevant_per_task"] == 0.0 and c["correct_abstain_rate"] == 1.0
    assert c["median_latency_ms"] == 350 and summary["privacy"]["egress_coverage"] == round(14 / 15, 6)
    assert summary["privacy"]["excluded_relevant_ratio"] == 0.0
    decision = B.decide(summary)
    assert decision["decision"] == "go", decision["reasons"]


def test_decide_no_go_when_c_misses_and_privacy_fails():
    frozen = _frozen(_task("t1", excluded=(1, 2, 3)), _task("t2"))
    gold = _gold(frozen, {("t1", 1), ("t2", 1)})
    records = [_rec("A", "t1", ["c1", "c2", "c3"]), _rec("A", "t2", ["c1", "c2", "c3"]),
               _rec("B", "t1", ["c4"], 8000, "0.01"), _rec("B", "t2", ["c1"], 8000, "0.01"),
               _rec("C", "t1", ["c4"], 300, "0.00004", "sent"), _rec("C", "t2", ["c3"], 300, "0.00004", "sent")]
    decision = B.decide(B.score(frozen, gold, ["t1", "t2"], records))
    assert decision["decision"] == "no-go"
    assert not decision["gates"]["privacy_excluded_relevant"] and not decision["gates"]["c_vs_a_hit_rate"]


def test_stability_counts_identical_selections():
    records = [_rec("C", "t1", ["c1"]), _rec("C", "t1", ["c1"], repeat=1), _rec("C", "t2", ["c1"]),
               _rec("C", "t2", ["c2"], repeat=1)]
    assert B._stability(records, "C") == {"pairs": 2, "identical_selection": 1}
    assert len(B.stability_tasks([f"t{i}" for i in range(20)])) == 8


def test_cli_score(tmp_path, capsys):
    frozen = _frozen(_task("t1"))
    (tmp_path / "f.json").write_text(json.dumps(frozen))
    gold = {"pairs": {k: {"gold": v} for k, v in _gold(frozen, {("t1", 1)}).items()}}
    (tmp_path / "g.json").write_text(json.dumps(gold))
    (tmp_path / "s.json").write_text(json.dumps({"dev": ["t1"], "hidden": ["t1"]}))
    recs = [_rec("A", "t1", ["c1", "c2", "c3"]), _rec("B", "t1", ["c1"], 5000, "0.01"),
            _rec("C", "t1", ["c1"], 300, "0.0001", "sent")]
    (tmp_path / "r.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    code = cli.main(["h2", "score", "--frozen", str(tmp_path / "f.json"), "--gold", str(tmp_path / "g.json"),
                     "--split-file", str(tmp_path / "s.json"), "--split", "hidden", "--records", str(tmp_path / "r.jsonl")])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["arms"]["C"]["precision_at_3"] == 1.0


# ---------------------------------------------------------------- v4.1

FILL = B.FILL_REPO


def _rtask(task_id, repo=FILL, n=3):
    task = _task(task_id, n=n)
    task["repo"] = repo
    return task


def test_c_revision_selects_criteria_and_is_recorded(tmp_path):
    task = _task("t1")
    request = B.build_c_request(task, "rev2")
    assert request["questions"]["c1"]["criteria"] == B.C_CRITERIA_REV2
    assert "not enough" in request["questions"]["c1"]["criteria"]["no"]
    assert B.build_c_request(task)["questions"]["c1"]["criteria"] == B.C_CRITERIA  # 預設仍是 v4 的 rev1
    with pytest.raises(B.BenchError):
        B.build_c_request(task, "rev9")
    body = {"model": "jev-1.13.0", "answers": _yes_answers(request, {"c1"}), "usage": {"input_tokens": 10}}
    B.run(_frozen(task), ["t1"], ["C"], tmp_path / "r.jsonl", jev=_jev([(200, body, {})]), c_revision="rev2")
    assert B._load_records(tmp_path / "r.jsonl")[0]["details"]["c_revision"] == "rev2"
    assert B.PROTOCOLS["v4.1"]["c_revision"] == "rev2" and B.PROTOCOLS["v4"]["c_revision"] == "rev1"
    rev3 = B.build_c_request(task, "rev3")["questions"]["c1"]["criteria"]
    assert rev3 == B.C_CRITERIA_REV3 and "test expectation" in rev3["yes"] and "execution records" in rev3["no"]


def test_task_strata_and_unresolved_not_counted_in_precision():
    frozen = _frozen(_task("t1"), _task("t2"), _task("t3"))
    gold = _gold(frozen, {("t1", 1)})
    gold["t3#c1"] = "unresolved"
    assert B.task_strata(frozen, gold) == {"t1": "non-empty", "t2": "empty", "t3": "indeterminate"}
    metrics = B._arm_metrics(frozen, gold, ["t1", "t3"], {"t1": _rec("C", "t1", ["c1"]), "t3": _rec("C", "t3", ["c1"])})
    assert metrics["precision_at_3"] == 1.0 and metrics["counts"]["unresolved"] == 1


def test_stratified_split_prefers_non_fill_for_hidden_and_is_deterministic():
    other = "github.com/example/widget"
    tasks = [_rtask(f"e{i}", other if i < 2 else FILL) for i in range(6)]
    tasks += [_rtask(f"n{i}", other if i < 1 else FILL) for i in range(8)]
    tasks.append(_rtask("x0"))
    tasks.append(_task("u0", egress_task="excluded"))  # C 送不出去：不能白拿 empty 層的一題
    frozen = _frozen(*tasks)
    gold = _gold(frozen, {(f"n{i}", 1) for i in range(8)})
    gold["x0#c2"] = "unresolved"
    kwargs = {"hidden_empty": 3, "hidden_nonempty": 3, "dev_total": 4, "dev_empty": 1}
    split = B.stratified_split(frozen, gold, "seed-a", **kwargs)
    assert {"e0", "e1", "n0"} <= set(split["hidden"]) and len(split["hidden"]) == 6
    assert len(split["dev"]) == 4 and not set(split["dev"]) & set(split["hidden"])
    assert sum(t.startswith("e") for t in split["dev"]) == 1
    assert not {"x0", "u0"} & set(split["dev"] + split["hidden"])
    assert split["counts"] == {"empty": 6, "non-empty": 8, "indeterminate": 1, "not-sendable": 1,
                               "hidden_non_cortex": 3}
    assert split["generalization_scope"] == "cortex-dominant-public-engineering"
    assert B.stratified_split(frozen, gold, "seed-a", **kwargs) == split
    with pytest.raises(ValueError, match="empty"):
        B.stratified_split(frozen, gold, "seed-a", **{**kwargs, "hidden_empty": 6})


def _v41_case():
    frozen = _frozen(_task("n1"), _task("n2"), _task("e1"), _task("e2"))
    gold = _gold(frozen, {("n1", 2), ("n2", 1), ("n2", 3)})
    records = [
        *(_rec("A", t, ["c1", "c2", "c3"]) for t in ("n1", "n2", "e1", "e2")),
        _rec("B", "n1", ["c2"], 9000, "0.03"), _rec("B", "n2", ["c1"], 9000, "0.03"),
        _rec("B", "e1", [], 9000, "0.03"), _rec("B", "e2", ["c1"], 9000, "0.03"),
        _rec("C", "n1", ["c2"], 300, "0.0002", "sent"), _rec("C", "n2", ["c1", "c3"], 300, "0.0002", "sent"),
        _rec("C", "e1", [], 300, "0.0002", "sent"), _rec("C", "e2", [], 300, "0.0002", "sent"),
        _rec("C", "n2", ["c3"], 300, "0.0002", "sent", repeat=1), _rec("C", "e1", [], 300, "0.0002", "sent", repeat=1),
    ]
    return frozen, gold, records


def test_score_v41_hand_computed_and_decide_go():
    frozen, gold, records = _v41_case()
    summary = B.score_v41(frozen, gold, ["n1", "n2", "e1", "e2"], records)
    a, c = summary["arms"]["A"], summary["arms"]["C"]
    assert summary["strata"] == {"non-empty": 2, "empty": 2, "indeterminate": []}
    assert a["non-empty"]["precision_at_3"] == 0.5 and a["non-empty"]["irrelevant_per_task"] == 1.5
    assert c["non-empty"]["precision_at_3"] == 1.0 and c["empty"]["correct_abstain_rate"] == 1.0
    assert summary["stability"]["C"] == {"pairs": 2, "quality_stable": 2, "identical_selection": 1}
    decision = B.decide_v41(summary, {**B.GO_THRESHOLDS_V41, "quality_stable_min": 2})
    assert decision["decision"] == "go", decision["reasons"]
    assert "c_vs_b_precision" not in decision["gates"]  # B 只報告


def test_decide_v41_no_go_on_abstain_stability_and_indeterminate():
    frozen, gold, records = _v41_case()
    records = [r for r in records if not (r["arm"] == "C" and r["task_id"] == "e2")]
    records.append(_rec("C", "e2", ["c1"], 300, "0.0002", "sent"))
    records = [r for r in records if not (r["arm"] == "C" and r["repeat"] == 1 and r["task_id"] == "n2")]
    records.append(_rec("C", "n2", ["c2"], 300, "0.0002", "sent", repeat=1))  # 命中掉成 0、不相關增加
    gold["e1#c1"] = "unresolved"
    summary = B.score_v41(frozen, gold, ["n1", "n2", "e1", "e2"], records)
    decision = B.decide_v41(summary, {**B.GO_THRESHOLDS_V41, "quality_stable_min": 2})
    assert decision["decision"] == "no-go"
    assert not decision["gates"]["empty_abstain"] and not decision["gates"]["quality_stable"]
    assert any("無法分層" in r for r in decision["reasons"])


def test_cli_split_and_score_v41(tmp_path, capsys):
    tasks = [_rtask(f"e{i}") for i in range(23)] + [_rtask(f"n{i}") for i in range(29)]
    frozen = _frozen(*tasks)
    (tmp_path / "f.json").write_text(json.dumps(frozen))
    gold = {"pairs": {k: {"gold": v} for k, v in _gold(frozen, {(f"n{i}", 1) for i in range(29)}).items()}}
    (tmp_path / "g.json").write_text(json.dumps(gold))
    assert cli.main(["h2", "split", "--frozen", str(tmp_path / "f.json"), "--gold", str(tmp_path / "g.json"),
                     "--seed", "s1", "--out", str(tmp_path / "s.json")]) == 0
    split = json.loads((tmp_path / "s.json").read_text())
    assert len(split["hidden"]) == 40 and len(split["dev"]) == 12 and split["seed"] == "s1"
    recs = [_rec(arm, t, ["c1"] if t.startswith("n") else [], 300, "0.0002", "sent") for arm in "ABC"
            for t in split["hidden"]]
    (tmp_path / "r.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    capsys.readouterr()
    assert cli.main(["h2", "score", "--protocol", "v4.1", "--frozen", str(tmp_path / "f.json"), "--gold",
                     str(tmp_path / "g.json"), "--split-file", str(tmp_path / "s.json"), "--split", "hidden",
                     "--records", str(tmp_path / "r.jsonl")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"]["protocol"] == "v4.1" and out["summary"]["strata"]["empty"] == 20
    # A 與 C 選得一樣：P@3 沒有比 A 高 20pp；兩者都沒有不相關，「≤ A 的 50%」依字面成立；沒有重跑紀錄。
    assert out["decision"]["reasons"] == ["未過：nonempty_precision_vs_a", "未過：quality_stable"]
