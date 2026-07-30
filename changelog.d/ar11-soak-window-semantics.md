---
type: docs
---
### Changed

- 明文化 AR-11 canary/soak 計數的窗口語意（`openspec/specs/atomization-release-integrity/spec.md`）：soak 於單一「窗口」（同一部署 build 上、每個已執行輪皆 `ok` 且無回歸標記的連續 timer 排程運行）內計數；中性輪（idle-gate skip、timer 缺席、zero-ingress、zero-accepted-atom 且 `ok` 無回歸標記）不計入也不歸零；已執行輪出現非 `ok` 狀態、回歸標記相對前輪增長（新 legacy lock、generic-title／`_unknown` 增長、parked／split 增長、index coverage 不完整）、或部署 build 變更則重置窗口；回歸標記一律以前一已執行輪的 baseline 判增長，非絕對零。新增三個 Scenario（Neutral cycle does not reset／Regression or redeploy resets／Static backlog does not disqualify）。
