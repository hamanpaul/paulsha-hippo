import types

from paulsha_hippo import followups as fu

BODY = ("## Flash\n表格略。\n\nREADME-ARC.md 記載的舊 FLASH 數字 `133,604 B` 已與 fresh build 的 `133,372 B` 不符，"
        "需要更新（`README-ARC.md:108`）。\n\nNVS 未顯式保留，未來需加 linker reservation。\n")


def _seed_body(tmp_path, body=BODY, project="ot-ti-mirror", slice_id="sl-1"):
    k = tmp_path / "knowledge" / project
    k.mkdir(parents=True)
    (k / f"n--{slice_id}.md").write_text(
        f"---\nslice_id: {slice_id}\nmemory_layer: knowledge\nproject: {project}\n---\n" + body,
        encoding="utf-8")


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


# --- T11 leftover minor: _cite_in 相鄰行查找必須尊重圍籬遮罩 -------------------------


def test_cite_lookup_ignores_backtick_fence_marker_line():
    """圍籬 marker 行本身也視為圍籬內（`_fence_mask` 既有規則）；`_cite_in` 的相鄰行
    查找之前沒有遵守這條規則——marker 行若剛好長得像 cite locator（語言註記寫成
    ```stale.py:42``），會被誤當成真正引用。這裡的 marker 行緊鄰在可行動語句後一行。
    """
    body = "需要更新\n```stale.py:42\ncontent\n```\n"
    items = fu.extract_followups(slice_id="sl-1", project="p", body=body, cites=[])
    assert len(items) == 1
    assert items[0]["target"] is None


def test_cite_lookup_ignores_tilde_fence_marker_line():
    """同上，圍籬標記換成 `~~~`。"""
    body = "需要更新\n~~~stale.py:42\ncontent\n~~~\n"
    items = fu.extract_followups(slice_id="sl-1", project="p", body=body, cites=[])
    assert len(items) == 1
    assert items[0]["target"] is None


def test_cite_lookup_ignores_unclosed_fence_marker_line():
    """未閉合的圍籬（body 結尾前沒有對應的收尾 marker）：開頭 marker 行仍視為圍籬內
    （`_fence_mask` 對未閉合圍籬的既有語意——toggle 之後一路到 EOF 都算圍籬內），
    同樣不得被 `_cite_in` 當成 cite 來源。
    """
    body = "需要更新\n```leaked.py:7\n"
    items = fu.extract_followups(slice_id="sl-1", project="p", body=body, cites=[])
    assert len(items) == 1
    assert items[0]["target"] is None


# --- T11b: 遮蔽再落 ledger（memory-consumer 邊界，比照 hooks/_shortlist_common._redact）---


def test_extract_all_fails_closed_and_skips_item_when_check_boundary_raises(tmp_path, monkeypatch):
    """policy.check_boundary 炸掉：這筆 follow-up 整筆不落 ledger，summary 計數但不 raise。"""
    import paulsha_hippo.policy as pol

    def _boom(*a, **k):
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(pol, "check_boundary", _boom)
    _seed_body(tmp_path)
    s = fu.extract_all(tmp_path, apply=True, now="2026-08-25T00:00:00Z")
    assert s["opened"] == 0
    assert s["skipped_redaction"] == 2
    assert not (tmp_path / "runtime" / "ledger" / "followups.jsonl").exists()


def test_extract_all_stores_redacted_text_when_check_boundary_alters_it(tmp_path, monkeypatch):
    """policy.check_boundary 回傳的 .text 與原文不同 → ledger 存的必須是遮蔽後的文字。"""
    import paulsha_hippo.policy as pol

    def _fake(boundary, text, **kwargs):
        assert boundary == "external_to_raw"
        assert kwargs["project_slug"] == "ot-ti-mirror"
        assert kwargs["session_ref"] == "sl-1"
        return types.SimpleNamespace(text="[REDACTED]")

    monkeypatch.setattr(pol, "check_boundary", _fake)
    _seed_body(tmp_path)
    s = fu.extract_all(tmp_path, apply=True, now="2026-08-25T00:00:00Z")
    assert s["opened"] == 2
    st = fu.fold(tmp_path)
    assert {v["claim"] for v in st.values()} == {"[REDACTED]"}
    stales = {v.get("expected_stale") for v in st.values()}
    assert stales == {"[REDACTED]", None} or stales == {"[REDACTED]"}


def test_extract_all_real_default_policy_leaves_readme_fixture_unchanged(tmp_path):
    """真實預設 policy：README-ARC.md fixture 的 claim／expected_stale 不含機密樣式，
    走過 check_boundary 後文字必須維持不變（不能因為加了遮蔽就悄悄改壞既有輸出）。
    """
    _seed_body(tmp_path)
    s = fu.extract_all(tmp_path, apply=True, now="2026-08-25T00:00:00Z")
    assert s["opened"] == 2
    st = fu.fold(tmp_path)
    claims = {v["claim"] for v in st.values()}
    assert any("133,604 B" in c and "需要更新" in c for c in claims)
    stales = {v.get("expected_stale") for v in st.values()}
    assert "133,604 B" in stales


def test_actionable_todo_requires_word_boundary():
    """`TODO` 沒有 word boundary 時，`TODOS.md`／`todos`／`TodoList` 這種只是提到檔名或
    型別名的句子會被憑空抽成一張待辦單（regex 是純字串比對，抽錯就是開錯單）。
    """
    assert fu.extract_followups(slice_id="sl-1", project="p",
                                body="參考 TODOS.md 的清單即可。\n", cites=[]) == []
    assert fu.extract_followups(slice_id="sl-1", project="p",
                                body="TodoList 元件已經定版。\n", cites=[]) == []
    # 真正的待辦仍要抽得到（大小寫不敏感、冒號／空白分隔皆可）。
    items = fu.extract_followups(slice_id="sl-1", project="p",
                                 body="TODO: 補上 guard（`a.py:3`）。\n", cites=[])
    assert len(items) == 1 and items[0]["target"] == {"path": "a.py", "line": 3}
