---
status: accepted
work_item: issue-105-proposal-soft-repair
---

# Proposal 未知欄位 soft-repair（issue #105）設計

- 日期：2026-08-04
- Issue：[#105](https://github.com/hamanpaul/paulsha-hippo/issues/105)
- 狀態：已核可，待實作

## 背景

證據與 root cause 見 spec（`docs/superpowers/specs/2026-08-04-issue-105-proposal-soft-repair-spec.md`）。本設計要回答兩個問題：(1) 未知欄位該怎麼分級處理，(2) 既有的「一個 proposal 拖垮整份回應」設計不變量註解要不要動、怎麼動。

`paulsha_hippo/atomizer/llm_output.py::_build_proposal` 目前的分工（docstring 原文）：

> Build one proposal. Hard-field violations fail the whole response; soft issues (unknown project, malformed relation) are repaired in place.

`project`（L240-247）與 `relations`（L249-256）已經是 soft：不合法時 coerce 成 `_unknown` 或丟棄該筆 relation 並記 `_LOG.warning`，不影響整份回應。唯獨 unknown-field 檢查（L218-220）發生在最前面、且直接 raise，尚未納入這個 soft 分工。

`_parse_proposals`（L280-281）內的既有註解：

```python
# A canonical response is one transaction.  Publishing only the valid
# subset would make retries non-deterministic and silently lose findings.
proposals.append(_build_proposal(item, index, allowed_projects, seen_titles))
```

## Decisions

### D1：未知欄位降級為 soft violation——丟棄該欄位，記 warning，不整份判死

`_build_proposal` 把 unknown-key 檢查從「直接 raise」改為「丟棄該欄位（或剔除該筆 proposal，實作時二擇一並在 PR 說明取捨），記 `_LOG.warning`，繼續驗證該 proposal 其餘欄位」。判斷依據：未知欄位是模型多產出的雜訊（如打錯欄位名、多寫一個 schema 沒有的欄位），與 `project`／`relations` 既有的 soft-repair 性質相同——都是「這一小塊不認得，丟掉即可，不影響其餘欄位語意」，不像缺 `title`／型別錯誤那樣代表整筆 proposal 的核心資料不可信。

實作路徑：在 unknown-key 檢查處，不再 `raise`，而是對 `unknown_keys` 逐一 `_LOG.warning`（含欄位名與 proposal 序號），並在後續欄位存取前先從 `item` 的有效鍵集合中剔除這些未知鍵（或等價地忽略，因為既有欄位存取本就用 `item.get(...)`／`_require_field` 只讀認得的 key，未知鍵本來就不會被讀取——真正需要動的只有「不 raise」與「加 warning」這兩步）。

### D2：既有 hard violation 清單逐條保留，整份判死語意不變

以下情形維持現狀，任一命中仍讓 `_parse_proposals` 對整份回應 raise `LlmOutputError`，不得降級：

- `item` 本身不是 dict（`proposal {index} is not an object`）。
- `artifact_kind` 不在 `ARTIFACT_KINDS`。
- `title` 缺漏／空字串／重複。
- `body` 缺漏／非字串／空字串。
- `tags` 缺漏／非 list／元素非字串。
- `source_fragment_indices` 缺漏／非 list／元素非 int／空 list。
- `relations` 欄位本身缺漏或非 list（注意：這是「`relations` 這個欄位存在與否、型別對不對」的檢查，跟 D1 的「單筆 relation 內容是否合法」是兩層——後者本來就是 soft，前者仍是 hard）。

這條清單直接對應 spec 的 G3／Acceptance，且是 tasks.md 「不變量保護」測試要逐一覆蓋的案例集。

### D3：不變量註解的更新論證——未知欄位丟棄是決定性操作、不產生 retry 非決定性

`_parse_proposals` 現有註解主張「只發佈有效子集會讓 retry 變得非決定性、悄悄遺失 findings」，因此設計為一個 proposal 壞掉、整份回應失效、觸發 retry（由外層 fallback/重跑機制決定下一步）。這條不變量的真正目的是：**不要在同一份回應裡「選擇性發佈」，因為選誰、不選誰若涉及重跑會產生不同結果，讓同一輸入在不同次執行下產出不同的已發佈集合。**

未知欄位丟棄不觸碰這個目的，理由：

1. **丟棄未知欄位是對單一 proposal 內部欄位集合的裁剪，不是「選擇性發佈哪些 proposal」。** 該 proposal 本身仍然完整產出並計入本次回應的 `findings`；被丟棄的只是它裡面一個 schema 不認得的欄位，等同 `project`／`relations` 既有的 soft-repair——兩者都已經在裁剪欄位內容而不影響「這個 proposal 算不算被發佈」。
2. **這是決定性操作，不觸發 retry。** 同一組輸入（含 `tags2`）每次跑都會被同樣丟棄、同樣記 warning、同樣的其餘欄位驗證結果，不存在「這次選了 A 沒選 B、下次選了 B 沒選 A」的分歧——不變量要防的是「retry 導致不同次選擇不同子集」，而丟棄行為本身是純函數、與 retry 無關。
3. **N 個合法 proposal 仍然一個不少地整批發佈或整批不發佈**——soft-repair 只改變「單一 proposal 內部欄位集合」，不改變「本次回應的 proposal 集合是否整批成立」。真正的 hard violation（D2 清單）一旦命中，仍然是整份回應失效、交由外層 retry/fallback 處理，不變量在這條路徑上完全未動。

結論：不變量的保護範圍（「不得對回應本身做選擇性子集發佈」）維持不變，本次修法只是把「未知欄位」從誤放的 hard 分類移回它本該屬於的 soft 分類，與既有 `project`／`relations` soft-repair 同層級。`_build_proposal` docstring 與 `_parse_proposals` 註解需同步更新，明文寫出這個分界理由，避免未來讀者誤以為修法繞過了不變量。

## Testing

- 新增：N 合法 + 1 帶未知欄位（如 `tags2`）不再整份 `LlmOutputError`，其餘 proposal 正常產出。
- 新增（不變量保護）：D2 清單逐條測試，仍整份判死。
- 新增：warning 內容含欄位名與 proposal 序號，可觀測。
- 既有：`tests/test_llm_output.py` 全套綠，`project`／`relations` 既有 soft-repair 行為零回歸。
