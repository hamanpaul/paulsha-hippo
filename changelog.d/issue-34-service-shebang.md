### Fixed

- `hippo doctor --fix-backend` 現在會辨識外部 CLI 的簡單 `/usr/bin/env <interpreter>` shebang；當 systemd service PATH 找不到該 interpreter 時，以互動環境解析出的絕對 interpreter 與 script 路徑重寫 profile argv，不引入 shell wrapper，也不接管外部 agent 的認證。
- backend migration 在 atomic replace 前先驗證 canonical config 與改寫後 profiles，避免驗證失敗時留下已變更的無效設定。
