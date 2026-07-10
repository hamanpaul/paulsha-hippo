# tests/test_copilot_user_prompt_submit_hook.py —（條件 task：matrix verdict=supported 才存在）
# capability matrix 2026-07-11 復測：官方 key `userPromptSubmitted` probe FIRED、
# additionalContext 注入實測通過 → copilot prompt-time 接線（Task 6 Step 6）。
import json, subprocess, sys
from pathlib import Path

HOOK = Path("paulsha_hippo/hooks/copilot_user_prompt_submit.py").resolve()


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


def test_copilot_prompt_hook_injects_and_attributes_tool(tmp_path):
    _seed(tmp_path)
    proj_cwd = tmp_path / "proj"; proj_cwd.mkdir(exist_ok=True)
    # 實測 payload 形（payload_probe 2026-07-11）：sessionId / timestamp / cwd / prompt
    out = _run(tmp_path, {"sessionId": "cp1", "cwd": str(proj_cwd),
                          "prompt": "SerialWrap 執行"})
    ctx = out.get("additionalContext", "")
    assert "a.md" in ctx and "Read" in ctx
    led = (tmp_path / "runtime" / "ledger" / "offered.jsonl").read_text(encoding="utf-8")
    ev = json.loads(led.splitlines()[0])
    assert ev["tool"] == "copilot-cli" and ev["session_id"] == "cp1"


def test_copilot_prompt_hook_error_emits_empty_exit0(tmp_path):
    out = _run(tmp_path, {"sessionId": "cp2", "cwd": "/nonexistent", "prompt": "x"})
    assert out.get("additionalContext", "") == ""
