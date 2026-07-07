# tests/test_retrieval.py
import sqlite3
from paulsha_hippo.retrieval import to_fts_query, format_shortlist


def test_to_fts_query_neutralizes_fts5_specials():
    q = to_fts_query('fix the "bug" (AND* core)')
    # must not raise when used as a MATCH query against an fts5 table
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute("INSERT INTO t VALUES ('fixing the bug in core')")
    rows = conn.execute("SELECT rowid FROM t WHERE t MATCH ?", [q]).fetchall()
    assert rows  # 'fix'/'bug'/'core' OR-match the row
    conn.close()


def test_to_fts_query_drops_short_latin_and_slashes():
    assert to_fts_query("/effort") == '"effort"'  # slash is not a token; 'effort' kept
    assert to_fts_query("a b c") == ""           # all 1-char latin dropped
    assert to_fts_query("") == ""


def test_to_fts_query_keeps_cjk_runs():
    q = to_fts_query("記憶系統 怎麼 retitle")
    assert '"記憶系統"' in q and '"retitle"' in q


def test_format_shortlist_lines():
    out = format_shortlist([
        {"title": "SerialWrap Exec", "summary": "抽象 UART 執行層", "path": "/m/knowledge/x/a.md"},
        {"title": "P4 Split", "summary": "實體 repo 拆分", "path": "/m/knowledge/x/b.md"},
    ])
    assert "/m/knowledge/x/a.md" in out and "/m/knowledge/x/b.md" in out
    assert "Read" in out  # contains the hint
    assert out.count("\n- ") == 2


def test_format_shortlist_empty_is_empty_string():
    assert format_shortlist([]) == ""


def test_to_fts_query_drops_stopwords():
    q = to_fts_query("how do I fix the login bug")
    assert '"fix"' in q and '"login"' in q and '"bug"' in q
    assert '"how"' not in q and '"the"' not in q and '"do"' not in q
    # a stopword-only prompt yields no query -> caller injects nothing
    assert to_fts_query("please help me with this") == ""
