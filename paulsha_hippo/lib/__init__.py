"""自足共用件（lifecycle / idle / jsonl 遷入處）。

入會規則（spec §3.2）：兩個以上跨 repo 使用者、自足、盡量 stdlib-only。
本 package 禁止 import paulsha_hippo.lib.* 以外的 paulsha_hippo.*，
由 tests/test_lib_isolation.py 強制。
"""
