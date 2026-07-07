### Fixed
- #7 遞迴自捕捉：agent_exec 對蒸餾子程序注入 `HIPPO_SELF_SESSION=1`，5 個 capture hook（session_end×3／precompact×2）讀到即早退（layer 1）；importer 對 prompt 內容即 atomize skill 調用文本者 `self-skip`（layer 2）
- #8 空 session 汙染：importer 對無 prompt/無 touched files/summary 空或佔位/turn≤1 的 session `empty-skip`，不寫 inbox、不入蒸餾佇列
