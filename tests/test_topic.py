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
