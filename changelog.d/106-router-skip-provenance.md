---
type: fix
---
- RouterState 主迴圈因鏈預算耗盡提前 break 時（pre-attempt deadline check 補 `session_deadline`、attempt 中途 deadline/call-budget 耗盡補 `session_budget`），剩餘 enabled 且 task_class 匹配的 profiles 補記 skipped/ineligible provenance 記錄，attempts_detail 不再憑空遺漏 profiles；circuit-open 的剩餘 profile 維持無聲跳過不補記。
- exhausted raise 的 category/profile_id/exit_code/stderr 錨定最後一筆真實 attempt（不受合成 skip 記錄影響），park 行為與修復前完全一致；skip 補記可使 attempts 長度超過 max_attempts，屬明文預期語意。
