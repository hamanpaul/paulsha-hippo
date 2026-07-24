### Fixed
- redaction：`openai_key` / `anthropic_key` 規則 pattern 加前置 negative lookbehind `(?<![A-Za-z0-9])`，避免 `sk-` 出現在字串內部（最常見為 wiki-link slug 的 `ta`sk-`routing`）時整行被誤判 `[REDACTED LINE: openai_key]`，靜默劣化 task-* 記憶的 recall 品質（#57）。真憑證（前接空白/引號/`=`/行首）仍照抓。
