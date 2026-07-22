### Fixed

- 修正 installed hooks 在 legacy nested venv 殘留時仍啟動舊 importer 的 split-surface，明確以 `HIPPO_HOOK_PYTHON` 為優先 authority。
