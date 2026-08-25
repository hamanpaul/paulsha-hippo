---
status: accepted
work_item: issue-136-knowledge-hygiene
---

# Knowledge hygiene（provenance / supersedes / agent view / episodic / follow-ups）規格（issue #136）

- Issue：[#136](https://github.com/hamanpaul/paulsha-hippo/issues/136)；分支 `feature/136-knowledge-hygiene`

## Problem and Outcome

live 記憶庫 4,178 筆 knowledge notes：3,218 筆 `provenance.commit: _unknown`、337 處 `path:line` 引用全是散文；
跨 session 同主題永不 `supersedes`（`_attach_unambiguous_supersedes` 只涵蓋同 session 重蒸餾），08-17 的過期結論
蓋過 08-21 的修正；可行動發現（「`README-ARC.md:108` 需要更新」）8 天無人處理；frontmatter 佔 79%（`distiller:`
54%），agent 每次 Read 付 ≈ 2.8 KB 拿 ≈ 0.57 KB；104 筆 session 狀態句以 knowledge 層進 shortlist。

預期結果：新 note 自帶可查詢的 commit／cites；同主題只浮出最新；發現變成可驗證、可自動關閉的 follow-up；
agent 以 ≈ 30% 的 token 取得同樣內容且 KPI 看過率不失真；session 狀態不再進 shortlist 但檔案保留。

## Goals

- provenance 六鍵字串化、`commit_source` 明示來源；既存 note 可回填並標 `backfill-approx`。
- 同主題折疊在呈現層完成，`search()` determinism 與 KPI 分母不變；跨 session supersedes 分 auto／review。
- `hippo show --agent` 精簡輸出並自行記 read 事件。
- session 狀態降為 `episodic` 層（保留、可逆），不動 `classify_noise`。
- follow-up ledger：regex 抽取、唯讀 verify、dream 階段、wakeup brief 一行。
- 所有 migration：dry-run 預設、apply parse-equivalent、冪等、只碰 knowledge 層。

## Requirements

### Provenance（fix 1）

SessionEnd hook SHALL inline、stdlib-only、best-effort（≤ 2 s）寫 payload `commit / git_branch / git_dirty`；失敗不寫、exit 0。
importer SHALL fallback `git_head(toplevel)` 並永遠寫 `commit_source ∈ {hook, import-discovery, backfill-approx}`。
provenance 六鍵 `repo / commit / path / commit_source / branch / dirty` 一律字串，atomizer／janitor 貫通，三鍵舊 note 仍可讀。
atomizer SHALL 抽 `cites: [{path, line}]`（regex、保序去重、上限 32、不查存在性、optional）。
`hippo knowledge backfill-provenance` SHALL 以 archive queue `cwd`+`ended_at` 跑 `rev-list -1 --before` 回填。
janitor `check_provenance_commit: true` 時 dangling commit ⇒ `source_invalid`；預設 false；`backfill-approx` 不觸發。

### 同主題新鮮度／supersedes（fix 2）

`topic.py` SHALL 為純函式：同 project 或同 family，且 canonical title 相等 ∨ alias 相等 ∨ Jaccard ≥ 0.6（token ≥ 4）。
`projects.yaml` 頂層 `families:` SHALL 為 opt-in；無設定時跨 project MUST NOT 判同主題。
shortlist SHALL 在 claim 前 keep-newest 折疊，`collapsed` 記進 offered ledger；`search()` 只多回 `captured_at`。
`link-supersedes` auto tier SHALL 只連完全同標題／別名且嚴格較新者，多對一與同時間戳留 review；review 只出報表，`--accept` 才寫。
`_attach_unambiguous_supersedes` SHALL 放寬跨 session，其餘條件不變；下游 decay 路徑不變。

### Agent view（fix 3b）

`hippo show <ref> --agent` SHALL 只印 ≤ 6 行 header＋body，bytes ≤ body + 400，不含 distiller／checksum／publication_id。
帶 `--tool/--session-id` 時 SHALL 以 Read hook 同 schema append read 事件（`offered` 依 by_id），否則不寫；歸因失敗不影響輸出。
shortlist 每列 SHALL 加 `slice_id`；hint 依 `shortlist.read_hint: show|read`，show 模式命令 SHALL 已填 tool／session_id。
不做 sidecar 拆檔。

### Episodic（fix 4）

`noise.episodic_reason` SHALL 非 deletion-grade；`classify_noise`／`prune-noise` 行為不變。
命中者 SHALL 以 `memory_layer: episodic` 發布到 `knowledge/<proj>/`，index／MOC 排除，`validate` 接受。
skill CONCEPT_ANALYSIS SHALL 排除 session 狀態句。
`mark-episodic` SHALL 支援 dry-run／apply／`--revert`，apply 記 lifecycle `archived/episodic`，冪等。

### Follow-ups（fix 5）

抽取 SHALL 純 regex：可行動 pattern＋`path:line`（同行或 ±1 行）＋`expected_stale`；id 由 hash 派生，冪等。
`followups.jsonl` SHALL append-only、事件 fold 五狀態。
`verify` SHALL 唯讀讀 `file:line ±2`：舊值在 ⇒ `verified-open`，不在 ⇒ `resolved-in-source`，不存在 ⇒ `unverifiable`。
dream `followups` 階段 SHALL 在 janitor 後、失敗不改 run status；wakeup brief SHALL 末尾一行 open 計數（0 不印）；不進 shortlist。
KPI report SHALL 加 `followups_open / resolved_in_source_7d`。

### Flags 與治理

`runtime_flags.py` SHALL best-effort 讀 `shortlist.collapse_same_topic`、`shortlist.read_hint`、`followups.enabled`、`episodic_filter`，缺鍵預設，不需 config migration。
TTL SHALL 維持預設 90 天，不加 override；任何真實 janitor scan 前 SHALL 先 dry-run 保留報表。
五個 `changelog.d/hygiene-*.md`；README 日常命令同步；pytest／policy_check／openspec 全綠。

## Non-Goals

- 3a sidecar 拆檔（`distiller:` 出走）。
- fix 6：`kind` 欄位、prompt 型別過濾、`verify:` shell 檢查。
- 不改 `search()` 排序、不改 `classify_noise`、不改 Stage 3 REQUIRED schema、不改 PHASES／ARTIFACT_KINDS。
- 不改 dream status 判定語意（followups 失敗只記 error）。

## Acceptance

- 在 git repo 內結束的新 session：inbox 與 knowledge slice `provenance.commit` 為 40-hex、`commit_source: hook`。
- `hippo knowledge backfill-provenance --dry-run` 對 live root 報表候選 ≈ 3,218、cites ≈ 159；apply 後再 dry-run 為 0。
- shortlist 對含同主題三筆的查詢只浮出最新，offered ledger 有 `collapsed`。
- `hippo show <id> --agent --tool claude-code --session-id S` 輸出不含 distiller 且 `memory_usage.jsonl` 多一筆 read。
- `hippo knowledge mark-episodic --dry-run` 列 ≈ 104 筆；apply 後 shortlist 不再浮出，`--revert` 可還原。
- `hippo followups extract --apply` 後 `verify` 對 README-ARC.md:108 案例回 `verified-open`，修檔後 `resolved-in-source`。
- 全套 pytest 通過；`python3 -m policy_check --repo .` 無 failure；`openspec validate --all --strict` 通過。
