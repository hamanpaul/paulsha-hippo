### Fixed

- #146：將 active OpenSpec/workstream checklist 的 pre-archive 驗證項拆成獨立 gate，分開記錄已完成的 `pytest`／`policy_check`／`OpenSpec strict` 與仍待完成的 cross-repo review、canary、utility trial，避免過早宣稱整體 closeout。
