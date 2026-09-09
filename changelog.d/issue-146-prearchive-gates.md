### Fixed

- #146：將 active OpenSpec `tasks.md` 的 pre-archive 驗證項拆成獨立 gate，分開記錄已完成的 `pytest`／`policy_check`／`OpenSpec strict` 與仍待完成的 cross-repo review、canary、utility trial，同時維持凍結 workstream todo 文案不漂移。
- #146：修正 governed preflight 在 disposable HOME 下的 runtime-health fixture 偽陽性，改用穩定且非暫存的既有 cwd，避免無關案例誤增 `cwd-temp-worktree`。
