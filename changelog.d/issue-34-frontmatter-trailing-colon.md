### Fixed

- Importer 會引用 YAML 中以冒號結尾或接 flow delimiter 的 scalar，避免 fallback session title 讓 inbox frontmatter 無法解析並在每輪 Dream 重複隔離。
