### Fixed
- `install service` 生成的 systemd unit：`ExecStart` 綁定當前 interpreter（`sys.executable`），修正 pipx / venv 隔離安裝下寫死 `/usr/bin/env python3`（全域 python）import 不到 `paulsha_hippo`、導致 dream service 一觸發即 `exit 1`（ModuleNotFoundError）的問題。
