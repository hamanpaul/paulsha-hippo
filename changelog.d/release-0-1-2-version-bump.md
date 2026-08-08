---
type: change
scope: release
---
### Changed

- 版號 `0.1.1` → `0.1.2`：`VERSION`、`pyproject.toml`、`paulsha_hippo.__version__`、`paulsha_hippo.importer.__version__`、`scripts/build_release_artifact.py` 的 manifest 預設值，以及四處斷言真實版號的測試（`test_build_info` 兩處＋其嵌入式 `_build.json` fixture、`test_cli` 的 `--version` 輸出、`test_ops` 產生的 systemd unit `HIPPO_BUILD_VERSION`、`tests/installed` 乾淨安裝的 version JSON）。mock／fixture 中與真實版號無關的 `0.1.1` 字面值（`test_distillation_provenance` 的 provenance 樣本、`test_ops` 的 build attestation 樣本）刻意不動——它們不是版本宣告。註：`build_identity()` 對「執行期版號 ⟷ wheel 內嵌 `_build.json` 版號」做 fail-closed 比對，故連嵌入式 fixture 都必須同步，版號一致性是執行期強制而非慣例。
- `CHANGELOG.md` 的 `[Unreleased]` 定稿為 `[0.1.2] - 2026-08-08`，沿用 0.1.1 的凍結作法（保留空 `[Unreleased]` 於頂、`changelog.d/` 碎片不清除）。
- candidate 自凍結點 `38ee2f2` 拉出，**不含 `96513bc`（#126）**：該 commit 於凍結後落地且變更 `paulsha_hippo/lib/lifecycle/schema.py`，不在 AR-11 soak 所測的部署 build（`4d2b1f2`）內，故留待 0.1.3。下游 paulshaclaw 以 commit SHA 直接 pin `96513bc`，不依賴本次 release 取得該修復。
- `reports/verify/release-readiness-matrix.json` 由 `f5df394` 重綁至 0.1.2 candidate `ddeba3a3`（wheel `919d685d…`）。依 `bind_candidate()` 漂移語意先作廢全部既有 `passed`，再逐 gate 以本 candidate 的實跑重新 attest——**16/16 passed**。artifact-bound 的 AR-02/03/14 依定義對新 wheel 重跑；AR-04/06/07/09/10/12/13 與 IC-01/IC-02 全數實跑；僅 AR-08 的 codex／copilot 兩半走規格允許的 applicability revalidation（`paulsha_hippo/hooks/**` 在 `f5df394..ddeba3a3` 逐位元未變、`importer/**` 只差版本字串、`cli.py` 差異純為兩個 hook 不呼叫的新增子命令），其 claude-code 半邊仍有部署 build 上的新鮮全鏈證據。
- `reports/verify/release-0.1.2-closeout.md` 新增追加批次段，涵蓋 `f5df394..ddeba3a3` 期間關閉的 10 個 issue，每筆的 closing PR merge commit 皆以 `git merge-base --is-ancestor` 驗證為 candidate 祖先。
