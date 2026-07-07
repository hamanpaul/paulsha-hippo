"""#218 對抗審查回歸（自主 repo 隨遷）：session-end 截取自足性。"""
from pathlib import Path

from paulsha_hippo import paths


def test_session_end_capture_path_stays_stdlib_only():
    """#218 F1 回歸：session-end 截取 hooks 為複製部署腳本，module-level
    禁止 import paulshaclaw.*——package import 失敗不得讓 queue write 前就死掉。
    （session_start/wakeup 類經 _bootstrap 的函式內 fail-open import 屬 main 既有慣例，不在此限。）"""
    import ast

    pkg_root = Path(paths.__file__).resolve().parents[1]
    hooks_dir = pkg_root / "paulsha_hippo" / "hooks"
    capture_hooks = ["claude_session_end.py", "codex_session_end.py", "copilot_session_end.py"]
    offenders = []
    for name in capture_hooks:
        tree = ast.parse((hooks_dir / name).read_text(encoding="utf-8"))
        for node in tree.body:  # module-level only
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            if any(n == "paulshaclaw" or n.startswith("paulshaclaw.") for n in names):
                offenders.append(name)
                break
    assert offenders == []


def test_copied_session_end_hook_runs_without_package(tmp_path):
    """#218 F1 部署情境回歸：把 hook 複製到套件外目錄、無 repo cwd/PYTHONPATH，
    餵 stdin JSON 後必須 exit 0 且 queue 檔寫入（fail-open 截取不丟）。"""
    import json as _json
    import shutil
    import subprocess
    import sys as _sys

    pkg_root = Path(paths.__file__).resolve().parents[1]
    src = pkg_root / "paulsha_hippo" / "hooks" / "claude_session_end.py"
    deploy_dir = tmp_path / "deployed-hooks"
    deploy_dir.mkdir()
    hook = deploy_dir / "claude_session_end.py"
    shutil.copy(src, hook)
    memory_root = tmp_path / "memory"

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PSC_MEMORY_ROOT": str(memory_root),
        "PSC_IMPORTER_DISABLED": "1",
    }
    payload = {"session_id": "deploy-selfcontained-test", "transcript_path": str(tmp_path / "t.jsonl")}
    completed = subprocess.run(
        [_sys.executable, str(hook)],
        input=_json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(tmp_path),
        env=env,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    queue = list((memory_root / "runtime" / "queue").glob("claude-code__*.json"))
    assert queue, "queue 檔未寫入——複製部署截取失效"
