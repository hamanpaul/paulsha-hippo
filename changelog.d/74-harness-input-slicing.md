---
type: fix
---
- `contrib/local-harness/harness.py`：map-reduce 第二階段（per-concept write）依 concept `fragment_indices` 裁切 prompt 輸入（±1 鄰域 `slice_prompt_by_fragments`），大 session 時避免重複發送全量 payload 導致 max_tokens 截斷。
