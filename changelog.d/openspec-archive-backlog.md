---
type: chore
---
- 歸檔 9 個已完成但未收尾的 openspec change：`issue-41-usage-feedback-loop-v5-sonnet`、`issue-64-ledger-torn-line-repair`、`issue-74-local-harness-input-slicing`、`issue-80-atomize-chunk-budget`、`issue-98-search-retrieval-schema`、`issue-105-proposal-soft-repair`、`issue-106-router-skipped-profile-provenance`、`issue-109-normalize-tags-migration`、`issue-136-knowledge-hygiene`。對應 issue 皆已關閉，程式碼亦已在 main（例如 `agent_profiles.py::FIXED_PER_CHUNK_DEADLINE_SECONDS`、`runtime_flags.py`、`topic.py` 均存在），僅 `openspec archive` 這步未執行，導致 10 個 active change 中有 9 個是殘留。依 Cortex lifecycle，active openspec change 本身即 authority source，殘留會讓已完成工作持續被視為可 claim 的 `todo`。
- 經由 `openspec archive` 執行（非手動搬移目錄），故 change 的 spec delta 已一併合併進主 specs：新建 `stage2-memory-usage-feedback`，並更新 `atomization-release-integrity`、`ledger-integrity`、`stage2-llm-distillation`、`stage2-memory-governance`、`stage2-memory-prompt-retrieval`、`stage2-memory-read-attribution`，合計 7 檔、+444/-13 行。`openspec validate --all --strict` 14 項全數通過。
- `.cortex/work-items.yaml` 清除 8 筆已完成條目（`issue-34`／`64`／`74`／`80`／`98`／`105`／`106`／`109`），僅保留唯一仍 open 的 `issue-99-cg-max-session-chunks`，並補上其先前缺漏的 `openspec` 與 proposal 路徑連結。原檔另有 4 個懸空路徑引用（`issue-34` proposal 已在 archive、`issue-98`／`74`／`106` 的 plan 檔已不存在），一併隨條目移除。
- 未動 `issue-99-cg-max-session-chunks`：其 issue 仍為 open，change 維持 active。
- 註記：`issue-64` tasks.md 第 7 節（備份 `import.jsonl`、dry-run、`--apply` 修復 line 230、`hippo requeue` 兩個 parked session、`hippo dream run` 覆核）為實機操作步驟，無法由 repo 內容判定是否已執行；`issue-80`／`issue-136` 的 tasks.md 亦為 0 勾選但程式碼已落地，即 task ledger 未隨 ship 回勾。本次僅歸檔，未回填勾選狀態。
