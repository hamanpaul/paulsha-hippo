# tests/test_user_prompt_submit_hook.py
import json, subprocess, sys
from pathlib import Path

HOOK = Path("paulsha_hippo/hooks/claude_user_prompt_submit.py").resolve()


def _seed(mr: Path):
    from paulsha_hippo.moc import search as S
    k = mr / "knowledge" / "proj"; k.mkdir(parents=True)
    (k / "a.md").write_text(
        "---\nmemory_layer: knowledge\nslice_id: sl-aaaaaaaaaaaaaaaa\nproject: proj\n"
        "title: SerialWrap\ncaptured_at: '2026-06-29T00:00:00Z'\n---\n抽象 UART 執行層\n",
        encoding="utf-8")
    S.build_index(mr, link_weights={})


def _run(mr: Path, payload: dict) -> dict:
    env = {"PSC_MEMORY_ROOT": str(mr), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def test_relevant_prompt_injects_shortlist(tmp_path, monkeypatch):
    _seed(tmp_path)
    # resolve_project depends on cwd; point cwd at a dir whose folder name == 'proj'
    proj_cwd = tmp_path / "proj"; proj_cwd.mkdir(exist_ok=True)
    out = _run(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                          "cwd": str(proj_cwd), "prompt": "SerialWrap 執行"})
    ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "a.md" in ctx and "Read" in ctx


def test_error_or_unknown_emits_empty_and_exit0(tmp_path):
    out = _run(tmp_path, {"hook_event_name": "UserPromptSubmit", "session_id": "s2",
                          "cwd": "/nonexistent", "prompt": "anything"})
    assert out.get("hookSpecificOutput", {}).get("additionalContext", "") == ""
