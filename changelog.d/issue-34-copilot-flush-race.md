### Fixed

- 修正 Copilot CLI sessionEnd hook 與 `events.jsonl` 最終 flush 的競態，避免有效真 session 被誤記為 `empty-skip`。
