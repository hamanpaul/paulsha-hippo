### Fixed

- 當 systemd PATH 內的同名 external CLI 因缺少 shebang interpreter 而不可執行時，`doctor --fix-backend` 會優先固定使用操作者互動環境中的 CLI 版本，再以絕對 interpreter 路徑使其 service-effective；避免誤綁較舊、未由操作者維護的 system-wide 副本。
- Codex 預設 profile model 更新為已通過本機 ChatGPT account external CLI smoke 的 `gpt-5.6-sol`；model 仍是可由 operator 調整的 profile 欄位，Hippo 不管理登入或憑證。
