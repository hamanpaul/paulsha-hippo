#!/usr/bin/env python3
"""non-zero exit mock backend：模擬 CLI 認證失敗／配額耗盡類故障。"""
from __future__ import annotations

import sys

sys.stdin.read()
print("fatal: authentication required", file=sys.stderr)
sys.exit(3)
