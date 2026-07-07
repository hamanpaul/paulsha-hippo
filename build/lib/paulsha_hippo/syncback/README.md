# Sync-back gate

`psc memory syncback check` 是 Stage 2 T9 的唯讀治理關卡：它評估 5 個 sync-back 條件，輸出 `PASS` / `FAIL`，並在全數通過時列出 sync manifest。

## Guardrails

- **Fail-closed**：任何檔案缺失、解析失敗、測試失敗或例外都視為 FAIL。
- **Read-only**：只讀 repo 內素材與執行既有測試，不寫回 canonical state、也不自動 sync。
- **Deterministic**：`now` 由呼叫端注入；測試可透過 `test_runner` 注入，避免依賴隱含 wall-clock 或不可控 runner。

## Sync-back 實體

sync-back 的實體是 **可安裝套件**，不是 skill：`paulshaclaw/memory` 模組、hooks、`install.sh` / `uninstall.sh`，以及未來會納入的 MCP server。CLI 回傳的 sync manifest 是「若 gate 通過，人工後續要回寫哪些套件路徑」。

## 五個條件

1. **`tests`**：跑 importer / classifier / replay 相關 unittest，確認核心 Stage 2 模組可通過既有測試。
2. **`decay_evidence`**：跑 decayed / reactivation 相關 unittest，並要求對應 evidence 已在 repo 內落地。
3. **`evidence_present`**：檢查 `docs/superpowers/workstreams/stage2-paulsha-memory/evidence/` 的必要檔案存在且非空。
4. **`review_clear`**：解析 `docs/superpowers/workstreams/stage2-paulsha-memory/review.md` 的 `## 結論`，必須可判定為可合併，且沒有 live blocking marker。
5. **`schema_unextended`**：確認 Stage 2 沒有擴充 Stage 3 lifecycle 的必填 frontmatter schema；若新增額外 required 欄位則 fail。

## CLI

```bash
python3 -m paulshaclaw.memory.cli memory syncback check --repo-root .
```
