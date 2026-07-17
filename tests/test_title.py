from paulsha_hippo.importer import title


def test_generate_uses_runner_and_truncates_to_20():
    long = "這是一個非常長的標題會超過二十個中文字所以一定要被截斷對吧真的很長"
    out, source = title.generate_title(
        {"user_prompts": ["問題"], "assistant_summary": "答案"},
        runner=lambda text, cmd, timeout: long,
    )
    assert source == "gemma4"
    assert len(out) <= 20


def test_generate_falls_back_when_runner_raises():
    out, source = title.generate_title(
        {"user_prompts": ["幫我修 UART 升級流程很長很長很長很長很長很長很長"], "assistant_summary": "x"},
        runner=lambda text, cmd, timeout: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert source == "fallback"
    assert len(out) <= 20
    assert out.startswith("幫我修")


def test_default_runner_fails_fast_when_backend_unreachable(monkeypatch):
    import paulsha_hippo.importer.title as t

    monkeypatch.setattr(t, "_gemma4_reachable", lambda *a, **k: False)
    out, source = t.generate_title({"user_prompts": ["主題"], "assistant_summary": "y"})
    assert source == "fallback"
    assert out == "主題"


def test_generate_falls_back_on_empty_llm_output():
    out, source = title.generate_title(
        {"user_prompts": ["主題"], "assistant_summary": "y"},
        runner=lambda text, cmd, timeout: "   ",
    )
    assert source == "fallback"


def test_apply_caches_and_sets_fields(tmp_path):
    calls = []

    def runner(text, cmd, timeout):
        calls.append(1)
        return "簡短標題"

    sess = {"session_id": "s9", "user_prompts": ["a"], "assistant_summary": "b"}
    s1 = title.apply(dict(sess), memory_root=tmp_path, runner=runner)
    title.apply(dict(sess), memory_root=tmp_path, runner=runner)
    assert s1["session_title"] == "簡短標題"
    assert s1["assistant_summary"] == "b"
    assert s1["title_source"] == "gemma4"
    assert len(calls) == 1  # second call hit cache


def test_pipeline_injects_title_into_inbox(tmp_path, monkeypatch):
    import json as _json
    from pathlib import Path
    from paulsha_hippo.importer import pipeline

    monkeypatch.setattr(
        "paulsha_hippo.importer.title._default_runner",
        lambda text, cmd, timeout: "UART 升級修復",
    )
    fix = Path(__file__).parent / "fixtures" / "claude_transcript.jsonl"
    qdir = tmp_path / "inbox-queue"
    qdir.mkdir()
    qp = qdir / "q.json"
    qp.write_text(_json.dumps({"tool": "claude-code", "session_id": "s1", "cwd": "/repo",
                               "transcript_path": str(fix)}), encoding="utf-8")
    decision = pipeline.ingest_queue_item(qp, memory_root=tmp_path, dry_run=True)
    rendered = decision["rendered"]
    assert "title: UART 升級修復" in rendered
    assert "title_source: gemma4" in rendered
    assert "## Summary\n已修好 UART 升級流程並加上重試。" in rendered


def test_apply_does_not_cache_fallback(tmp_path):
    calls = []

    def runner(text, cmd, timeout):
        calls.append(1)
        raise RuntimeError("offline")

    sess = {"session_id": "sf", "user_prompts": ["重要任務"], "assistant_summary": "x"}
    s1 = title.apply(dict(sess), memory_root=tmp_path, runner=runner)
    title.apply(dict(sess), memory_root=tmp_path, runner=runner)
    assert s1["title_source"] == "fallback"
    assert len(calls) == 2  # fallback not cached → re-attempted (補生 when gemma4 returns)


def test_gemma4_reachable_targets_upstream_url(monkeypatch):
    import paulsha_hippo.importer.title as t

    seen = {}

    def fake_conn(addr, timeout):
        seen["addr"] = addr
        raise OSError("refused")

    monkeypatch.setenv("PSC_CLAUDE_GEMMA4_UPSTREAM_URL", "http://10.0.0.5:9001")
    monkeypatch.setattr(t.socket, "create_connection", fake_conn)
    assert t._gemma4_reachable() is False
    assert seen["addr"] == ("10.0.0.5", 9001)  # checks upstream host:port, not localhost:8001


def test_gemma4_reachable_false_on_malformed_upstream(monkeypatch):
    import paulsha_hippo.importer.title as t

    attempts = {"n": 0}

    def fake_conn(addr, timeout):
        attempts["n"] += 1
        raise OSError("should not be called")

    monkeypatch.setattr(t.socket, "create_connection", fake_conn)
    for bad in ["192.0.2.10:8001", "not-a-url", "ftp://host:1", ""]:
        monkeypatch.setenv("PSC_CLAUDE_GEMMA4_UPSTREAM_URL", bad)
        assert t._gemma4_reachable() is False
    assert attempts["n"] == 0  # malformed URL never triggers a connection (no localhost probe)


def test_default_runner_threads_upstream_env(monkeypatch):
    import importlib
    import subprocess
    import paulsha_hippo.importer.title as t

    t = importlib.reload(t)
    seen = {}

    def fake_run(command, **kwargs):
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="標題", stderr="")

    monkeypatch.setattr(t, "_gemma4_reachable", lambda *a, **k: True)
    monkeypatch.setattr(
        t.atomizer_config,
        "resolve_agent_exec_settings",
        lambda: (("scripts/claude-gemma4",), "http://10.0.0.5:9001"),
    )
    monkeypatch.setattr(t.subprocess, "run", fake_run)

    assert t._default_runner("prompt", ("scripts/claude-gemma4",), 60) == "標題"
    assert seen["env"]["PSC_CLAUDE_GEMMA4_UPSTREAM_URL"] == "http://10.0.0.5:9001"


def test_generate_returns_neutral_marker_when_no_content():
    def boom(text, cmd, timeout):
        raise AssertionError("LLM must not be called for a content-less session")

    out, src = title.generate_title({"user_prompts": [], "assistant_summary": ""}, runner=boom)
    assert out == "(無內容)"
    assert src == "fallback"


def test_generate_fallback_uses_summary_when_no_prompt(monkeypatch):
    # Summary-only session whose LLM call fails must fall back to the summary,
    # not be mislabeled "(無內容)".
    out, src = title.generate_title(
        {"user_prompts": [], "assistant_summary": "完成了 PON HLAPI 對照表整理"},
        runner=lambda t, c, to: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert src == "fallback"
    assert out.startswith("完成了")
    assert out != "(無內容)"
