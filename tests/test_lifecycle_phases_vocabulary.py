"""共用 lifecycle 詞彙表的釘樁測試。

`PHASES` 是記憶平面與治理平面（paulsha-cortex）共用的詞彙聯集，靠「各自持有
相同常數 + 消費端對齊測試」達成一致，套件之間**不建立 import 依賴**。本檔釘住
本平面這一側的內容，cortex 側有對稱的 `tests/test_phases_constant.py`，最終相
等性由 paulshaclaw 的 `tests/test_cortex_alignment.py` 守。
"""

from paulsha_hippo.lib.lifecycle.schema import PHASES


def test_phases_is_the_shared_cross_plane_vocabulary():
    assert PHASES == (
        "claim",
        "research",
        "define",
        "plan",
        "build",
        "verify",
        "review",
        "ship",
    )


def test_research_stays_this_planes_entry_phase():
    """聯集不得改變本平面的起始階段——既有 slice 仍以 research 起。"""
    from paulsha_hippo.lib.lifecycle.template import build_lifecycle_template

    template = build_lifecycle_template(project="p", current_slice="s")

    assert template["current_phase"] == "research"
    assert set(template["gates"]) == set(PHASES)


def test_lifecycle_module_does_not_import_cortex():
    """零依賴底限：共用詞彙不得退化成跨套件 import。"""
    import inspect

    from paulsha_hippo.lib.lifecycle import schema

    assert "paulsha_cortex" not in inspect.getsource(schema)
