---
type: fix
---
- 修正 `hippo --help` crash：`cli.py` 的 `show` 子命令 help 字串裡 `~70% token` 有一個未跳脫的 `%`，argparse 對 help 字串做 `%`-formatting 時炸掉（`ValueError: unsupported format character 't'`），改為 `~70%% token`；PR #137（issue #136）帶進來的，補上遞迴 render 全部子命令 help 的 regression test（issue #139）。
