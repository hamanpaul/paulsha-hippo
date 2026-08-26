from pathlib import Path
from paulsha_hippo import runtime_flags as rf


def test_defaults_when_file_missing(tmp_path):
    f = rf.load_flags(tmp_path / "nope.yaml")
    assert f == rf.HygieneFlags()


def test_reads_keys_and_falls_back_per_key(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("shortlist:\n  collapse_same_topic: false\n  read_hint: read\nfollowups:\n  enabled: 'nope'\nepisodic_filter: false\n",
                 encoding="utf-8")
    f = rf.load_flags(p)
    assert f.collapse_same_topic is False and f.read_hint == "read"
    assert f.followups_enabled is True      # 型別錯 → 預設
    # `episodic_filter` 刻意不在 HygieneFlags 上：atomizer 自己讀那個鍵
    # （AtomizerConfig.episodic_filter），這裡再存一份沒有讀取端。
    assert not hasattr(f, "episodic_filter")


def test_invalid_read_hint_falls_back(tmp_path):
    p = tmp_path / "config.yaml"; p.write_text("shortlist:\n  read_hint: whatever\n", encoding="utf-8")
    assert rf.load_flags(p).read_hint == "show"


def test_template_declares_flags():
    tpl = Path(rf.__file__).resolve().parent / "atomizer" / "atomizer.yaml"
    text = tpl.read_text(encoding="utf-8")
    for key in ("collapse_same_topic", "read_hint", "followups:", "episodic_filter"):
        assert key in text
