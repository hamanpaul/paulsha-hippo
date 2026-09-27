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
# CLI：hippo shortlist freeze（由既有索引產生待標註骨架；此處用合成 memory root）
#
# freeze 會讀 runtime config（read_hint、collapse_same_topic）以重現 hook 的實際路徑。
# 這裡一律用 repo 既有的 isolated_atomizer_config 顯式隔離 HIPPO_CONFIG_ROOT（不只依賴
# conftest 的 autouse fixture；見 paulsha-hippo#141 事故），並依測試需要改寫隔離後的 config。
# ---------------------------------------------------------------------------

try:
    from atomizer_config_testutil import isolated_atomizer_config
except ImportError:  # pragma: no cover - only hit under `-m unittest tests.x` from repo root
    from tests.atomizer_config_testutil import isolated_atomizer_config

_BASE_NOTES = (
    ("a.md", "sl-aaaaaaaaaaaaaaaa", "SerialWrap", "SerialWrap 抽象 UART 執行層", "2026-06-29T00:00:00Z"),
    ("b.md", "sl-bbbbbbbbbbbbbbbb", "Console 重試", "SerialWrap console 重試策略", "2026-06-29T00:00:00Z"),
)


def _seed(mr: Path, notes=_BASE_NOTES) -> None:
    k = mr / "knowledge" / "proj"
    k.mkdir(parents=True)
    for fname, sid, title, body, captured in notes:
        (k / fname).write_text(
            f"---\nmemory_layer: knowledge\nslice_id: {sid}\nproject: proj\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\ncaptured_at: '{captured}'\n---\n{body}\n",
            encoding="utf-8")
    S.build_index(mr, link_weights={})


def _tree(root: Path) -> list[tuple[str, int]]:
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*") if p.is_file())


def _freeze(mr: Path, tmp_path: Path, queries: str, name: str = "f.json") -> dict:
    qfile = tmp_path / f"{name}.txt"
    qfile.write_text(queries, encoding="utf-8")
    out = tmp_path / name
    assert cli.main(["shortlist", "freeze", "--memory-root", str(mr), "--project", "proj",
                     "--queries-file", str(qfile), "--annotator", "t", "--method", "m",
                     "--out", str(out)]) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _hook_injection(mr: Path, monkeypatch, prompt: str) -> tuple[str, list[str]]:
    """以全新 session 跑一次真實 prompt-time 管線，回傳（注入字串, 注入的 sl_id 依序）。"""
    from paulsha_hippo.hooks import _shortlist_common as SC
    monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
    injected = SC.build_shortlist_and_record(mr, "claude-code", E.FREEZE_PLACEHOLDER_SESSION,
                                             cwd="/x", prompt=prompt)
    ev = json.loads((mr / "runtime" / "ledger" / "offered.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])
    return injected, [o["sl_id"] for o in ev["offered"]]


def _assert_freeze_matches_hook(data: dict, injected: str, offered_ids: list[str]) -> None:
    cands = data["queries"][0]["candidates"]
    assert [c["note_id"] for c in cands[:E.BASELINE_K]] == offered_ids
    assert len(injected) == data["block_overhead_chars"] + sum(c["chars"] for c in cands[:E.BASELINE_K])


def test_cli_freeze_emits_unlabeled_skeleton_readonly(tmp_path, capsys):
    with isolated_atomizer_config():
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
    with isolated_atomizer_config():
        mr = tmp_path / "mr"
        _seed(mr)
        data = _freeze(mr, tmp_path, "SerialWrap UART\n")
        injected, offered_ids = _hook_injection(mr, monkeypatch, "SerialWrap UART")
        _assert_freeze_matches_hook(data, injected, offered_ids)


# 審查 finding 1：baseline 必須走 hook 的同一條路徑（含同主題折疊與 claim），不是 raw search()。
_COLLAPSE_NOTES = (
    ("old.md", "sl-0101010101010101", "SerialWrap UART 設定", "SerialWrap UART 舊版設定 SerialWrap UART",
     "2026-06-01T00:00:00Z"),
    ("new.md", "sl-0202020202020202", "SerialWrap UART 設定", "SerialWrap UART 新版設定",
     "2026-07-01T00:00:00Z"),
    ("c.md", "sl-0303030303030303", "Console 重試", "SerialWrap console 重試", "2026-06-15T00:00:00Z"),
    ("d.md", "sl-0404040404040404", "Log 格式", "UART log 格式", "2026-06-15T00:00:00Z"),
)


def test_cli_freeze_baseline_follows_hook_collapse_and_claim(tmp_path, monkeypatch):
    with isolated_atomizer_config():  # 模板預設 collapse_same_topic: true
        mr = tmp_path / "mr"
        _seed(mr, _COLLAPSE_NOTES)
        raw = S.search(mr, "SerialWrap UART", project="proj", limit=E.FETCH_K, include_decayed=False)
        raw_ids = [h["slice_id"] for h in raw]
        assert {"sl-0101010101010101", "sl-0202020202020202"} <= set(raw_ids[:3])  # raw top-3 含同主題兩則
        data = _freeze(mr, tmp_path, "SerialWrap UART\n")
        q = data["queries"][0]
        ids = [c["note_id"] for c in q["candidates"]]
        assert "sl-0101010101010101" not in ids  # 舊的同主題 note 已被折疊，不再是候選
        assert q["collapsed"] == {"sl-0202020202020202": ["sl-0101010101010101"]}
        injected, offered_ids = _hook_injection(mr, monkeypatch, "SerialWrap UART")
        _assert_freeze_matches_hook(data, injected, offered_ids)


def test_cli_freeze_respects_collapse_flag_off(tmp_path, monkeypatch):
    with isolated_atomizer_config() as cfg:
        text = cfg.read_text(encoding="utf-8")
        text = text.replace("collapse_same_topic: true", "collapse_same_topic: false")
        text = text.replace("read_hint: show", "read_hint: read")
        cfg.write_text(text, encoding="utf-8")
        mr = tmp_path / "mr"
        _seed(mr, _COLLAPSE_NOTES)
        data = _freeze(mr, tmp_path, "SerialWrap UART\n")
        q = data["queries"][0]
        assert {"sl-0101010101010101", "sl-0202020202020202"} <= {c["note_id"] for c in q["candidates"]}
        assert q["collapsed"] == {}
        injected, offered_ids = _hook_injection(mr, monkeypatch, "SerialWrap UART")
        assert "Read" in injected and "show --memory-root" not in injected  # 同一份隔離 config 的 read_hint
        _assert_freeze_matches_hook(data, injected, offered_ids)


# 審查 finding 2：title／summary 含換行（或被 redaction 整行替換）時，每列字元數必須結構化對齊。
def _secret_like() -> str:
    return "sk-" + "Q" * 24  # 執行期組字串：只為觸發 redaction，檔案內不留憑證樣式字面值


def test_cli_freeze_chars_structurally_aligned_with_multiline_title(tmp_path, monkeypatch):
    notes = (
        ("m.md", "sl-0505050505050505", "SerialWrap\nUART 多行標題", "SerialWrap UART 多行 SerialWrap UART",
         "2026-06-29T00:00:00Z"),
        ("r.md", "sl-0606060606060606", f"SerialWrap UART {_secret_like()}", "SerialWrap UART 會被遮蔽",
         "2026-06-29T00:00:00Z"),
        ("p.md", "sl-0707070707070707", "Plain", "SerialWrap 普通", "2026-06-29T00:00:00Z"),
    )
    with isolated_atomizer_config():
        mr = tmp_path / "mr"
        _seed(mr, notes)
        data = _freeze(mr, tmp_path, "SerialWrap UART\n")
        injected, offered_ids = _hook_injection(mr, monkeypatch, "SerialWrap UART")
        assert "\nUART 多行標題" in injected and "[REDACTED LINE:" in injected
        cands = {c["note_id"]: c for c in data["queries"][0]["candidates"]}
        assert set(cands) == {"sl-0505050505050505", "sl-0606060606060606", "sl-0707070707070707"}
        rows = injected.split("\n")
        multi = next(i for i, line in enumerate(rows) if line.startswith("- [SerialWrap"))
        # 多行標題那則佔兩行：字元數＝兩段內容＋兩個換行
        assert cands["sl-0505050505050505"]["chars"] == len(rows[multi]) + len(rows[multi + 1]) + 2
        redacted = next(line for line in rows if line.startswith("[REDACTED LINE:"))
        assert cands["sl-0606060606060606"]["chars"] == len(redacted) + 1
        _assert_freeze_matches_hook(data, injected, offered_ids)


def test_push_shadow_chars_after_exact_with_multiline_title(tmp_path, monkeypatch):
    """線上 shadow 同樣結構化對齊：多行標題時 chars_after 仍精確（chars_exact 為 true）。"""
    from paulsha_hippo import push_shadow as PS
    from paulsha_hippo import runtime_flags as rf
    from paulsha_hippo.hooks import _shortlist_common as SC
    notes = (
        ("m.md", "sl-0505050505050505", "SerialWrap\nUART 多行標題", "SerialWrap UART 多行 SerialWrap UART",
         "2026-06-29T00:00:00Z"),
        ("p.md", "sl-0707070707070707", "Plain", "SerialWrap 普通", "2026-06-29T00:00:00Z"),
    )
    with isolated_atomizer_config():
        mr = tmp_path / "mr"
        _seed(mr, notes)
        monkeypatch.setattr(SC, "resolve_project", lambda cwd, memory_root: "proj")
        monkeypatch.setattr(SC, "load_flags", lambda: rf.HygieneFlags(
            push_shadow=PS.PushShadowConfig(enabled=True, max_k=1)))
        full = SC.build_shortlist_and_record(mr, "claude-code", "sidM", cwd="/x", prompt="SerialWrap UART")
        ev = json.loads(PS.ledger_path(mr).read_text(encoding="utf-8").splitlines()[0])
        assert ev["chars_exact"] is True and ev["chars_before"] == len(full)
        for sub in ("ledger", "wakeup"):
            import shutil
            shutil.rmtree(mr / "runtime" / sub, ignore_errors=True)
        monkeypatch.setattr(SC, "SHORTLIST_K", 1)
        monkeypatch.setattr(SC, "load_flags", lambda: rf.HygieneFlags())
        top1 = SC.build_shortlist_and_record(mr, "claude-code", "sidM", cwd="/x", prompt="SerialWrap UART")
        assert ev["chars_after"] == len(top1)


def test_cli_freeze_missing_index_exits_1(tmp_path, capsys):
    with isolated_atomizer_config():
        qfile = tmp_path / "q.txt"
        qfile.write_text("anything\n", encoding="utf-8")
        rc = cli.main(["shortlist", "freeze", "--memory-root", str(tmp_path / "empty"), "--project", "p",
                       "--queries-file", str(qfile), "--annotator", "t", "--method", "m"])
        assert rc == 1
        assert "index" in capsys.readouterr().err
