---
status: accepted
work_item: issue-105-proposal-soft-repair
---

# Proposal 未知欄位 soft-repair（issue #105）規格

## Problem and Outcome

`claude-code:805cf9bf-2def-4d50-b643-00762079346c` 這輪（56 fragments，正常量級）claude 有實際跑完並回傳，但 proposal 13 帶了一個 schema 不認得的欄位 `tags2`。`_build_proposal`（`paulsha_hippo/atomizer/llm_output.py:218-220`）目前把「未知欄位」歸類為 hard-field violation：

```python
unknown_keys = sorted(set(item) - _PROPOSAL_KEYS)
if unknown_keys:
    raise LlmOutputError(f"proposal {index} has unknown fields: {', '.join(unknown_keys)}")
```

這個 raise 發生在 `_parse_proposals`（L270-283）的迴圈裡，一個 proposal 觸發即整份 `LlmOutputError`，導致同一回應裡其他 N 個合法 proposal 全部陪葬；往下四層 fallback（claude → codex → …）跟著耗盡，整輪 session park。

2026-07-31T10:00:33Z 生產實證（`~/.agents/memory/runtime/ledger/processing.jsonl`）：

```
error: llm promote failed after session attempt(s): external agent fallback exhausted
(process, exit 4): claude proposal 13 has unknown fields: tags2 | codex exit 1 ...
```

此輪 park 直接觸發 AR-11 soak 窗口重置（reset 前已累積 1/3 合格輪）。與 #99／#74 同屬「單一 session 打穿四層 fallback、觸發 AR-11 soak 窗口重置」症狀族，但本 issue 根因層在 claude 輸出驗證的容錯設計，不是 payload 過大。

`tags2` 究竟是模型把 `tags` 打錯欄位名、還是在 `tags` 之外多產生一個欄位，現有程式碼路徑無法區分：unknown-key 檢查（L218）發生在其他欄位驗證之前，一撞到就直接 raise，不會繼續往下看 `tags` 本身是否合法存在。是否與同日落地的 #103（tags 正規化）有 prompt/schema 不同步的關聯，目前只是時間點巧合觀察，未證實因果，本次修法不依賴此假設。

預期結果：proposal 帶未知欄位時，該欄位被丟棄（或該 proposal 被剔除）並記可觀測 warning（含欄位名與 proposal 序號），其餘合法 proposal 正常產出、不再整份判死；已存在的 hard violation（缺 `title`、非法 `artifact_kind`、型別錯誤等）語意一條不能鬆——仍整份 `LlmOutputError`。

## Goals

- G1：`_build_proposal` 把「未知欄位」從 hard-field 清單分離為 soft violation——丟棄該欄位（或剔除該 proposal），不再讓 `_parse_proposals` 對整份回應 raise `LlmOutputError`。
- G2：Soft-repair 產生可觀測 warning，內容含被丟棄的欄位名與 proposal 序號（呼應既有 `project`／`relations` soft-repair 的 log 慣例）。
- G3：既有 hard violation 語意零回歸——`_require_field`／`_require_non_empty_string`／`_require_list`／`_require_string_list`／`_require_int_list`、`artifact_kind` 檢查、`title` 重複檢查、`body` 空值檢查等仍整份判死，逐一列舉為測試案例。
- G4：更新 `_build_proposal` docstring 與 `_parse_proposals` 內「一個 proposal 拖垮整份回應」的設計不變量註解，顯式論證縮小失效範圍（未知欄位丟棄）為何不違反其原始目的（見 design.md D3）。

## Non-goals

- 不放寬既有 hard violation 判定範圍（缺 `title`、非法 `artifact_kind`、型別錯誤、重複 `title`、空 `body`、空 `source_fragment_indices` 等）。
- 不改動 `project`／`relations` 既有的 soft-repair 行為（保持現狀，僅新增 unknown-field 分支）。
- 不改 canonical response 頂層 schema（`schema_version`／`disposition`／`reason`／`findings`）驗證，本次僅限單一 proposal 內部的欄位驗證分界。

## Acceptance

- N 個合法 proposal + 1 個帶未知欄位（如 `tags2`）的回應：`_parse_proposals` 不拋 `LlmOutputError`，未知欄位被丟棄（或該 proposal 被剔除），其餘 N 個 proposal 正常產出。
- 既有 hard violation 測試（缺 `title`、非法 `artifact_kind`、型別錯誤等）逐一列舉，仍整份判死。
- Warning 內容含被丟棄的欄位名與 proposal 序號，可在測試中以 caplog／mock logger 觀測到。
- 全套 `python3 -m pytest tests/ -q`、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。
