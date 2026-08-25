from paulsha_hippo import followups as fu

BODY = ("## Flash\n表格略。\n\nREADME-ARC.md 記載的舊 FLASH 數字 `133,604 B` 已與 fresh build 的 `133,372 B` 不符，"
        "需要更新（`README-ARC.md:108`）。\n\nNVS 未顯式保留，未來需加 linker reservation。\n")


def test_extract_target_and_expected_stale():
    items = fu.extract_followups(slice_id="sl-1", project="ot-ti-mirror", body=BODY, cites=[])
    assert len(items) == 2
    a, b = items
    assert a["target"] == {"path": "README-ARC.md", "line": 108} and a["expected_stale"] == "133,604 B"
    assert b["target"] is None and b["claim"].startswith("NVS 未顯式保留")
    assert a["id"].startswith("fu-") and len(a["id"]) == 19


def test_extract_is_deterministic_and_uses_cites_fallback():
    x = fu.extract_followups(slice_id="sl-1", project="p", body="需加 guard。\n", cites=[{"path": "a.py", "line": 3}])
    y = fu.extract_followups(slice_id="sl-1", project="p", body="需加 guard。\n", cites=[{"path": "a.py", "line": 3}])
    assert x == y and x[0]["target"] == {"path": "a.py", "line": 3}


def test_extract_all_opens_once(tmp_path):
    k = tmp_path / "knowledge" / "ot-ti-mirror"; k.mkdir(parents=True)
    (k / "n--sl-1.md").write_text("---\nslice_id: sl-1\nmemory_layer: knowledge\nproject: ot-ti-mirror\n---\n" + BODY, encoding="utf-8")
    s1 = fu.extract_all(tmp_path, apply=True, now="2026-08-25T00:00:00Z")
    s2 = fu.extract_all(tmp_path, apply=True, now="2026-08-25T00:01:00Z")
    assert s1["opened"] == 2 and s2["opened"] == 0
    assert len((tmp_path / "runtime" / "ledger" / "followups.jsonl").read_text().splitlines()) == 2
    assert fu.open_count(tmp_path, "ot-ti-mirror") == 2
