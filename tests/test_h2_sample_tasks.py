"""v4.1：scripts/h2_sample_tasks.py 的排除、配額與分批。只用合成索引與假的 issue 清單，不呼叫 gh。"""

import importlib.util
import json
import sqlite3
from pathlib import Path

from paulsha_hippo.moc.search import index_path

_SPEC = importlib.util.spec_from_file_location(
    "h2_sample_tasks", Path(__file__).resolve().parents[1] / "scripts" / "h2_sample_tasks.py")
S = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(S)

HIPPO = "github.com/hamanpaul/paulsha-hippo"


def _setup(tmp_path, monkeypatch):
    db = index_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE slice_meta (project TEXT, captured_at TEXT)")
    conn.executemany("INSERT INTO slice_meta VALUES (?, ?)",
                     [(HIPPO, "2026-08-01T00:00:00Z"), (S.FILL_REPO, "2026-08-01T00:00:00Z")])
    conn.commit()
    conn.close()
    issues = {
        HIPPO: [{"number": n, "title": f"hippo {n}", "body": "b", "createdAt": "2026-09-01T00:00:00Z"} for n in range(1, 6)],
        S.FILL_REPO: [{"number": n, "title": f"cortex {n}", "body": "b", "createdAt": "2026-09-01T00:00:00Z"}
                      for n in range(1, 31)],
    }
    monkeypatch.setattr(S, "_closed_issues", lambda repo: issues.get(repo, []))


def _run(tmp_path, out, *extra):
    assert S.main(["--memory-root", str(tmp_path), "--seed", "s", "--out", str(out), *extra]) == 0
    return json.loads(out.read_text())


def test_exclude_quota_and_batches_are_disjoint(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    first = _run(tmp_path, tmp_path / "round1.json", "--total", "8", "--dev", "2", "--quota", f"{HIPPO}=2")
    batch1 = _run(tmp_path, tmp_path / "b1.json", "--total", "12", "--dev", "0",
                  "--exclude-tasks", str(tmp_path / "round1.json"), "--quota", f"{HIPPO}=100")
    batch2 = _run(tmp_path, tmp_path / "b2.json", "--total", "5", "--dev", "0",
                  "--exclude-tasks", str(tmp_path / "round1.json"), "--exclude-tasks", str(tmp_path / "b1.json"))
    ids = [{t["task_id"] for t in d["tasks"]} for d in (first, batch1, batch2)]
    assert not ids[0] & ids[1] and not ids[0] & ids[2] and not ids[1] & ids[2]
    assert [len(i) for i in ids] == [8, 12, 5]
    hippo_left = 5 - sum(t["repo"] == HIPPO for t in first["tasks"])
    assert hippo_left == 3
    assert sum(t["repo"] == HIPPO for t in batch1["tasks"]) == hippo_left  # 配額覆寫：剩下的 hippo 全取
    assert all(t["split"] == "hidden-pool" for t in batch1["tasks"])
    assert batch1["sampling"]["excluded_tasks"] == 8 and batch1["sampling"]["quotas"][HIPPO] == 100
    assert _run(tmp_path, tmp_path / "b1-again.json", "--total", "12", "--dev", "0",
                "--exclude-tasks", str(tmp_path / "round1.json"), "--quota", f"{HIPPO}=100")["tasks"] == batch1["tasks"]
