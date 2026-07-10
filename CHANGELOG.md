# Changelog

本專案所有重大變更都會記錄在此檔案。

格式基於 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-TW/1.1.0/)，
本專案遵循 hamanpaul project policy v1.0.12。

## [Unreleased]

### Added
- dream `--require-idle` 增加記憶體 headroom 閘，新增 `--min-avail-mem-pct`（預設 20%）並在低記憶體時輸出 `skipped: "low memory"` 與 `avail_pct`。
- `hippo dream supervise` 偵測 systemd dream timer 已接管時會讓位，避免與 timer 雙跑。
- 索引 coverage 六欄報表（`scanned / invalid_frontmatter / pool_excluded(by reason) / noise_excluded(by reason) / eligible / indexed`）：`build_index()` 回傳、隨索引寫入 `retrieval.db` 的 `coverage` 表（權威來源，與索引成對原子發布）並派生 `runtime/indexes/retrieval.coverage.json`；`dream run` 輸出新增 `index_coverage`。
- `hippo index verify`：三方對賬（獨立 filesystem census × coverage 報表 × index DB 反查），驗證強不變量 `indexed IDs == eligible IDs`；供 runtime 恢復序列驗證使用。DB 反查驗到實際提供搜尋結果的兩張表（slice_meta ↔ slices_fts slice_id multiset 一對一 + FTS integrity-check）——FTS 缺行／幽靈行／重複行而 metadata 完整時不再 false green。
- Project registry（#14）：importer ingest 後將已解析的 project mapping（slug/roots/remotes）寫入 generated 檔 `paulsha/project-hippo.yaml`——schema_version 1、deterministic 輸出（排序去重、逐 byte 可重現）、remote 正規化去 credential、worktree 歸併主 repo root、temp+atomic replace+固定名 lock、opt-in `project_registry.auto_write`（預設 off）、fail-open；讀取端（resolve_project 預設載入）union-read legacy `projects.yaml` 與新檔（非破壞過渡）；檔案契約文件 `docs/project-registry-contract.md` 由 producer contract test 逐 byte 錨定。

### Changed
- dream systemd timer 排程改為 `OnCalendar=hourly`，並保留 `Persistent=true`。
- dream systemd service 新增 `CPUWeight=20`、`MemoryHigh=20%`、`MemoryMax=30%`、`TasksMax=256` 的可攜 cgroup 資源上限，且不設 `CPUQuota`。

### Fixed
- `install service` 生成的 systemd unit：`ExecStart` 綁定當前 interpreter（`sys.executable`），修正 pipx / venv 隔離安裝下寫死 `/usr/bin/env python3`（全域 python）import 不到 `paulsha_hippo`、導致 dream service 一觸發即 `exit 1`（ModuleNotFoundError）的問題。
- `slugify()`/`target_name()` 檔名以 UTF-8 byte 上限（NAME_MAX 255）截斷且落在 code-point 邊界，`--<slice_id>.md` 尾段永不截斷——超長 LLM title 不再於 MOC reconcile rename 觸發 `ENAMETOOLONG` 中止整輪、導致 reindex 從未執行（#16 根因）。
- MOC naming/linker 對單一壞 slice fail-soft：記 warning 跳過該 slice，不中止整輪 MOC pass。
- `build_index()` 改 temp DB + atomic replace，廢除「先 unlink 現有 DB」：建索引中途失敗時舊索引完整保留。
- `build_index()` 並發安全：全程持 `runtime/locks/index-rebuild.lock`（阻塞式 flock）序列化所有 index writer（dream／rekey／retitle 任意呼叫路徑），temp DB 與 coverage 落盤改用 per-invocation 唯一暫存路徑——交錯的並發重建不再可能把對方未完成的索引發布成正式版。
- `build_index()` 發布視窗收口：coverage 併入同一顆 temp DB（`coverage` 表），與索引由**單次** `os.replace` 原子發布——coverage 寫入失敗（如 ENOSPC）或程序在兩步間終止，不再留下「新 DB＋舊/缺 coverage」的半發布狀態，失敗時舊索引與舊 coverage 完整保留；`retrieval.coverage.json` 改為發布成功後的派生輸出（衍生失敗僅記 warning，不推翻已發布索引），`hippo index verify` 改以 DB 內 coverage 為權威來源（無 coverage 表的舊版 DB 退回讀派生 JSON）。
- `build_index()` 對磁碟上重複 `slice_id`（naming dedup fail-soft 跳過後的殘留態）fail-soft：掃描迴圈先到先贏去重，後到者歸 `pool_excluded[duplicate-slice-id-on-disk]` 並記 warning——不再讓 `slice_meta` PK 的 `IntegrityError` 炸掉整批重建、連健康無關 slices 都退回舊索引；census 對賬鏡像同一規則（分佈對齊），`duplicate slice_id on disk` 仍由 `hippo index verify` 顯性回報。
- census 三方對賬的 fate/eligible 身份改以自身 line-based 獨立解析（`CensusEntry.slice_id/memory_layer`）為基準，並逐檔與 `fio.read` 交叉比對、任何 identity divergence 記入 problems——與 build_index 共用的 parser 誤判磁碟 ID（合法 YAML tag/anchor 如 `!!str sl-x`、或 parser bug）時，eligible 端與 DB 端不再拿到同一個錯 ID 而 false green（spec §3.2 防同源自證）。
- `build_index()` row 正規化嚴格驗證 `tags` 型別（必須為 list[str]；缺欄/null 視為空）＋逐檔分類全程包 per-slice 例外邊界：合法 YAML 的 `tags: [1]` 之類錯型歸 `invalid_frontmatter` 記路徑 warning、非預期分類例外亦只犧牲該檔——不再讓單一毒 slice 的 `TypeError` 炸掉整批重建、健康 slices 不發布或持續供應 stale index；census 雙寫同一 tags 型別規則（分佈對齊）。

## [0.1.0] - 2026-07-07

### Added
- #125 Phase 1 code 遷入：paulshaclaw `memory/**`（31k LOC、872 tests 綠）平移為 `paulsha_hippo/**`；`lifecycle` → `lib/lifecycle`、`idle` → `lib/idle`；hippo CLI 樹去 `memory` 前綴（`hippo atomize|dream|janitor|replay|bundle|search|wakeup|syncback|knowledge`）；`paths.py` 單一權威 resolver（`HIPPO_*` > `PSC_*` deprecated 警告 > config.yaml > `~/.agents/memory`）；stage2 12 份 capability specs、integration check、gemma4 wrapper（scripts/ + examples/）隨遷
- 隨遷主 repo `tests/` 下漏網的 memory 測試：policy 三件（boundary/redaction/lint/cli，54 tests）與 #218 hooks 截取自足回歸 2 件
- quickstart 面：`hippo init`（backend preset 寫入 atomizer override）、`hippo doctor`（雙 root FAIL 健檢）、`hippo install hooks|service`（systemd 偵測＋`dream supervise` fallback）、`hippo dream supervise`（前景常駐）；蒸餾三檔位——`claude-headless`（零 key）、`openai-compatible`（stdlib http-runner）、`custom-argv`（既有 agent_exec）
- repo 骨架：conventions 引擎 1.0.12（pin 5829015）+ `tier: shareable`（R-21 deident gate day-1）、package 0.1.0 與 `hippo --version` 入口、版號一致性測試、`paulsha_hippo.lib` import 隔離護欄、骨架期 README
- `paulsha_hippo.lib.session_readers`：`read_codex_rollout`/`read_copilot_history` 升格 lib API（hippo importer + paulshaclaw bro hook 兩使用者；adapters.base 保留 re-export）

### Fixed
- #7 遞迴自捕捉：agent_exec 對蒸餾子程序注入 `HIPPO_SELF_SESSION=1`，5 個 capture hook（session_end×3／precompact×2）讀到即早退（layer 1）；importer 對 prompt 內容即 atomize skill 調用文本者 `self-skip`（layer 2）
- #8 空 session 汙染：importer 對無 prompt/無 touched files/summary 空或佔位/turn≤1 的 session `empty-skip`，不寫 inbox、不入蒸餾佇列
- wheel/pipx 情境 `hippo install hooks`：repo_root 無 pyproject 時 importer venv 改複製已解包套件（原 pip install -e 必失敗）；sample yaml 隨包（config-samples/）
- wheel 安裝缺非 .py 資產（hooks install.sh／dream systemd 範本／atomizer.yaml／skills）——補 package-data 宣告；fresh-install E2E 驗證抓到
