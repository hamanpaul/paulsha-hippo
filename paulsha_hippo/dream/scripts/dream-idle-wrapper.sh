#!/usr/bin/env bash
# 排程（systemd）dream 路徑的薄 wrapper。
# #175: 顯式 pin --promoter llm，與生產 dream loop（scripts/start.sh）一致，
# 且本地 atomizer override 無法翻轉 promoter 選擇。
# 注意：identity promoter 僅保留為顯式測試/離線選項——它會把 importer 樣板
# fragments（## Source / ## CWD / (none) 清單等）1:1 複製成 knowledge slices，
# noise gate 只能擋掉其中一部分樣板。
set -euo pipefail
MEMORY_ROOT="${PSC_MEMORY_ROOT:-$HOME/.agents/memory}"
exec python3 -m paulshaclaw.memory.cli memory dream run --memory-root "$MEMORY_ROOT" --require-idle --promoter llm
