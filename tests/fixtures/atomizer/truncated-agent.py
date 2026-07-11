#!/usr/bin/env python3
"""截斷輸出 mock backend：模擬 max-token 截斷——JSON 陣列中途斷裂。"""
from __future__ import annotations

import sys

sys.stdin.read()
print('[{"title":"alpha","artifact_kind":"report","project":"paulshaclaw",'
      '"tags":["t1"],"body":"alp', end="")
