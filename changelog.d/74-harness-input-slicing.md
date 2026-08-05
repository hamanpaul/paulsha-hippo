---
type: fix
---
- `contrib/local-harness/harness.py`：map-reduce 第二階段（per-concept write）依 concept `fragment_indices` 裁切 prompt 輸入（±1 鄰域 `slice_prompt_by_fragments`），大 session 時避免重複發送全量 payload 導致 max_tokens 截斷。fragment 標記正則逐字對齊 `build_prompt` 實際格式（行首、小寫、閉合括號、可選 `part X/Y` 後綴），body 內引用的假標記不再誤切；三種裁切不可信情況（無標記／空索引集合／全部越界）皆退回全量並記 stderr warning；每個 concept write 記錄 indices 與 sliced/full bytes 供稽核。
