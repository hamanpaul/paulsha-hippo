---
status: accepted
work_item: issue-136-knowledge-hygiene
---

# Knowledge hygiene：provenance / supersedes / agent view / episodic / follow-ups 提案

## Why

2026-08-25 在 ot-ti-mirror session（MCU 知識資產盤點）對 live 記憶庫（`~/.agents/memory/knowledge`，
4,178 筆 knowledge notes）做唯讀統計，並在同一個 session 內觀察到三種實際失效：

- **provenance 是散文**：`provenance.commit: _unknown` 3,218 筆（MCU-Octopus + ot-ti-mirror 647/647）；
  commit 只以「HEAD `2a655c3`」這種散文出現在 body；`path:line` 引用 337 處／159 筆，全部不可查詢。
  整條寫入鏈（`adapters/base.py` 讀 payload `commit` → `sanitizer` 白名單 → `importer/frontmatter.py`
  寫入 → atomizer 原樣帶進 fragment）都在，缺的只是生產者：SessionEnd hook 不抓 `git rev-parse HEAD`。
- **舊 note 蓋過新 note**：同主題跨 session 永不連結——`_attach_unambiguous_supersedes` 只涵蓋同一
  session 重蒸餾；shortlist 無新鮮度項。實例：08-17「NVS 0x54000 未顯式保留」被 hook 浮出，
  但 08-21「以 linker/map assertion 防止碰撞」已修正；兩筆 `supersedes: []`。
- **發現沒有閉環**：08-17 note 明寫「`README-ARC.md:108` 的 133,604 B 已不符，需要更新」，8 天後
  該行仍是 133,604。它是一筆記憶，不是一張票。
- **token 結構**：frontmatter 佔全檔 79%（`distiller:` 佔其中 54%），agent 依 hint 用 Read 開整檔，
  每筆付 ≈ 2.8 KB 拿 ≈ 0.57 KB；`distiller:` 在 read-time 沒有任何消費者（index／census／MOC／janitor／
  wakeup／KPI 都不讀，grep 確認）。
- **session 狀態被當知識**：104 筆 body 含「尚未 commit／session 結束時／handoff／本次修改僅限」，
  寫入即過期，卻以 `memory_layer: knowledge` 進索引與 shortlist。

## What Changes

- **fix 1 provenance**：三支 SessionEnd hook inline、stdlib-only、best-effort（2 s timeout）抓
  `commit / git_branch / git_dirty` 進 queue payload；importer 以 `_git.git_head(toplevel)` 補
  fallback 並記 `provenance.commit_source ∈ {hook, import-discovery, backfill-approx}`；provenance
  子鍵一律字串、擴為六鍵（`repo / commit / path / commit_source / branch / dirty`），atomizer 三處與
  janitor `record_source` 貫通；atomizer 發布前以 regex 抽 `cites: [{path, line}]`（保序去重、上限
  32、不檢查存在性）；janitor 既有 `check_provenance_commit` flag 給予語意（true 時 dangling commit
  ⇒ `source_invalid`，預設維持 false）。一次性 `hippo knowledge backfill-provenance [--dry-run|--apply]`
  以 archive queue 的 `cwd`+`ended_at` 跑 `git rev-list -1 --before` 回填，標 `backfill-approx`。
- **fix 2 同主題新鮮度／supersedes**：新純函式模組 `paulsha_hippo/topic.py`（canonical title、
  token、`is_same_topic`、`collapse_same_topic`、`family_key`）；`projects.yaml` 頂層 `families:`
  opt-in 清單；shortlist 在 claim 前依同主題分組只留 `captured_at` 最新者，被折疊的 sl_id 記進 offered
  ledger `collapsed`（`search()` 本身不加新鮮度，保 determinism 與 KPI 分母）；`hippo knowledge
  link-supersedes [--tier auto|review] [--dry-run|--apply|--accept <report>]` 回填跨 session supersedes；
  `_attach_unambiguous_supersedes` 放寬 `distilled_from` 條件為跨 session 完全同標題。
- **fix 3 agent view**：`hippo show <slice_id|path> --agent [--tool --session-id]` 只印 ≤ 6 行 header＋
  body（不含 distiller／hash／attempts，≈ 省 70%）；帶 tool/session 時以與 PostToolUse Read hook 相同
  schema append `memory_usage.jsonl` read 事件（否則 KPI 看過率歸零）；shortlist 每列加印 `slice_id`，
  hint 依 `shortlist.read_hint: show|read` 切換；wakeup recall 指引同步。不做 sidecar 拆檔（3a 延後）。
- **fix 4 episodic**：skill CONCEPT_ANALYSIS 排除 session 狀態句；`noise.episodic_reason()`（非
  deletion-grade，不改 `classify_noise`，因為 `prune-noise` 會刪檔）；命中者以 `memory_layer: episodic`
  照常發布到 `knowledge/<proj>/`，index／MOC 既有 non-knowledge-layer 分支自動排除；`validate` 放寬
  接受 `episodic`；一次性 `hippo knowledge mark-episodic [--dry-run|--apply|--revert]`。
- **fix 5 follow-ups**：regex 抽「可行動句＋`path:line`＋預期舊值」→ `runtime/ledger/followups.jsonl`
  （append-only、事件 fold）；`hippo followups list|verify|close|extract`；`verify` 以 `projects.yaml`
  roots 唯讀重查 `file:line ±2`，舊值消失自動 `resolved-in-source`；dream 在 janitor 後加可選
  `followups` 階段（失敗只記 warning，永不改變 run status）；wakeup brief 末尾一行 open 計數；KPI
  `followups` 區塊。
- **flags**：新模組 `paulsha_hippo/runtime_flags.py` 從 canonical `config.yaml` best-effort 讀
  `shortlist.collapse_same_topic`、`shortlist.read_hint`、`followups.enabled`、`episodic_filter`，
  缺鍵一律預設；`atomizer.yaml` 模板同步加鍵；不需 config migration。
- **治理**：五個 `changelog.d/hygiene-*.md` 碎片；`README.md` 日常命令同步（R-16）；
  `python3 -m pytest tests/ -q`、`python3 -m policy_check --repo .`、`openspec validate --all --strict`
  全綠。

## Impact

- 影響範圍：SessionEnd hooks（薄腳本、需 `hippo install hooks` 重新部署）、importer／atomizer／janitor
  的 provenance 傳遞、shortlist 呈現層、新 CLI 子命令（`show`、`followups`、`knowledge
  backfill-provenance|link-supersedes|mark-episodic`）、dream 可選階段、wakeup brief 一行、KPI 報表。
  不動 `search()` 排序、不動 `classify_noise`、不動 Stage 3 REQUIRED frontmatter schema、不動 PHASES／
  ARTIFACT_KINDS（paulsha-cortex 唯一耦合點）。
- 資料變更：全部走一次性 migration，`--dry-run` 預設、`--apply` 走 `frontmatter_io.update()`
  （parse-equivalent、body 逐位元不變）、冪等；live 記憶庫只在使用者看過 dry-run 報表後才 apply。
- 已鎖定的決策（2026-08-25）：`families` opt-in 加；不做 sidecar 拆檔；episodic 降層保留不刪；
  follow-ups 出現在 wakeup brief 不進 per-prompt shortlist；backfill 近似 commit 寫入並標記
  `backfill-approx`；**TTL 維持預設 90 天、不加 override**（任何真實 janitor scan 前先 dry-run 保留
  報表）。
- 範圍外：3a sidecar 拆檔、fix 6（`kind`／prompt 型別過濾／`verify:`）——列入 later。
- Authority：`docs/superpowers/plans/2026-08-25-issue-136-knowledge-hygiene-plan.md`、
  `docs/superpowers/specs/2026-08-25-issue-136-knowledge-hygiene-{design,spec}.md`。
- Issue：[#136](https://github.com/hamanpaul/paulsha-hippo/issues/136)；分支 `feature/136-knowledge-hygiene`；PR body 以 `Closes #136` 關閉（R-17）。
