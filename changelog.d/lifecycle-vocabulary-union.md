---
type: fix
---
- lifecycle 詞彙表改為記憶平面與治理平面的**聯集**，修復與 `paulsha-cortex` 的跨平面對齊 FAIL：`lib/lifecycle/schema.PHASES` 由 7 個擴充為 8 個，新增 cortex 的首階段 `claim`，成為 `("claim", "research", "define", "plan", "build", "verify", "review", "ship")`。背景：cortex 於 `ae4bc43` 把首階段由 `research` 改名為 `claim`，本平面未跟進，兩者的消費端對齊測試（paulshaclaw `tests/test_cortex_alignment.py::test_cortex_phases_match_hippo_schema`）自此 FAIL。
- 採聯集而非改名的依據：`claim` 在 cortex 是結構上不同的階段（work item 認領、manager 決定性執行），與本平面 `research`（記憶 slice 的調查階段）語意不同，不可互相改名；且本平面既有 **235 個 `phase: research` slice** 若改名會被 `schema.py` 的 phase 驗證判為非法，聯集則零資料遷移。
- 安全性：`PHASES` 在本平面只做成員資格檢查（`schema.py` 的 phase 驗證、`events.py` 的 transition 驗證）與 `template.py` 的 gates 產生，**不決定順序**，故擴充不改變既有行為；`build_lifecycle_template()` 的 `current_phase` 預設維持 `research`，新建 lifecycle 模板的 `gates` 多一個 `claim` 條目。
- 新增 `tests/test_lifecycle_phases_vocabulary.py` 釘住聯集內容、本平面起始階段仍為 `research`，以及「共用詞彙不得退化成跨套件 import」的零依賴底限。異動此表必須同步 `paulsha-cortex` 的 `persona/contract.PHASES`，已在常數註解寫明。
