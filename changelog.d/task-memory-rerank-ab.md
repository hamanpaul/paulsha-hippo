新增 `HIPPO_TASK_MEMORY_RERANK=ab` A/B 模式，以固定 seed 和 task ID 穩定分組；A 組保留原始 payload 並背景量測，R75 組同步嘗試 rerank，失敗時回退 A 並記錄實際交付 manifest hash 與原因。
