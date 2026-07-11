#!/usr/bin/env python3
"""散文包 JSON mock backend：模擬 codex 類 CLI 在 JSON 前後夾敘事文字。"""
from __future__ import annotations

import sys

sys.stdin.read()
print("好的，以下是本次蒸餾結果，總共一個 slice：")
print("```json")
print(
    '[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw",'
    '"tags":["t1"],"body":"alpha distilled","source_fragment_indices":[0],'
    '"relations":[{"type":"mentions","entity":"MTK"}]}]'
)
print("```")
print("以上輸出已完成，如需調整請告知。")
