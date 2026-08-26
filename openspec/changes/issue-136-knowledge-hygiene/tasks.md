---
status: accepted
work_item: issue-136-knowledge-hygiene
---

# Tasks: issue-136-knowledge-hygiene（issue #136）

依 `docs/superpowers/plans/2026-08-25-issue-136-knowledge-hygiene-plan.md` Task 0–17；四條獨立線
{T1→T2→T3}、{T4→T5→T6}、{T4→T7→T8}、{T9} 可並行，之後 T10（需 T2/T3/T9）、T11→T12（需 T4/T9）、
T13→T14（需 T4）、T15（需 T7）、T16（需 T2）、T17 收尾。每個 task 先寫失敗測試再實作。

- [ ] Task 0: TTL 維持預設 90 天——不建 override；任何真實 janitor scan 前先 `--dry-run` 保留報表（決定 2026-08-25） <!-- id: 0 -->
- [ ] Task 1: fix 1a — 三支 SessionEnd hook inline `_git_snapshot(cwd)` → payload `commit / git_branch / git_dirty`（stdlib-only、best-effort、2 s、exit 0） <!-- id: 1 -->
- [ ] Task 2: fix 1a/1b — importer `NormalizedSession`＋sanitizer＋`_git.git_head`＋`render_markdown` 六鍵 provenance 與 `commit_source: hook|import-discovery` <!-- id: 2 -->
- [ ] Task 3: fix 1a — atomizer `_split_pass`／`_render_fragment`／`_read_fragment`、`slice_frontmatter.render`、janitor `record_source` 六鍵貫通 <!-- id: 3 -->
- [ ] Task 4: `runtime_flags.py` — `shortlist.collapse_same_topic`／`shortlist.read_hint`／`followups.enabled`／`episodic_filter` 單一讀取點＋`atomizer.yaml` 模板鍵 <!-- id: 4 -->
- [ ] Task 5: fix 3b — `hippo show <ref> --agent [--tool --session-id]`＋`usage_read.append_read_event`（與 PostToolUse hook 同 schema） <!-- id: 5 -->
- [ ] Task 6: fix 3b — `format_shortlist(hint="show")`、每列加 `slice_id`、wakeup recall 指引改 show <!-- id: 6 -->
- [ ] Task 7: fix 2a — `topic.py`（canonical_title／title_tokens／is_same_topic／collapse_same_topic／family_key）＋`projects.yaml` 頂層 `families:` 解析 <!-- id: 7 -->
- [ ] Task 8: fix 2a — `search()` 回傳 `captured_at`；shortlist claim 前同主題折疊 keep-newest；offered ledger `collapsed` <!-- id: 8 -->
- [ ] Task 9: fix 1c — `extract_cites` 與 `build_from_proposal` 的 `cites` 欄位 <!-- id: 9 -->
- [ ] Task 10: fix 1b/1c migration — `hippo knowledge backfill-provenance`（`git_rev_before` → `backfill-approx`；cites 回填；dry-run/apply/冪等） <!-- id: 10 -->
- [ ] Task 11: fix 5 — `followups.py`（regex 抽取、jsonl fold、唯讀 verify ±2、close）＋`hippo followups list|verify|close|extract` <!-- id: 11 -->
- [ ] Task 12: fix 5 — dream 可選 `followups_fn` 階段（例外進 summary.error、不降級）、wakeup brief 一行、KPI `followups` 區塊 <!-- id: 12 -->
- [ ] Task 13: fix 4 — `noise.episodic_reason`（非 deletion-grade）、發布前降層 `memory_layer: episodic`、`validate` 放寬、`AtomizerConfig.episodic_filter`、skill 規則 <!-- id: 13 -->
- [ ] Task 14: fix 4 migration — `hippo knowledge mark-episodic [--revert]`＋`frontmatter_io.update(remove=)` <!-- id: 14 -->
- [ ] Task 15: fix 2b — `supersedes_link.py`（auto/review 分層、報表、`--accept`）＋`_attach_unambiguous_supersedes` 放寬跨 session 完全同標題 <!-- id: 15 -->
- [ ] Task 16: fix 1d — `_git.git_commit_exists`、janitor `source_commit_exists` 透傳、`check_provenance_commit` 語意（預設 false，dream 傳 no-op） <!-- id: 16 -->
- [ ] Task 17: 交付治理 — 五個 `changelog.d/hygiene-*.md` 碎片、`README.md` 日常命令同步（R-16）、全套 pytest／policy_check／openspec、live migration 順序＋`hippo upgrade`＋`hippo install hooks` <!-- id: 17 -->
