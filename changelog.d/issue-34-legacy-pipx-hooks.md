### Fixed

- `hippo install all --force` 現在會辨識並清除舊版未標 `managedBy`、但透過 pipx Python 執行 Hippo script 的 Claude/Codex hook entries；保留不相關 wrapper，同一 event 最終只留下單一 managed hook。
