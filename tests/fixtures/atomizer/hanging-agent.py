#!/usr/bin/env python3
"""timeout mock backend：讀完 stdin 後長眠，觸發 agent_exec timeout。"""
from __future__ import annotations

import sys
import time

sys.stdin.read()
time.sleep(30)
