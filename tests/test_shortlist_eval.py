# tests/test_shortlist_eval.py — #158（#148 H1）BM25 push shortlist 基準線評估。
#
# 凍結 query 集（格式見 docs/push-shadow-baseline.md）＋確定性收窄參數 → Precision@3、
# Noise@3、Relevant-missed@12、注入字元量。對固定輸入必須產生可重現（逐位元相同）的輸出。
# 本檔只用合成 fixture，不含任何真實 prompt 或記憶內容。
import copy
import json
from pathlib import Path

import pytest

from paulsha_hippo import cli
from paulsha_hippo import shortlist_eval as E
from paulsha_hippo.moc import search as S

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "shortlist_eval" / "synthetic_frozen_queries.json"


def _fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict, name: str = "frozen.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 載入與驗證
# ---------------------------------------------------------------------------

def test_load_fixture_resolves_per_query_annotator_and_method():
    fs = E.load_frozen_set(FIXTURE)
    assert fs.name == "synthetic-sample-v1"
    assert fs.block_overhead_chars == 200
    assert [q.id for q in fs.queries] == ["q01", "q02", "q03", "q04"]
    assert fs.queries[0].annotator == "synthetic-fixture"
    assert fs.queries[2].annotator == "synthetic-fixture-second-pass"
    assert fs.queries[2].method.startswith("覆寫示範")
    assert [c.note_id for c in fs.queries[0].candidates][:2] == ["sl-0000000000000101", "sl-0000000000000102"]
    assert fs.queries[3].candidates == ()


def _mutated(mutate) -> dict:
    data = copy.deepcopy(_fixture_data())
    mutate(data)
    return data


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d.update(format="something-else"), "format"),
    (lambda d: d.update(version=2), "version"),
    (lambda d: d.update(queries=[]), "queries"),
    (lambda d: d["queries"][0]["candidates"][0].update(relevant=None), "unlabeled"),
    (lambda d: d["queries"][0]["candidates"][0].update(relevant="yes"), "relevant"),
    (lambda d: d["queries"][0]["candidates"][0].update(bm25=True), "bm25"),
    (lambda d: d["queries"][0]["candidates"][0].update(bm25="NaN"), "bm25"),
    (lambda d: d["queries"][0]["candidates"][0].update(chars=-1), "chars"),
    (lambda d: d["queries"][0]["candidates"][1].update(note_id="sl-0000000000000101"), "duplicate"),
    (lambda d: d["queries"][1].update(id="q01"), "duplicate"),
    (lambda d: d["queries"][0].update(query=""), "query"),
    (lambda d: d["queries"][0].update(candidates=[
        {"note_id": f"sl-{i:016d}", "bm25": -1.0, "relevant": False, "chars": 10} for i in range(13)]), "12"),
    (lambda d: (d.pop("annotator"),), "annotator"),
    (lambda d: (d.pop("method"),), "method"),
    (lambda d: d.update(block_overhead_chars=-5), "block_overhead_chars"),
])
def test_invalid_frozen_sets_are_rejected(tmp_path, mutate, needle):
    with pytest.raises(E.FrozenSetError) as excinfo:
        E.load_frozen_set(_write(tmp_path, _mutated(mutate)))
    assert needle in str(excinfo.value)


def test_unreadable_or_non_json_file_is_rejected(tmp_path):
    with pytest.raises(E.FrozenSetError):
        E.load_frozen_set(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(E.FrozenSetError):
        E.load_frozen_set(bad)


# ---------------------------------------------------------------------------
# 指標（golden：由 fixture 手算）
# ---------------------------------------------------------------------------

BASELINE = {
    "queries": 4, "injected_notes": 8, "relevant_injected": 3, "noise_injected": 5,
    "relevant_in_top12": 4, "relevant_missed": 1, "empty_injections": 1, "injected_chars": 1420,
    "precision_at_3": 0.375, "noise_at_3": 1.25, "relevant_missed_at_12": 0.25,
    "injected_chars_mean": 355.0,
}


def test_baseline_metrics_golden():
    fs = E.load_frozen_set(FIXTURE)
    report = E.evaluate(fs, min_score=4.0, max_k=3)
    assert report["baseline"] == BASELINE


def test_narrowed_metrics_golden_threshold_4():
    fs = E.load_frozen_set(FIXTURE)
    report = E.evaluate(fs, min_score=4.0, max_k=3)
    assert report["narrowed"] == {
        "queries": 4, "injected_notes": 4, "relevant_injected": 3, "noise_injected": 1,
        "relevant_in_top12": 4, "relevant_missed": 1, "empty_injections": 2, "injected_chars": 835,
        "precision_at_3": 0.75, "noise_at_3": 0.25, "relevant_missed_at_12": 0.25,
        "injected_chars_mean": 208.75,
    }
    assert report["delta"] == {"injected_chars": -585, "noise_injected": -4, "relevant_missed": 0,
                               "injected_notes": -4}


def test_narrowed_metrics_golden_default_params_top1():
    fs = E.load_frozen_set(FIXTURE)
    report = E.evaluate(fs, min_score=E.DEFAULT_MIN_SCORE, max_k=E.DEFAULT_MAX_K)
    n = report["narrowed"]
    assert (n["injected_notes"], n["relevant_injected"], n["noise_injected"]) == (3, 2, 1)
    assert (n["relevant_missed"], n["empty_injections"], n["injected_chars"]) == (2, 1, 930)
    assert n["precision_at_3"] == 0.6667
    assert n["relevant_missed_at_12"] == 0.5 and n["injected_chars_mean"] == 232.5


def test_per_query_breakdown():
    fs = E.load_frozen_set(FIXTURE)
    rows = {r["id"]: r for r in E.evaluate(fs, min_score=4.0, max_k=3)["per_query"]}
    assert rows["q01"]["baseline"] == ["sl-0000000000000101", "sl-0000000000000102", "sl-0000000000000103"]
    assert rows["q01"]["narrowed"] == ["sl-0000000000000101", "sl-0000000000000102"]
    assert rows["q01"]["relevant_missed"] == ["sl-0000000000000104"]
    assert rows["q03"]["narrowed"] == [] and rows["q03"]["narrowed_chars"] == 0
    assert rows["q04"]["baseline_chars"] == 0


def test_precision_is_null_when_nothing_injected():
    fs = E.load_frozen_set(FIXTURE)
    n = E.evaluate(fs, min_score=100.0, max_k=3)["narrowed"]
    assert n["injected_notes"] == 0 and n["precision_at_3"] is None
    assert n["injected_chars"] == 0 and n["relevant_missed_at_12"] == 1.0


def test_sweep_auto_grid_and_recommended_threshold():
    fs = E.load_frozen_set(FIXTURE)
    sweep = E.sweep(fs, E.auto_thresholds(fs), max_k=3)
    assert [r["bm25_min_score"] for r in sweep["rows"]] == [0.0, 0.8, 1.0, 1.5, 2.5, 4.0, 4.5, 5.0, 6.0]
    by = {r["bm25_min_score"]: r for r in sweep["rows"]}
    assert by[0.0]["injected_chars"] == 1420 and by[0.0]["extra_missed"] == 0
    assert by[1.0]["injected_chars"] == 1325
    assert by[4.5]["injected_chars"] == 735 and by[4.5]["extra_missed"] == 0
    assert by[5.0]["extra_missed"] == 1
    assert by[6.0]["relevant_missed"] == 3
    assert sweep["recommended_bm25_min_score"] == 4.5
    assert sweep["max_k"] == 3


def test_sweep_recommendation_is_none_when_every_threshold_adds_misses():
    fs = E.load_frozen_set(FIXTURE)
    assert E.sweep(fs, [5.0, 6.0], max_k=3)["recommended_bm25_min_score"] is None


# ---------------------------------------------------------------------------
# CLI：hippo shortlist eval
# ---------------------------------------------------------------------------

def test_cli_eval_json_is_reproducible_bytes(capsys):
    argv = ["shortlist", "eval", "--queries", str(FIXTURE), "--min-score", "4.0", "--max-k", "3",
            "--sweep", "auto", "--json"]
    assert cli.main(argv) == 0
    first = capsys.readouterr().out
    assert cli.main(argv) == 0
    second = capsys.readouterr().out
    assert first == second
    payload = json.loads(first)
    assert payload["format"] == "hippo-shortlist-eval"
    assert payload["baseline"] == BASELINE
    assert payload["params"] == {"baseline_k": 3, "bm25_min_score": 4.0, "max_k": 3, "fetch_k": 12}
    assert payload["frozen_set"]["name"] == "synthetic-sample-v1"
    assert payload["frozen_set"]["queries"] == 4
    assert len(payload["frozen_set"]["sha256"]) == 64
    assert payload["frozen_set"]["annotators"] == ["synthetic-fixture", "synthetic-fixture-second-pass"]
    assert payload["sweep"]["recommended_bm25_min_score"] == 4.5


def test_cli_eval_text_output_is_reproducible_and_labelled(capsys):
    argv = ["shortlist", "eval", "--queries", str(FIXTURE), "--min-score", "4", "--max-k", "3"]
    assert cli.main(argv) == 0
    first = capsys.readouterr().out
    assert cli.main(argv) == 0
    assert capsys.readouterr().out == first
    for token in ("Precision@3", "Noise@3", "Relevant-missed@12", "injected_chars",
                  "baseline", "narrowed", "0.3750", "0.7500", "1420", "835"):
        assert token in first


def test_cli_eval_defaults_use_module_defaults_not_live_config(capsys, tmp_path, monkeypatch):
    # 評估指令不讀 runtime config：即使 config 設了別的 push_shadow 參數，數字也不變。
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(
        "shortlist:\n  push_shadow:\n    enabled: true\n    bm25_min_score: 9\n    max_k: 3\n",
        encoding="utf-8")
    monkeypatch.setenv("HIPPO_CONFIG_ROOT", str(cfg))
    assert cli.main(["shortlist", "eval", "--queries", str(FIXTURE), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["params"]["bm25_min_score"] == E.DEFAULT_MIN_SCORE
    assert payload["params"]["max_k"] == E.DEFAULT_MAX_K
    assert payload["narrowed"]["injected_chars"] == 930


def test_cli_eval_invalid_frozen_set_exits_2(tmp_path, capsys):
    data = _mutated(lambda d: d["queries"][0]["candidates"][0].update(relevant=None))
    rc = cli.main(["shortlist", "eval", "--queries", str(_write(tmp_path, data))])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "unlabeled" in captured.err


@pytest.mark.parametrize("extra", [
    ["--max-k", "4"], ["--max-k", "-1"], ["--min-score", "-0.5"], ["--min-score", "nan"],
    ["--sweep", "1,abc"], ["--sweep", ""],
])
def test_cli_eval_rejects_bad_params(extra, capsys):
    assert cli.main(["shortlist", "eval", "--queries", str(FIXTURE), *extra]) == 2


# ---------------------------------------------------------------------------
# CLI：hippo shortlist freeze（由真實索引產生待標註骨架；此處用合成 memory root）
# ---------------------------------------------------------------------------

def _seed(mr: Path) -> None:
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    notes = (
        ("a.md", "sl-aaaaaaaaaaaaaaaa", "SerialWrap", "SerialWrap 抽象 UART 執行層\n"),
        ("b.md", "sl-bbbbbbbbbbbbbbbb", "Console 重試", "SerialWrap console 重試策略\n"),
    )
    for fname, sid, title, body in notes:
        (k / fname).write_text(
            f"---\nmemory_layer: knowledge\nslice_id: {sid}\nproject: proj\n"
            f"title: {title}\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n{body}",
            encoding="utf-8")
    S.build_index(mr, link_weights={})


def _tree(root: Path) -> list[tuple[str, int]]:
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*") if p.is_file())


def test_cli_freeze_emits_unlabeled_skeleton_readonly(tmp_path, capsys):
    mr = tmp_path / "mr"
    _seed(mr)
    before = _tree(mr)
    qfile = tmp_path / "queries.txt"
    qfile.write_text("# 註解行略過\nSerialWrap UART\n\nzzzznomatch\n", encoding="utf-8")
    out = tmp_path / "frozen.json"
    rc = cli.main(["shortlist", "freeze", "--memory-root", str(mr), "--project", "proj",
                   "--queries-file", str(qfile), "--annotator", "tester", "--method", "合成測試",
                   "--name", "tmp-set", "--out", str(out)])
    assert rc == 0
    assert _tree(mr) == before  # 唯讀：不記 offered、不寫任何 runtime 檔
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format"] == E.FROZEN_FORMAT and data["version"] == E.FROZEN_VERSION
    assert data["name"] == "tmp-set" and data["project"] == "proj"
    assert data["annotator"] == "tester" and data["method"] == "合成測試"
    assert data["block_overhead_chars"] > 0
    assert [q["query"] for q in data["queries"]] == ["SerialWrap UART", "zzzznomatch"]
    first = data["queries"][0]
    ids = [c["note_id"] for c in first["candidates"]]
    assert ids and set(ids) <= {"sl-aaaaaaaaaaaaaaaa", "sl-bbbbbbbbbbbbbbbb"}
    for c in first["candidates"]:
        assert c["relevant"] is None and c["chars"] > 0 and c["bm25"] < 0 and c["title"]
    assert data["queries"][1]["candidates"] == []
    # 未標註 → eval 拒絕
    assert cli.main(["shortlist", "eval", "--queries", str(out)]) == 2
    capsys.readouterr()
    # 標註後 → 可評估
    for q in data["queries"]:
        for c in q["candidates"]:
            c["relevant"] = c["note_id"] == "sl-aaaaaaaaaaaaaaaa"
    labelled = _write(tmp_path, data, "labelled.json")
    assert cli.main(["shortlist", "eval", "--queries", str(labelled), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["baseline"]["queries"] == 2


def test_cli_freeze_candidate_chars_match_hook_row_lengths(tmp_path, monkeypatch):
    """freeze 算出的單列字元數＋overhead 必須等於 hook 實際注入同一批 note 的字元數。"""
    from paulsha_hippo.hooks import _shortlist_common as SC
    mr = tmp_path / "mr"
    _seed(mr)
    qfile = tmp_path / "q.txt"
    qfile.write_text("SerialWrap UART\n", encoding="utf-8")
    out = tmp_path / "f.json"
    assert cli.main(["shortlist", "freeze", "--memory-root", str(mr), "--project", "proj",
                     "--queries-file", str(qfile), "--annotator", "t", "--method", "m",
                     "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    cands = data["queries"][0]["candidates"]
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    injected = SC.build_shortlist_and_record(mr, "claude-code", E.FREEZE_PLACEHOLDER_SESSION,
                                             cwd="/x", prompt="SerialWrap UART")
    expected = data["block_overhead_chars"] + sum(c["chars"] for c in cands[:3])
    assert len(injected) == expected


def test_cli_freeze_missing_index_exits_1(tmp_path, capsys):
    qfile = tmp_path / "q.txt"
    qfile.write_text("anything\n", encoding="utf-8")
    rc = cli.main(["shortlist", "freeze", "--memory-root", str(tmp_path / "empty"), "--project", "p",
                   "--queries-file", str(qfile), "--annotator", "t", "--method", "m"])
    assert rc == 1
    assert "index" in capsys.readouterr().err
