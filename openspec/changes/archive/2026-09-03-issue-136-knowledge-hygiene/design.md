---
status: accepted
work_item: issue-136-knowledge-hygiene
---

# Knowledge hygiene 設計

- 日期：2026-08-25
- Issue：[#136](https://github.com/hamanpaul/paulsha-hippo/issues/136)（tracking issue，2026-08-25 開立）；分支 `feature/136-knowledge-hygiene`
- 狀態：已核可，待實作
- 完整設計與量化證據：`docs/superpowers/specs/2026-08-25-issue-136-knowledge-hygiene-design.md`；本文件只列決策。

## 背景

live 記憶庫 4,178 筆 knowledge notes 中 3,218 筆 `provenance.commit: _unknown`、跨 session 同主題永不
連結、可行動發現無人追、frontmatter 佔 79%、104 筆 session 狀態被當知識。五個 fix 全部掛在既有管線的
既有落點上（SessionEnd hook → importer → atomizer → index／shortlist → janitor／dream／wakeup），
不新造框架。

## Decisions

### D1：provenance 的生產者放在 SessionEnd hook，importer 只做 fallback

寫入鏈已完整（`adapters/base.py` 讀 payload `commit` → `sanitizer` → `importer/frontmatter.py` →
atomizer fragment）。缺的是生產者。三支 hook（claude／codex／copilot）**必須維持 stdlib-only、複製部署可
獨立執行**（`tests/test_hooks_selfcontained.py`），因此 `_git_snapshot(cwd)` inline 在各 hook 重複三份，
不抽共用模組；best-effort、timeout 2 s、任何失敗不寫鍵、exit 0。importer 在
`_preview_queue_item_unlocked` 以 `_git.git_head(discovered_toplevel)` 補值並記
`commit_source: import-discovery`；hook 有值則 `hook`；一次性回填標 `backfill-approx`，永不假裝精確。

### D2：provenance 子鍵一律字串、六鍵向後相容；cites 純 regex

`repo / commit / path / commit_source / branch / dirty`，`dirty ∈ {"true","false"}`；缺省省略，舊讀者只取
前三鍵仍可讀。`_render_fragment`／`_read_fragment`／`slice_frontmatter.render`／`record_source` 的三鍵迴圈
擴成六鍵。`cites` 由 `extract_cites(body)` 以 `[\w./-]+\.(md|py|c|h|lds|syscfg|cpp|yml|yaml|sh|json):\d+`
保序去重、上限 32，不做存在性檢查（存在性交給 follow-ups verify）；欄位 optional，`validate()` 對缺
`cites` 的舊 note 仍通過。

### D3：同主題只在 shortlist 呈現層折疊；`search()` 不加新鮮度

`search()` 的 determinism 契約與 KPI 分母必須穩定，因此折疊放在 `build_shortlist_and_record` 的 claim
之前：對過取的 hits 依 `topic.is_same_topic`（同 project 或同 family；canonical title 相等 ∨ aliases 相等 ∨
token Jaccard ≥ 0.6 且 token ≥ 4）分組，只留 `captured_at` 最新者，其餘 sl_id 寫進 offered ledger 的
`collapsed` 保留可稽核性。`search()` 只多回傳 `captured_at`。`families:` 為 `projects.yaml` 頂層 opt-in
清單（hand-rolled parser 需明確解析）；沒有 family 設定時，跨 repo 的「父目錄 vs 子 repo」對（08-17 vs
08-21 flash layout）**不會**被折疊或連結——這是明示的邊界。

### D4：跨 session supersedes 分 auto／review 兩層，下游 plumbing 不動

auto tier：同 project（或 family）＋canonical title／alias 完全相等＋`captured_at` 嚴格較新＋checksum 不同
→ 新者 `supersedes: [舊]` 並 `relations.append_edge`；同時間戳或多對一留 review。review tier 只出報表
（Jaccard ≥ 0.6 或 tags 交集 ≥ 2 或 `related` 互指），使用者勾選後 `--accept`。新 note 路徑把
`_attach_unambiguous_supersedes` 的 `distilled_from` 條件放寬成跨 session（其餘條件不變）。janitor
`decay_superseded: true` → lifecycle → `search(include_decayed=False)` 隱藏，這條路已由 32 筆 lifecycle
事件證明會走，不改。

### D5：`hippo show --agent` 取代 sidecar 拆檔；show 必須自己記 read 事件

`distiller:` 在 read-time 無消費者，拆檔安全但要改寫 4,178 檔（≈ 省 43%）；`show --agent` 無資料變更且
省 ≈ 70%，先做。agent 改用 show 後 PostToolUse Read hook 不再觸發，因此 `show` 帶 `--tool/--session-id`
時必須以相同 schema append `memory_usage.jsonl`（`source:"read"`，`offered` 依 per-session map
`by_id`），否則 KPI 看過率歸零。shortlist 每列加印 `slice_id` 但保留絕對路徑（offered map 以 path 為
鍵，Read 歸因不受影響）；hint 由 `shortlist.read_hint: show|read` 切換。

### D6：session 狀態降為 `memory_layer: episodic`，不走 `classify_noise`

`classify_noise` 同時被 `knowledge prune-noise` 用來刪檔；session 狀態不該被刪，只該不被撈。新增
`noise.episodic_reason(body) -> str|None`（標題 handoff／狀態結尾，或散文行 ≥ 50% 命中狀態 pattern），
atomizer 在 supersedes 之後、validate 之前把命中者降層並照常發布到 `knowledge/<proj>/`；`build_index`
既有 `non-knowledge-layer:<layer>` 分支與 MOC builder 自動排除；`validate` 放寬為
`memory_layer ∈ {knowledge, episodic}`；skill CONCEPT_ANALYSIS 加排除規則（`skill_hash` 變更會反映在
provenance）。既有 104 筆由 `mark-episodic` 降層，`--revert` 可救，lifecycle 用既有 `archived` 事件型別。

### D7：follow-ups 純 regex、唯讀 verify、只進 wakeup brief

抽取不依賴 LLM（新舊 note 一致）：可行動 pattern＋同行或前後一行的 `path:line`＋同行第一個數字／反引號
字串作 `expected_stale`；id 由（slice_id, target, claim）hash 派生，重跑不重複。狀態以事件 fold
（`opened / verified-open / resolved-in-source / closed-manual / unverifiable`）。`verify` 以
`projects.yaml` roots 為 base 讀 `file:line ±2`，唯讀、不改任何 repo 檔；工作樹狀態只作提示。dream 在
janitor 後加可選 `followups_fn`，例外進 `summary.error`、永不改變 run status；呈現只在 wakeup brief
末尾一行（0 筆不印），不進 per-prompt shortlist。

### D8：一次性 migration 全部仿 `tags_migration.py`；flags 無需 config migration

scan → dry-run 報表 → `frontmatter_io.update()` apply → 冪等（apply→dry-run 0→再 apply no-op、bytes
不變）；只碰 `memory_layer == "knowledge"`（mark-episodic `--revert` 例外）。`frontmatter_io.update`
加 `remove=()` 參數供 revert 移除 `episodic_reason`。新 flag 由 `runtime_flags.py` 從 canonical
`config.yaml`（`paths.atomizer_config_path()`）best-effort 讀，缺鍵預設；`load_config` 忽略未知頂層鍵。

### D9：TTL 維持預設 90 天（使用者決定）；janitor `check_provenance_commit` 只實作不開

decay 為軟性（檔案留在磁碟、`search --include-decayed` 可取回、只在同 source 重新 import 時
reactivation），90 天未被使用即降級是合理政策，不加 override；任何真實 scan 前先 `--dry-run` 保留報表，
fix 2b 跑 janitor 前重複同一道閘。`check_provenance_commit: true` 時 `provenance.commit` 在對應 repo
`git cat-file -e` 失敗 ⇒ `source_invalid`（detail `provenance_commit`）；預設維持 false，dream 傳
no-op。

## Testing

- hooks：git repo cwd 下 payload 含 40-hex `commit`／`git_branch`／`git_dirty`；非 repo／`git` 不在
  PATH／timeout 三情境無該鍵且 exit 0；self-contained 測試不變。
- importer／atomizer／janitor：六鍵 provenance 與 `cites` round-trip；`commit_source` 正確；舊三鍵 note
  仍可讀；`validate()` 對缺 `cites` 與 `memory_layer: episodic` 皆通過。
- topic／shortlist：同主題判定表（相等／別名／Jaccard 0.59 vs 0.60／短標題／不同 project／同 family）；
  12 hits 含 3 筆同主題只 claim 最新、offered ledger 記 `collapsed`、下一輪不再 offer 被折疊者。
- show：`--agent` 輸出不含 `distiller`／`checksum`／`publication_id`，bytes ≤ body＋400；帶 tool/session
  多一筆 `source=read` 且 `offered` 與 map 一致；不帶不寫；KPI 看過率分子含經 show 的 read。
- migrations（backfill-provenance／link-supersedes／mark-episodic／followups extract）：dry-run 零副作用、
  apply 冪等、parse-equivalent、只碰 knowledge 層；link-supersedes 多對一留 review、apply 後 janitor 下一輪
  產生 `superseded` decay 事件。
- follow-ups verify：stale 值仍在／已移除／檔案不存在／行號漂移 ±2 四種轉換；verify 不寫 repo；dream
  followups 階段失敗不改變 run status；brief 含 open 計數行、0 筆不印。
- 全套 pytest、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。
