# paulsha-hippo 🦛

> 跨 LLM vendor 的經驗筆記基座——session 自動蒸餾成原子筆記，睡眠期（dream）整理，隔天喚醒（wakeup）回灌 context。
> 命名取自海馬迴（hippocampus）：大腦在睡眠時做記憶固化的器官。

**狀態：✅ v0.1.0 已發布；v0.1.1 release candidate 驗證中。** 已從 [paulshaclaw](https://github.com/hamanpaul/paulshaclaw) 完整拆出、可單獨安裝運轉（完整測試與 WSL2+systemd 全鏈：截取→蒸餾→回灌）。
設計見[拆包執行設計 spec](https://github.com/hamanpaul/paulshaclaw/blob/main/docs/superpowers/specs/2026-07-06-memory-extraction-hippo-design.md)。
> 已驗環境：WSL2＋systemd＋外部 headless CLI。profile/tier/fallback 矩陣見 `docs/backend-matrix.md`；無 systemd 主機用 `hippo dream supervise`（追蹤 [#10](https://github.com/hamanpaul/paulsha-hippo/issues/10)）。Hippo 不管理 API key、OAuth、provider URL 或 credential store。

## Quickstart

    pipx install git+https://github.com/hamanpaul/paulsha-hippo
    hippo init                          # 預設：~/.agents/memory + 外部 claude profile
    hippo install all --force --dry-run # 先檢查 Hippo-owned release surfaces
    hippo install all --force            # 只套用 ownership manifest 證明的檔案
    hippo install hooks && hippo install service --enable
    hippo doctor                        # 健檢：路徑契約/hooks/服務/backend/runtime 進程與 lock（--fix-backend 冪等遷移裸命令為絕對路徑；預設解析級檢查，--probe-live 才真實喚起 backend smoke probe）
    hippo dream run --dry-run --memory-root ~/.agents/memory
    hippo wakeup --project <slug>

## Install

- 支援 host：claude / codex / copilot（session hooks 隨包出貨）
- 常駐：systemd user units 自動偵測；不可用時 `hippo dream supervise` 前景模式（`--once` 可單輪驗收）
- WSL 注意：`loginctl enable-linger` 才能開機自起

## Usage

日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo recall`（跨 CLI 任務相關檢索）／`hippo search`／`hippo usage`（漏斗報表；`mark-applied` 回報 applied）／`hippo index verify`／`hippo replay`／`hippo bundle`／`hippo requeue <session-key>|--all-parked`（parked session 修復後重排）／`hippo recovery plan|apply|resume|rollback`（hash-pinned、預設 5-session 的 importer recovery，不自動重播 LLM）。
跨 CLI 消費能力（codex/copilot 的 prompt-time／read attribution 實測）見 `docs/cross-cli-capability-matrix.md`。
蒸餾失敗顯性化：backend 不可用／重試超限的 session 進 `parked`（證據在 `runtime/queue/_failed/`），修復後 `hippo requeue` 恢復；`dream run` 以 global lock 保證單一 writer，並發第二實例記 log 後跳過。
維運：`hippo doctor`（含 dream lock 持鎖狀態與 dream/supervise 進程健康報告——PID/start/cmdline、非 canonical 標記，只報告不自動 kill）；`hippo locks cleanup-legacy --memory-root <root> [--apply]`（legacy per-session lock 一次性清理，預設 dry-run，僅維護窗口使用）。

設定：runtime distiller 唯一來源為 `~/.config/paulsha-hippo/config.yaml`；`HIPPO_*` 僅覆寫路徑。外部 CLI 自行負責登入與 launcher，Hippo 不讀取外部 agent 的認證狀態。
Project registry：設 `project_registry.auto_write: true`（預設 off）後，importer 自動把已解析的 project mapping 寫入 generated 檔 `~/.agents/config/paulsha/project-hippo.yaml`（勿手改；讀取端自動 union-read legacy `projects.yaml`）。契約見 `docs/project-registry-contract.md`。
蒸餾只使用宣告式 external headless profiles：Tier 1 `claude`/`codex`、Tier 2 `agy`/`cg`、Tier 3 `co-gem`/`claude-gem`/custom local。每個 profile 自訂 traits、task classes、model、effort 與 tokenized argv；prompt 一律走 stdin，fallback 順序與 bounded budget 見 `docs/backend-matrix.md`。

## 架構

pipeline：hooks ingress → raw → atomize 蒸餾 → ledger/moc → dream（清晨整理）→ wakeup（回灌）。
`paulsha_hippo/lib/`：自足共用件（lifecycle schema／idle／jsonl 原語），與 [paulshaclaw](https://github.com/hamanpaul/paulshaclaw) 共用。

## Version

目前 release candidate 為 `0.1.1`（Issue 34 語意保全、外部 CLI atomization 與可逆 recovery；尚未 tag/release）。版本記錄見 `CHANGELOG.md`；
發版採 semver，主 repo 以 commit SHA pin 依賴（tag 僅人讀標記）。

## 家族

`paulshaclaw`（agent 框架）｜`paulsha-hippo`（本 repo，記憶基座）｜`paulsha-conventions`（policy 引擎）
