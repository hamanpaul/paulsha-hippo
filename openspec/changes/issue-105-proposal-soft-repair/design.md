---
status: accepted
work_item: issue-105-proposal-soft-repair
---

# Issue #105 Proposal 未知欄位 soft-repair 設計

- 日期：2026-08-04
- Issue：[#105](https://github.com/hamanpaul/paulsha-hippo/issues/105)
- 狀態：已核可，待實作

## 背景

證據與 root cause 見 spec（`docs/superpowers/specs/2026-08-04-issue-105-proposal-soft-repair-spec.md`）。`paulsha_hippo/atomizer/llm_output.py::_build_proposal`（L211-267）目前把未知欄位檢查（L218-220）放在 hard-field 陣營，一撞到就 `raise LlmOutputError`，使 `_parse_proposals`（L270-283）對整份回應失效。既有 `project`（L240-247）與 `relations`（L249-256）已是 soft-repair：不合法時 coerce/丟棄並記 `_LOG.warning`，不影響整份回應。

`_parse_proposals` 內既有設計不變量註解：

```python
# A canonical response is one transaction.  Publishing only the valid
# subset would make retries non-deterministic and silently lose findings.
proposals.append(_build_proposal(item, index, allowed_projects, seen_titles))
```

## Decisions

### D1：未知欄位降級為 soft violation——丟棄該欄位，記 warning，不判死整份回應

`_build_proposal` 把 unknown-key 檢查從「直接 raise」改為：對每個未知鍵 `_LOG.warning`（含被丟棄的欄位名與 proposal 序號），丟棄後繼續驗證該 proposal 其餘（認得的）欄位。理由：未知欄位是模型多產出的雜訊（打錯欄位名／多寫一個 schema 沒有的欄位），性質與 `project`／`relations` 既有 soft-repair 相同——都是「這一小塊不認得，丟掉即可」，不像缺 `title`／型別錯誤那樣代表整筆 proposal 的核心資料不可信。既有欄位存取（`_require_field`／`item.get(...)`）本就只讀認得的 key，未知鍵原本就不會被讀取，因此本項變更的最小範圍是「不 raise + 加 warning」。

**邊界記載（legacy `parse()` 多陣列掃描路徑的候選鑑別力放寬）**：`parse()` 會掃描 raw 中所有候選 JSON 陣列並以 `_parse_proposals` 鑑別。未知欄位降級後，「proposal 形狀＋多餘欄位」的陣列不再被 unknown-field raise 淘汰而成為合法競爭者——若 raw 同時含真 proposal 陣列與一個帶額外欄位的 proposal 形狀陣列，結果會從「正常解析前者」變為 `multiple valid JSON arrays found` 整份失敗；掃描淘汰非 proposal 陣列（如 `[{"note": ...}]`）時，也可能先記一筆 unknown-field warning 再因 hard violation（如 `artifact_kind`）被淘汰。此變化僅及 legacy／測試面：生產路徑只走 `parse_response`（`llm_promoter.py` 經嚴格單一 JSON value 解析），不存在多候選競爭，故接受此邊界變化、不另設路徑開關。

### D2：hard violation 清單逐條保留，整份判死語意不變

以下情形維持現狀，任一命中仍讓 `_parse_proposals` 對整份回應 raise `LlmOutputError`：

- `item` 本身不是 dict。
- `artifact_kind` 不在 `ARTIFACT_KINDS`。
- `title` 缺漏／空字串／與既有 `seen_titles` 重複。
- `body` 缺漏／非字串／空字串。
- `tags` 缺漏／非 list／元素非字串。
- `source_fragment_indices` 缺漏／非 list／元素非 int／空 list。
- `relations` 欄位本身缺漏或非 list（欄位存在性與型別檢查，區別於 D1／既有 soft-repair 處理的「單筆 relation 內容是否合法」）。

這條清單即 tasks.md 「不變量保護」測試要逐一覆蓋的案例集，也是 spec G3／Acceptance 對應的驗收基準。

### D3：不變量註解的更新論證——未知欄位丟棄是決定性操作、不產生 retry 非決定性

`_parse_proposals` 現有註解要防的是「只發佈有效子集會讓 retry 變得非決定性、悄悄遺失 findings」——即不得對回應本身做選擇性子集發佈，因為選誰、不選誰若牽涉重跑會讓同一輸入在不同次執行下產出不同的已發佈集合。

未知欄位丟棄不觸碰這個目的：

1. **這是對單一 proposal 內部欄位集合的裁剪，不是「選擇性發佈哪些 proposal」。** 該 proposal 仍完整計入本次回應的 `findings`，只是裡面一個 schema 不認得的欄位被丟棄——與 `project`／`relations` 既有 soft-repair 同層級，兩者都已在裁剪欄位內容而不影響「這個 proposal 算不算被發佈」。
2. **這是決定性操作，不觸發 retry。** 同一組輸入（含未知欄位）每次執行都會被同樣丟棄、同樣記 warning、其餘欄位驗證結果同樣穩定——不存在「這次選了 A、下次選了 B」的分歧。不變量要防的是 retry 導致不同次選擇不同子集，丟棄行為本身是純函數、與 retry 無關。
3. **N 個合法 proposal 仍整批發佈或整批不發佈**——soft-repair 只改變單一 proposal 內部欄位集合，不改變「本次回應的 proposal 集合是否整批成立」。D2 清單命中時仍整份回應失效、交由外層 retry/fallback 處理，不變量在這條路徑上完全未動。

結論：不變量保護的範圍（不得對回應做選擇性子集發佈）維持不變，本次修法只是把「未知欄位」從誤放的 hard 分類移回它本該屬於的 soft 分類。實作時需同步更新 `_build_proposal` docstring 與 `_parse_proposals` 註解，明文寫出這個分界理由。

## Testing

- 新增：N 合法 + 1 帶未知欄位（如 `tags2`）不再整份 `LlmOutputError`，其餘 proposal 正常產出。
- 新增（不變量保護）：D2 清單逐條測試，仍整份判死。
- 新增：warning 內容含欄位名與 proposal 序號，可觀測（caplog／mock logger）。
- 既有：`tests/test_llm_output.py` 全套綠，`project`／`relations` 既有 soft-repair 行為零回歸；`python3 -m pytest tests/ -q`、`python3 -m policy_check --repo .`、`openspec validate --all --strict` 全綠。
