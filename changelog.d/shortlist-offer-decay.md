---
type: feat
---
- Prompt-time shortlist 新增 offer 早停機制：`build_shortlist_and_record`（`_shortlist_common.py`）
  在同一 `(tool, session_id)` 的歷史 offer 事件數達 `OFFER_STOP_AFTER_EVENTS`（8）門檻、且該
  session 於 `memory_usage.jsonl` 中從未出現讀取（`source=="read"`，不分 offered true/false）
  或 applied（`kind=="applied"`）訊號時提前 `return ""`——不注入 shortlist、不記新 offered
  ledger 事件。實測依據：每個 user prompt 觸發一次 offer、每次 3 個新 slice，長 session 因此
  累積 p90=39、max=127 個唯一 offered slice；但 9 次 offered→read 中 7 次發生在第 0-1 個 offer
  事件、2 次在第 6 個，第 7 個事件之後零讀取——對「一直沒在讀的 session」持續 offer 是純
  context 污染。門檻取 8（比實測讀取發生的最後一個事件序號 6 多兩個事件）保留安全邊際；
  session 只要曾有讀取或 applied，判定恆真、永不早停（使用者證明在消費，繼續供給）。
- 事件計數搭在既有 `offered.jsonl` 掃描路徑上（`_reconcile_offered_map` 內的 `_scan_offered_ledger`
  單一 pass 同時回傳 pairs 與事件筆數），不為早停判斷新增一次 ledger 掃描；`memory_usage.jsonl`
  的逐行讀取（`_session_has_engaged`）為本次唯一新增 I/O，且只在事件數達門檻時才觸發，未達門檻
  的一般 prompt 完全不受影響。早停狀態不落任何新檔——每次重算，無 schema migration、無 reconcile
  負擔。
