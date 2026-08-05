---
type: fix
---
- RouterState 主迴圈因 session_deadline 提前 break 時，剩餘 enabled 且 task_class 匹配的 profiles 補記 skipped/ineligible provenance 記錄，attempts_detail 不再憑空遺漏 profiles。
