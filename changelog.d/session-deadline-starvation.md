---
type: fix
---
- external agent fallback 鏈的 session 預算不再被單一 agent 的呼叫上限封頂。`external_agents.deadline_seconds` 界定的是**整條 fallback 鏈**，`FIXED_TIMEOUT_SECONDS` 界定的是**單一 agent 呼叫**；先前 `atomizer/config.py` 以後者驗證前者，使四個 eligible profile 共用「一個 agent 份量」的預算——router 給每次呼叫的是 `min(profile.timeout, remaining_seconds)`，因此鏈末的 tier-3 fallback 結構性地永遠拿不到應有時間。實測：claude 35.1s + codex 16.5s + cg 66.6s 消耗掉 300s 預算中的 118s，`local-vllm` 只拿到 181s，而它實際需要 203s。新增 `FIXED_SESSION_DEADLINE_SECONDS = 600` 專用於 chain-wide 預算，出貨模板 `atomizer.yaml` 的 `deadline_seconds` 同步為 600。**單次呼叫上限（即 hang 防護）刻意維持 300s 不變**，只放寬鏈路整體預算，讓進度正常但較慢的 fallback 能跑到自己的上限而非只吃殘額。
