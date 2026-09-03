## ADDED Requirements

### Requirement: SessionEnd 擷取時填實 provenance commit 與 commit_source

三支 SessionEnd 擷取 hook（claude／codex／copilot）SHALL 在寫 queue payload 前，對 `payload["cwd"]` best-effort 取得 `git rev-parse HEAD`、`git rev-parse --abbrev-ref HEAD`、`git status --porcelain` 並寫入 payload `commit`（40-hex）、`git_branch`、`git_dirty`（bool）；僅在 cwd 為 git repo 且 payload 原本沒有該鍵時寫入。探測 MUST 為 stdlib-only、inline 於各 hook（維持複製部署可獨立執行）、每個子程序 timeout ≤ 2 s；`git` 不存在、非 repo、timeout 或任何例外時 MUST NOT 寫入該鍵、MUST NOT 阻斷擷取，且 exit 0。importer SHALL 在 payload 無 `commit` 時以 `_git.git_head(discovered_toplevel)` 補值；inbox frontmatter `provenance:` 區塊在取得 commit 時 SHALL 含 `commit_source ∈ {hook, import-discovery, backfill-approx}` 標明來源；取不到 commit 時 `commit_source` SHALL 缺省省略（MUST NOT 偽造來源），`branch`／`dirty` 同樣缺省省略。provenance 子鍵一律字串（`dirty ∈ {"true","false"}`），共六鍵 `repo / commit / path / commit_source / branch / dirty`；atomizer fragment 渲染／讀回、`slice_frontmatter.render` 與 janitor `record_source` SHALL 完整傳遞六鍵，且 MUST 仍可讀取只含 `repo / commit / path` 三鍵的既存 note。

#### Scenario: git repo 內的 session 帶 commit
- **WHEN** SessionEnd hook 的 `payload["cwd"]` 是一個 git repo
- **THEN** queue payload SHALL 含 40-hex `commit`、`git_branch`、`git_dirty`，importer 產出的 inbox frontmatter `provenance.commit` SHALL 等於該 HEAD 且 `commit_source: hook`

#### Scenario: 非 repo 或 git 不可用時不寫鍵且不阻斷
- **WHEN** cwd 不是 git repo、`git` 不在 PATH、或子程序超過 2 s
- **THEN** payload MUST NOT 含 `commit`／`git_branch`／`git_dirty`，hook SHALL exit 0 且擷取照常完成

#### Scenario: importer fallback 補值並標記來源
- **WHEN** queue payload 沒有 `commit` 但 `cwd` 可解析出 git toplevel
- **THEN** inbox frontmatter `provenance.commit` SHALL 為該 toplevel 的 HEAD，且 `commit_source: import-discovery`

#### Scenario: 六鍵 provenance 經 fragment 與 slice 往返不遺失
- **WHEN** 含六鍵 provenance 的 inbox 經 atomizer `_split_pass` 渲染 fragment、再由 `_read_fragment` 讀回並發布為 knowledge slice
- **THEN** slice frontmatter `provenance` SHALL 含相同六鍵值；只含三鍵的既存 fragment／slice SHALL 仍可讀且不報錯

### Requirement: 知識 slice 的結構化引用 cites

atomizer SHALL 在發布前對 slice body 以 `atomizer/slice_frontmatter.CITE_RE`（`(?<![\w/.-])([\w./-]+\.(?:md|py|c|h|cpp|hpp|lds|syscfg|yml|yaml|sh|json|toml|cmake|txt)):(\d{1,6})\b`——negative lookbehind 排除 URL host:port 與更長路徑片段，副檔名白名單排除 `12:30` 之類時間戳）抽取 `path:line` 引用，保序去重、上限 32 筆，寫入 frontmatter `cites: [{path, line}]`。抽取 MUST 為純字串比對、MUST NOT 檢查路徑存在性。`cites` 為 optional 欄位：`validate()` 對缺 `cites` 的既存 note SHALL 仍通過；`cites` MUST NOT 進入 Stage 3 REQUIRED schema。

#### Scenario: body 含多處引用時保序去重且封頂
- **WHEN** slice body 含 `README-ARC.md:108`、`src/CC2674.syscfg:131`、再次出現 `README-ARC.md:108`，共 40 個相異引用
- **THEN** frontmatter `cites` SHALL 依首次出現順序列出不重複引用且長度為 32

#### Scenario: 無引用或既存 note 缺 cites 仍有效
- **WHEN** body 無任何 `path:line`，或既存 knowledge note 的 frontmatter 沒有 `cites`
- **THEN** 新 note SHALL 不寫 `cites`（或寫空 list），既存 note 經 `validate()` SHALL 通過

### Requirement: provenance 一次性回填 migration backfill-provenance

系統 SHALL 提供 `hippo knowledge backfill-provenance --memory-root <root> [--dry-run|--apply] [--project <slug>]`：對 `memory_layer == "knowledge"` 且 `provenance.commit == "_unknown"` 的 slice，讀取 `provenance.path` 指向的 archive queue JSON 取 `cwd` 與 `ended_at`，以 `git -C <cwd> rev-list -1 --before=<ended_at> HEAD` 取得近似 commit 寫入並標 `commit_source: backfill-approx`；cwd 不存在或非 repo 時 SHALL 維持 `_unknown` 並在報表列出原因；同時對 body 跑 cites 抽取回填。`--dry-run`（預設）SHALL 只輸出報表（可填數／不可填原因分佈／cites 命中數）且 MUST NOT 修改任何檔案；`--apply` SHALL 走 `frontmatter_io.update()`（parse-equivalent、body 逐位元不變）；migration SHALL 冪等：apply 後 dry-run 回報 0、再次 apply 為 no-op 且 bytes 不變。

#### Scenario: dry-run 報表不動檔案
- **WHEN** memory root 內有 3 筆 `_unknown` slice（其一 archive JSON 的 cwd 不存在）執行 `--dry-run`
- **THEN** 報表 SHALL 列 2 筆可填、1 筆不可填及其原因，且所有檔案 bytes 逐位元不變

#### Scenario: apply 標記近似來源並冪等
- **WHEN** 對同一 root 執行 `--apply`
- **THEN** 2 筆 slice 的 `provenance.commit` SHALL 為 40-hex 且 `commit_source: backfill-approx`，body 逐位元不變；重跑 `--dry-run` SHALL 回報 0，再次 `--apply` SHALL 為 no-op

### Requirement: 跨 session supersedes 連結（即時與 link-supersedes 回填）

atomizer 的 `_attach_unambiguous_supersedes` SHALL 放寬為跨 session：同 project（或同 family）、canonical title 或 aliases 完全相等、`captured_at` 不早於（`recency_key(old) <= recency_key(new)`——同 session 重蒸餾共用時間戳者亦可連結）、checksum 不同的既存 active slice SHALL 被新 slice `supersedes`；即時路徑不區分 session（等時戳的跨 session 配對若同時滿足上述條件且唯一，亦會連結）；未被即時路徑連結的跨 session 等時戳配對，由 `link-supersedes` 依 `distilled_from` 分流（同 session 跳過、不同 session 進 review tier 送人核可），並 `relations.append_edge(type="supersedes")`。系統 SHALL 提供 `hippo knowledge link-supersedes --memory-root <root> [--tier auto|review] [--dry-run|--apply] [--accept <report>]`：`auto` tier 以上述條件連結（同一時間戳或多對一 ≥ 2 舊筆 MUST 留在 review）；`review` tier 對 token Jaccard ≥ 0.6（token ≥ 4）或 tags 交集 ≥ 2 或 `related` 互指者只輸出報表 `runtime/reports/link-supersedes-<ts>.md`（新／舊 slice_id、標題、captured_at、共同 token），MUST NOT 自動寫入，使用者勾選後以 `--accept` 套用。migration SHALL 冪等：已 `supersedes` 指向同一舊筆 → no-op；舊筆已 decayed → 跳過。下游 SHALL 沿用既有 janitor `decay_superseded` → lifecycle `decayed` → `search(include_decayed=False)` 路徑，MUST NOT 新增第二套 decay。沒有 family 設定時，不同 project 的 slice MUST NOT 被連結。

#### Scenario: 跨 session 同標題即時 supersedes
- **WHEN** 新 session 蒸餾出與既存 active slice 同 project、canonical title 相同、checksum 不同且較新的 slice
- **THEN** 新 slice frontmatter `supersedes` SHALL 含舊 slice_id，relations ledger SHALL 多一筆 `supersedes` edge，janitor 下一輪 SHALL 對舊筆產生 `superseded` decay 事件

#### Scenario: 多對一與近似標題只進 review
- **WHEN** 一筆新 slice 對應 2 筆同標題舊筆，或兩筆標題僅 Jaccard 0.7 相似
- **THEN** `--tier auto --apply` MUST NOT 寫入任一 `supersedes`，`--tier review` 報表 SHALL 列出該對候選

#### Scenario: 跨 project 且無 family 不連結
- **WHEN** `ot-ti-mirror` 的「Flash Layout and Image Artifacts」與 `MCU-Octopus` 的「雙目標 Flash Layout」且 `projects.yaml` 無 `families:`
- **THEN** auto 與 review 皆 MUST NOT 連結或列出該對；設定同 family 後 review 報表 SHALL 列出供人核可

### Requirement: session 狀態內容降為 episodic 層

系統 SHALL 提供 `noise.episodic_reason(title, body) -> str | None`（非 deletion-grade）：標題命中 strong 措辭（`session-handoff…`｜`session 交接`｜`交接狀態`｜`handoff (status|state|note)` ＋分隔符或行尾…）時單獨即回傳原因；標題只命中 weak 形狀（裸 `handoff`｜以「狀態」結尾）時 SHALL 另需 body 至少一行 strong 命中才回傳；否則以散文行中 ≥ 50% 命中 session 狀態 pattern（尚未 commit｜尚未 push｜session 結束｜本次修改僅限｜目前狀態｜handoff｜待 push｜下一步）時回傳原因；`classify_noise` 與 `knowledge prune-noise` 的判定 MUST NOT 因此改變。atomizer 在 `episodic_filter`（預設 true）開啟時 SHALL 於 supersedes 之後、validate 之前把命中的 slice `memory_layer` 改為 `episodic` 並寫 `episodic_reason`，照常發布到 `knowledge/<project>/`（檔案保留、可稽核）；`slice_frontmatter.validate` SHALL 接受 `memory_layer ∈ {knowledge, episodic}`；`build_index` 與 MOC builder SHALL 以既有 non-knowledge-layer 分支排除 episodic。atomize skill 的 CONCEPT_ANALYSIS SHALL 明文排除 session 進度／狀態陳述，整個候選只剩狀態陳述時不產生 slice。系統 SHALL 提供 `hippo knowledge mark-episodic --memory-root <root> [--dry-run|--apply|--revert <slice_id>]`：dry-run 列候選（title／命中行／比例）不動檔案；apply 以 `frontmatter_io.update()` 降層並 `lifecycle.append_event(event_type="archived", reason="episodic")`；已 episodic → no-op；`--revert` SHALL 還原 `memory_layer: knowledge` 並移除 `episodic_reason`。

#### Scenario: 狀態句過半的 proposal 發布為 episodic
- **WHEN** 蒸餾 proposal 的散文行 60% 為「尚未 commit／session 結束時」類句子
- **THEN** 發布檔案 `memory_layer: episodic` 且含 `episodic_reason`，`validate()` 通過，`build_index` coverage 記 `pool_excluded[non-knowledge-layer:episodic]`，shortlist MUST NOT 撈到該筆

#### Scenario: 含實質步驟者不降層且 classify_noise 不變
- **WHEN** body 狀態句僅 30% 且其餘為可重用步驟
- **THEN** `episodic_reason` SHALL 回 None，slice 以 `memory_layer: knowledge` 發布；對同一 body `classify_noise().is_noise` SHALL 維持 False

#### Scenario: mark-episodic dry-run／apply／revert／冪等
- **WHEN** 對含 2 筆命中 note 的 root 依序執行 `--dry-run`、`--apply`、`--dry-run`、`--apply`、`--revert <id>`
- **THEN** 第一次 dry-run 列 2 筆且 bytes 不變；apply 後 2 筆為 episodic 且 lifecycle 多 2 筆 `archived/episodic`；第二次 dry-run 回報 0、第二次 apply 為 no-op；revert 後該筆 `memory_layer: knowledge` 且無 `episodic_reason`

### Requirement: follow-up ledger 與唯讀驗證

系統 SHALL 從 knowledge slice body 以純 regex 抽取 follow-up：某行含可行動 pattern（需要更新｜需加｜缺口｜TODO｜should be updated｜尚未修｜需修正｜待補）且同行或前後一行含 `path:line` 時，產生 `{id: "fu-<sha16>", slice_id, project, target: {path, line}, expected_stale, claim, status, created_at, source: "regex"}`，`expected_stale` 取同行第一個數字或反引號字串（無則 null）；無 `path:line` 者亦記但 `target: null`。`id` SHALL 由（slice_id, target, claim）hash 派生，重跑 MUST NOT 重複 `opened`。儲存為 append-only `runtime/ledger/followups.jsonl`，狀態以事件 fold：`opened / verified-open / resolved-in-source / closed-manual / unverifiable`，其中僅 `resolved-in-source` 與 `closed-manual` 為終局。系統 SHALL 提供 `hippo followups list [--project] [--status]`、`verify [--project]`、`close <id> --reason`、`extract [--dry-run|--apply]`；`verify` SHALL 以手寫 `projects.yaml` ∪ generated project registry（`project-hippo.yaml`）的 roots 為 base（或絕對路徑）唯讀讀取 `target` 行 ±2 行：`expected_stale` 仍在 ⇒ `verified-open`，不在 ⇒ `resolved-in-source`，檔案或行不存在 ⇒ `unverifiable`；`verify` MUST NOT 修改任何 repo 檔案。`target: null` 者沒有可讀取的 `file:line`，`verify` SHALL 跳過該筆（維持既有狀態、不計入 `checked`、MUST NOT 產生事件）——它仍以 `opened` 計入未解決項目，只能人手 `close`。`unverifiable` SHALL 為可重查狀態（原因消失即可回到 `verified-open`／`resolved-in-source`），並計入未解決項目；同一筆重查得到相同 `unverifiable` 原因時 MUST NOT 追加事件。`followups.enabled`（預設 true）關閉時 extract／verify／dream 階段 SHALL 為 no-op。

#### Scenario: README-ARC.md:108 端到端
- **WHEN** 某 slice body 有「README-ARC.md 記載的舊 FLASH 數字 `133,604 B` 已與 fresh build 不符，需要更新（`README-ARC.md:108`）」，執行 `extract --apply`，其後 roots 下該檔第 109 行仍含 `133,604`，再執行 `verify`
- **THEN** ledger SHALL 有一筆 target `README-ARC.md:108`、`expected_stale: "133,604"` 的 `opened`，verify 後 fold 狀態為 `verified-open`；使用者修檔後再 verify SHALL 轉 `resolved-in-source`

#### Scenario: verify 唯讀且四種狀態轉換
- **WHEN** fixture repo 分別為 stale 值仍在／已移除／檔案不存在／行號漂移 2 行內
- **THEN** 狀態 SHALL 分別為 `verified-open`／`resolved-in-source`／`unverifiable`／`verified-open`，且 fixture 檔案 mtime 與內容不變

#### Scenario: 冪等抽取
- **WHEN** 對同一 root 連續執行兩次 `extract --apply`
- **THEN** 第二次 MUST NOT 新增任何 `opened` 事件

### Requirement: janitor check_provenance_commit 語意

janitor `source_checks.check_provenance_commit`（既有 flag，預設 false）為 true 時，`_decide_decay` SHALL 在 `superseded` 之後、`ttl_expired` 之前檢查 `provenance.commit`：commit 為 40-hex 且在 `projects.yaml` roots 對應 repo 中 `git cat-file -e <commit>` 失敗 ⇒ `source_invalid`（detail `{"check": "provenance_commit"}`）；`_unknown` 或 `backfill-approx` 來源的 commit MUST NOT 觸發；flag 為 false 時 MUST NOT 產生任何事件，行為與現行一致。dream 服務 SHALL 傳入 no-op 檢查器（不在 dream 內探 repo）。

#### Scenario: flag 開啟時 dangling commit 觸發 source_invalid
- **WHEN** override 設 `check_provenance_commit: true`，某 active record 的 `provenance.commit` 在對應 repo 不存在
- **THEN** scan plan SHALL 含該 record 的 `decayed`，reason `source_invalid`、detail check `provenance_commit`

#### Scenario: flag 關閉或近似 commit 不觸發
- **WHEN** flag 為 false，或 record `commit_source: backfill-approx`
- **THEN** scan MUST NOT 因 provenance commit 產生任何 decay 事件

### Requirement: hygiene runtime flags 單一讀取點

系統 SHALL 提供 `paulsha_hippo/runtime_flags.py`，從 canonical `config.yaml`（`paths.atomizer_config_path()`）best-effort 讀取 `shortlist.collapse_same_topic`（預設 true）、`shortlist.read_hint`（預設 `show`，值域 `show|read`）、`followups.enabled`（預設 true）；`episodic_filter`（預設 true）由 `atomizer/config.py::load_config` 自行讀取（`AtomizerConfig.episodic_filter`），MUST NOT 在 `runtime_flags` 再存一份無讀取端的副本；檔案不存在、鍵缺失、型別錯誤或任何例外時 SHALL 回傳預設值，MUST NOT 拋出例外。`atomizer/atomizer.yaml` 模板 SHALL 同步含這些鍵；`atomizer/config.py::load_config` 對未知頂層鍵 MUST NOT 失敗，因此不需 config migration。

#### Scenario: 缺鍵與壞值回預設
- **WHEN** `config.yaml` 不存在，或 `shortlist.read_hint: 42`
- **THEN** flags SHALL 分別為全部預設值／`read_hint == "show"`，且不拋例外

#### Scenario: 顯式設定生效
- **WHEN** `config.yaml` 設 `shortlist: {collapse_same_topic: false}`、`episodic_filter: false`
- **THEN** shortlist MUST NOT 折疊同主題，atomizer MUST NOT 降層 episodic

## MODIFIED Requirements

### Requirement: Knowledge slice frontmatter union contract

Stage 2 SHALL produce knowledge slices whose frontmatter is the union of the Topic 4 janitor read contract and the Stage 3 frontmatter schema, plus a Stage-2-owned `tags` field. Each slice frontmatter MUST pass `paulshaclaw.lifecycle.schema.validate_frontmatter` and MUST also expose the Topic 4 fields (`memory_layer`, `source_agent`, `captured_at`, `provenance`, `supersedes`), where `memory_layer` MUST be `knowledge` or — for session-state content demoted by the episodic filter — `episodic`. The `provenance` block MAY carry the string sub-keys `repo / commit / path / commit_source / branch / dirty`; readers MUST accept blocks that only carry `repo / commit / path`. The optional Stage-2-owned fields `cites` (ordered list of `{path, line}`) and `episodic_reason` (string) MAY be present. The atomizer MUST assign `slice_id`, `artifact_kind`, `checksum`, and `supersedes` deterministically and MUST NOT extend or redefine the Stage 3 required frontmatter schema. `checksum` MUST equal `sha256(slice body)`.

#### Scenario: Slice passes Stage 3 gate

- **WHEN** a knowledge slice produced by the atomizer is fed to `python3 -m paulshaclaw.lifecycle.gate`
- **THEN** validation MUST pass

#### Scenario: Invalid slice fails closed

- **WHEN** a fragment cannot be mapped to a valid `artifact_kind` and a slice would fail frontmatter validation
- **THEN** the slice MUST NOT be written to `knowledge/`
- **THEN** the session MUST remain in `state=split` and a warning MUST be logged

#### Scenario: Optional hygiene fields do not break the gate

- **WHEN** a slice carries `memory_layer: episodic`, a six-key `provenance` block, `cites`, and `episodic_reason`
- **THEN** `slice_frontmatter.validate` and the Stage 3 gate MUST pass, and a slice lacking all of these optional fields MUST also pass

### Requirement: Dream orchestration service

Stage 2 SHALL provide an independent `dream` service that orchestrates the existing per-pass components rather than reimplementing them. `psc memory dream run` MUST execute the Topic 3/3.2 atomize pass and then the Topic 4 janitor pass over the current backlog, in that order, and MAY execute additional isolated passes after janitor (`moc`, and — when `followups.enabled` — `followups`). The `followups` pass MUST be read-only against project repositories and append-only to `runtime/ledger/followups.jsonl`; its failure MUST be recorded in the run record (`summary.error`) and MUST NOT change the run `status` grade. The service MUST record one run record to `runtime/ledger/dream.jsonl`. Passes MUST be isolated: a failure in one pass MUST be recorded and MUST NOT prevent the other passes from running or crash the run. The service MUST be separate from the ingestion pipeline and MUST be triggered by a scheduler, never by the importer.

#### Scenario: Passes run in order and are isolated

- **WHEN** `dream run` executes and the atomize pass raises
- **THEN** the janitor pass MUST still run
- **THEN** the run record MUST capture the atomize error and a degraded `status`

#### Scenario: Run is recorded

- **WHEN** a non-dry-run `dream run` completes
- **THEN** `runtime/ledger/dream.jsonl` MUST gain one record with `run_id`, `status` (`ok`/`partial`/`failed`), per-pass summaries, and `dream_config_hash`

#### Scenario: Dry run does not mutate state

- **WHEN** `dream run --dry-run` executes
- **THEN** both passes MUST run in dry mode and no `dream.jsonl` record MUST be written

#### Scenario: Follow-ups pass failure never degrades the run

- **WHEN** the `followups` pass raises while atomize and janitor succeed
- **THEN** the run record `status` MUST be `ok`, the follow-ups summary MUST carry `error`, and no repository file MUST have been modified
