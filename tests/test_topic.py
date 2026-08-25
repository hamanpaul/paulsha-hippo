from paulsha_hippo import topic

FAM = (("MCU-Octopus", "ot-ti-mirror"),)


def _n(sid, project, title, at, aliases=()):
    return {"slice_id": sid, "project": project, "title": title, "captured_at": at, "aliases": list(aliases)}


def test_exact_title_same_project():
    assert topic.is_same_topic(_n("a", "p", "CC2674P10 Flash 配置", "x"), _n("b", "p", "cc2674p10  flash 配置", "y"))


def test_alias_match():
    assert topic.is_same_topic(_n("a", "p", "X", "x", aliases=["雙目標 Flash Layout"]), _n("b", "p", "雙目標 Flash Layout", "y"))


def test_jaccard_boundary_and_short_title_guard():
    a = _n("a", "p", "dual profile build isolation contract", "x")
    b = _n("b", "p", "dual build profile isolation contract", "y")   # 5/5 token 交集
    assert topic.is_same_topic(a, b)
    c = _n("c", "p", "dual build profile isolation gate", "y")        # 4/6 = 0.66 → same
    assert topic.is_same_topic(a, c)
    d = _n("d", "p", "dual build profile", "y")                       # tokens < 4 → 只接受相等
    assert not topic.is_same_topic(a, d)
    e = _n("e", "p", "dual build profile isolation gate extra words", "y")  # 4/8 = 0.5 → not
    assert not topic.is_same_topic(a, e)


def test_project_prefix_stripped_and_family_bridges_projects():
    a = _n("a", "ot-ti-mirror", "ot-ti-mirror Flash Layout and Image Artifacts", "2026-08-17T00:00:00Z")
    b = _n("b", "MCU-Octopus", "雙目標 Flash Layout", "2026-08-21T00:00:00Z")
    assert not topic.is_same_topic(a, b)                       # 無 family：不同 project 永不同組
    assert not topic.is_same_topic(a, b, families=FAM)         # 有 family 但標題 Jaccard 不足 → 仍 False（review tier 才處理）
    assert topic.family_key("ot-ti-mirror", FAM) == topic.family_key("MCU-Octopus", FAM)


def test_collapse_keeps_newest_per_group():
    hits = [_n("old", "p", "Flash Layout", "2026-08-17T00:00:00Z"),
            _n("new", "p", "flash layout", "2026-08-21T00:00:00Z"),
            _n("other", "p", "Release Button DIO24", "2026-08-01T00:00:00Z")]
    kept, collapsed = topic.collapse_same_topic(hits)
    assert [h["slice_id"] for h in kept] == ["new", "other"]
    assert collapsed == {"new": ["old"]}


def test_malformed_captured_at_never_beats_valid_newer():
    # "N/A"／"yesterday" 字典序都大於 ISO 字串（例如 'y' > '2'），
    # 若仍用字串排序會被誤判為「最新」而搶下 owner 位置。
    hits = [_n("malformed_na", "p", "Flash Layout", "N/A"),
            _n("malformed_word", "p", "flash layout", "yesterday"),
            _n("valid", "p", "Flash layout", "2020-01-01T00:00:00Z")]
    kept, collapsed = topic.collapse_same_topic(hits)
    assert [h["slice_id"] for h in kept] == ["valid"]
    assert collapsed == {"valid": ["malformed_na", "malformed_word"]}


def test_two_malformed_captured_at_keep_input_order():
    hits = [_n("first", "p", "Alpha Beta Gamma Delta Epsilon", "N/A"),
            _n("second", "p", "Zeta Eta Theta Iota Kappa", "not-a-date")]
    kept, collapsed = topic.collapse_same_topic(hits)
    assert [h["slice_id"] for h in kept] == ["first", "second"]
    assert collapsed == {}


def test_z_suffix_and_offset_forms_compare_correctly():
    # "-05:00" 換算 UTC 後（2026-08-22T04:00:00）其實比 Z／naive 兩筆都新，
    # 但原始字串因日期欄位是 "21" 而在字典序上排最後——純字串排序會誤判成最舊。
    hits = [_n("offset", "p", "Flash Layout", "2026-08-21T23:00:00-05:00"),
            _n("z", "p", "flash layout", "2026-08-22T02:00:00Z"),
            _n("naive", "p", "flash Layout", "2026-08-22T01:00:00")]
    kept, collapsed = topic.collapse_same_topic(hits)
    assert [h["slice_id"] for h in kept] == ["offset"]
    assert collapsed == {"offset": ["z", "naive"]}
