---
status: accepted
work_item: issue-105-proposal-soft-repair
---

# Issue #105 Proposal 未知欄位 soft-repair 提案

## Why

`claude-code:805cf9bf-2def-4d50-b643-00762079346c` 這輪（56 fragments，正常量級）claude 有實際跑完並回傳，但 proposal 13 帶了一個 schema 不認得的欄位 `tags2`。`_build_proposal`（`paulsha_hippo/atomizer/llm_output.py:218-220`）目前把「未知欄位」判定為 hard-field violation，直接讓**整份回應**（含其餘 N 個合法 proposal）一起 `LlmOutputError` 失效；往下 fallback 到 codex 也 exit 1 失敗，整輪 session park，並觸發 AR-11 soak 窗口重置（issue #105 comment 佐證：`~/.agents/memory/runtime/ledger/processing.jsonl` 2026-07-31T10:00:33Z）。

`_build_proposal` 既有設計哲學（docstring）：「hard-field 違規讓整份回應失效；soft issue（unknown project、malformed relation）原地修復」。未知欄位目前誤放在 hard 分類，應與 `project`／`relations` 同層級歸為 soft。

程式碼內明文有一條設計不變量註解（`_parse_proposals`，L280-281）：「一個 proposal 拖垮整份回應」是刻意設計，防止 retry 非決定性遺失 findings。本提案必須顯式處理這條不變量，論證未知欄位丟棄為何不違反其目的（見 design.md D3），不得默默繞過。

## What Changes

- `paulsha_hippo/atomizer/llm_output.py::_build_proposal`：把未知欄位（unknown-key）檢查從 hard-field 清單分離為 soft violation——丟棄該欄位，記可觀測 warning（含欄位名與 proposal 序號），不再對 `_parse_proposals` raise `LlmOutputError`。
- 既有 hard violation 清單（缺 `title`、非法 `artifact_kind`、型別錯誤、重複 `title`、空 `body`、空 `source_fragment_indices` 等）維持整份判死，逐一列舉為回歸測試案例，語意一條不鬆。
- 更新 `_build_proposal` docstring 與 `_parse_proposals` 內設計不變量註解，顯式論證「未知欄位丟棄是決定性操作、不觸發 retry」為何不違反該不變量原始目的。
- 新增 `changelog.d/105-proposal-soft-repair.md`（type: fix）。

## Impact

- 影響範圍：`paulsha_hippo/atomizer/llm_output.py` 的 proposal 解析路徑（`_build_proposal`／`_parse_proposals`）；不影響頂層 canonical response schema（`schema_version`／`disposition`／`reason`）驗證，不影響 `project`／`relations` 既有 soft-repair 行為。
- 邊界（legacy `parse()` 多陣列掃描路徑）：「proposal 形狀＋多餘欄位」的陣列不再被 unknown-field raise 淘汰而成為合法競爭者，raw 同時含真 proposal 陣列與此類陣列時會改判 `multiple valid JSON arrays found`；掃描淘汰非 proposal 陣列時也可能先記一筆 unknown-field warning。僅及 legacy／測試面，生產路徑只走 `parse_response`（嚴格單一 JSON value）不受影響——詳見 design.md D1 邊界記載。
- 風險：低——只新增一個 soft 分支並收斂哪些欄位可被裁剪，既有 hard violation 判定範圍不變、有逐條回歸測試鎖定。
- 生產效益：避免單一 proposal 的一次幻覺欄位拖垮同一回應裡其他所有 proposal，減少 session park 與 AR-11 soak 窗口重置的誤觸發。
- Authority：`docs/superpowers/plans/2026-08-04-issue-105-proposal-soft-repair.md`、spec/design 同名對（`docs/superpowers/specs/2026-08-04-issue-105-proposal-soft-repair-{spec,design}.md`）、issue #105。
