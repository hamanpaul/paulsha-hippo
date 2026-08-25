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


# --- review round 1 -----------------------------------------------------------------


def test_expected_stale_excludes_cite_locator_backtick_span():
    """cite locator 自己的反引號 span（`` `README-ARC.md:108` ``）先被排除，真正的過時值
    `` `133,604 B` `` 才是 expected_stale——不是單純的『這行最左邊那個反引號值』。
    """
    line = "`README-ARC.md:108` 記載的舊 FLASH 數字 `133,604 B` 已與 fresh build 不符，需要更新。\n"
    items = fu.extract_followups(slice_id="sl-1", project="p", body=line, cites=[])
    assert len(items) == 1
    assert items[0]["expected_stale"] == "133,604 B"


def test_expected_stale_prefers_backtick_over_earlier_bare_digit_run():
    """一行內較早出現、無關的裸數字（`12345`）不得搶走真正以反引號標示的過時值。"""
    line = "已有 12345 版本，舊值 `133,604 B` 需要更新。\n"
    items = fu.extract_followups(slice_id="sl-1", project="p", body=line, cites=[])
    assert len(items) == 1
    assert items[0]["expected_stale"] == "133,604 B"


def test_expected_stale_none_when_line_has_only_a_cite():
    """一行只有 cite locator、沒有任何過時值：expected_stale 必須是 None，不能誤把
    cite 自己的 path:line 當成過時值。
    """
    line = "詳見 `README-ARC.md:108`，需修正。\n"
    items = fu.extract_followups(slice_id="sl-1", project="p", body=line, cites=[])
    assert len(items) == 1
    assert items[0]["expected_stale"] is None


def test_extract_all_respects_disabled_followups_flag(tmp_path):
    """followups.enabled: false（canonical config，經 conftest 的 HIPPO_CONFIG_ROOT 隔離）：
    extract_all(apply=True) 不寫入 ledger，summary 標記 skipped——binding constraint 3：
    flag 要內建進 extract_all 本身（enabled=None 時 best-effort 讀 runtime_flags），
    不是只在 CLI 擋一次。
    """
    config_path = tmp_path / "hippo-config" / "config.yaml"
    config_path.write_text("followups:\n  enabled: false\n", encoding="utf-8")
    k = tmp_path / "knowledge" / "ot-ti-mirror"; k.mkdir(parents=True)
    (k / "n--sl-1.md").write_text(
        "---\nslice_id: sl-1\nmemory_layer: knowledge\nproject: ot-ti-mirror\n---\n" + BODY,
        encoding="utf-8")
    s = fu.extract_all(tmp_path, apply=True, now="2026-08-25T00:00:00Z")
    assert s["opened"] == 0
    assert s.get("skipped") == "followups.disabled"
    assert not (tmp_path / "runtime" / "ledger" / "followups.jsonl").exists()


def test_actionable_regex_is_case_insensitive():
    up = fu.extract_followups(slice_id="sl-1", project="p", body="Should be updated soon.\n", cites=[])
    low = fu.extract_followups(slice_id="sl-1", project="p", body="todo: revisit this later.\n", cites=[])
    assert len(up) == 1 and len(low) == 1


def test_actionable_inside_fenced_code_block_is_skipped():
    body = "```\nTODO: not actionable, this is example code\n```\n\nTODO: this one is real\n"
    items = fu.extract_followups(slice_id="sl-1", project="p", body=body, cites=[])
    assert len(items) == 1
    assert items[0]["claim"] == "TODO: this one is real"
