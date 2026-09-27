#!/usr/bin/env python3
"""#164：依 docs/h2-offline-baseline.md 第 6 節的規則，從 public repo 已關閉 issue 抽出 H2 task 檔。

需要 gh CLI（讀 public issue）。私有字詞清單只從 --deny-terms 讀取，不寫進輸出。
輸出的 task 檔含 issue 文字（public），仍建議與凍結候選集一起放在私有實驗目錄。

v4.1（第二輪）：``--exclude-tasks`` 排除先前各輪／各批用過的題目，``--quota`` 覆寫配額，
``--dev 0`` 表示不在抽樣時分組（dev／hidden 改由 gold 完成後的分層抽樣決定）。
同一個 seed 的排序是確定的，所以追加一批＝排除前面各批後再取下一段。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paulsha_hippo import h2_offline  # noqa: E402
from paulsha_hippo.moc.search import index_path  # noqa: E402

QUOTAS = {  # cortex 補滿到 TOTAL
    "github.com/hamanpaul/serialwrap": 2,
    "github.com/hamanpaul/paulsha-conventions": 6,
    "github.com/hamanpaul/paulsha-patchmud": 6,
    "github.com/hamanpaul/paulsha-hippo": 10,
}
FILL_REPO = "github.com/hamanpaul/paulsha-cortex"
TOTAL = 44
DEV = 12
BODY_CHARS = 1500
MIN_GAP = dt.timedelta(days=3)
_RELEASE = re.compile(r"^(chore\(release\)|release)", re.IGNORECASE)


def _order(seed: str, repo: str, issue: int) -> str:
    return hashlib.sha256(f"{seed}|{repo}|{issue}".encode("utf-8")).hexdigest()


def _earliest(conn: sqlite3.Connection, repo: str) -> dt.datetime | None:
    times = [h2_offline.parse_time(r[0]) for r in conn.execute(
        "SELECT captured_at FROM slice_meta WHERE project = ?", (repo,))]
    times = [t for t in times if t is not None]
    return min(times) if times else None


def _closed_issues(repo: str) -> list[dict]:
    owner_repo = repo.removeprefix("github.com/")
    out = subprocess.run(
        ["gh", "issue", "list", "-R", owner_repo, "--state", "closed", "--limit", "1000",
         "--json", "number,title,body,createdAt"], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", required=True)
    parser.add_argument("--deny-terms", default=None)
    parser.add_argument("--seed", default="h2-20260927")
    parser.add_argument("--out", required=True)
    parser.add_argument("--total", type=int, default=TOTAL)
    parser.add_argument("--dev", type=int, default=DEV, help="抽樣時直接分到 dev 的題數；0 表示全部進 hidden-pool")
    parser.add_argument("--exclude-tasks", action="append", default=[],
                        help="排除這些 task 檔（format=hippo-h2-tasks）裡的題目，可重複指定")
    parser.add_argument("--quota", action="append", default=[], metavar="REPO=N",
                        help="覆寫某 repo 的配額（github.com/<owner>/<repo>=N），可重複指定")
    args = parser.parse_args(argv)
    quotas = dict(QUOTAS)
    for item in args.quota:
        repo, _, count = item.partition("=")
        if repo not in quotas or not count.isdigit():
            parser.error(f"--quota 格式或 repo 不對：{item}")
        quotas[repo] = int(count)
    excluded = set()
    for path in args.exclude_tasks:
        excluded.update(t["task_id"] for t in json.loads(Path(path).read_text(encoding="utf-8"))["tasks"])

    deny = h2_offline.load_deny_terms(Path(args.deny_terms) if args.deny_terms else None)
    conn = sqlite3.connect(f"file:{index_path(Path(args.memory_root))}?mode=ro", uri=True)
    pools: dict[str, list[dict]] = {}
    stats: dict[str, dict] = {}
    for repo in [*quotas, FILL_REPO]:
        earliest = _earliest(conn, repo)
        if earliest is None:
            pools[repo], stats[repo] = [], {"earliest_slice": None, "eligible": 0}
            continue
        since = earliest + MIN_GAP
        eligible, dropped = [], {"release": 0, "too-early": 0, "egress": 0, "excluded": 0}
        slug = repo.rsplit("/", 1)[-1]
        for raw in _closed_issues(repo):
            if f"{slug}-{raw['number']}" in excluded:
                dropped["excluded"] += 1
                continue
            created = h2_offline.parse_time(raw["createdAt"])
            if created is None or created < since:
                dropped["too-early"] += 1
                continue
            if _RELEASE.match(raw["title"].strip()):
                dropped["release"] += 1
                continue
            body = (raw.get("body") or "").strip()[:BODY_CHARS]
            if h2_offline.deny_scan(f"{raw['title']}\n{body}", deny):
                dropped["egress"] += 1
                continue
            eligible.append({"repo": repo, "issue": raw["number"], "title": raw["title"].strip(), "body": body,
                             "as_of": created.strftime("%Y-%m-%dT%H:%M:%SZ")})
        eligible.sort(key=lambda t: _order(args.seed, repo, t["issue"]))
        pools[repo] = eligible
        stats[repo] = {"earliest_slice": earliest.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "eligible": len(eligible), "dropped": dropped}
    conn.close()

    selected = []
    for repo, quota in quotas.items():
        selected += pools[repo][:quota]
    selected += pools[FILL_REPO][:max(args.total - len(selected), 0)]
    for repo in stats:
        stats[repo]["selected"] = sum(1 for t in selected if t["repo"] == repo)
    # 分組用另一個 hash：入選用的 hash 在大 repo 會集中在小值端，沿用會讓 dev 全落在同一個 repo。
    selected.sort(key=lambda t: _order(f"{args.seed}|split", t["repo"], t["issue"]))
    tasks = []
    for index, task in enumerate(selected):
        split = "dev" if index < args.dev else "hidden-pool"
        slug = task["repo"].rsplit("/", 1)[-1]
        tasks.append({"task_id": f"{slug}-{task['issue']}", "split": split, **task})
    payload = {
        "format": h2_offline.TASKS_FORMAT,
        "version": 1,
        "sampling": {"seed": args.seed, "total": args.total, "dev": args.dev, "quotas": quotas, "fill_repo": FILL_REPO,
                     "body_chars": BODY_CHARS, "min_gap_days": MIN_GAP.days, "per_repo": stats,
                     "excluded_tasks": len(excluded), "deny_terms_count": len(deny)},
        "tasks": tasks,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(tasks), "per_repo": {r: s["selected"] for r, s in stats.items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
