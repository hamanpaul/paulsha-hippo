# paulsha-hippo 🦛

> 跨 LLM vendor 的經驗筆記基座——session 自動蒸餾成原子筆記，睡眠期（dream）整理，隔天喚醒（wakeup）回灌 context。
> 命名取自海馬迴（hippocampus）：大腦在睡眠時做記憶固化的器官。

**狀態：✅ v0.1.0 上線。** 已從 [paulshaclaw](https://github.com/hamanpaul/paulshaclaw) 完整拆出、可單獨安裝運轉（953 tests；WSL2+systemd 全鏈實測：截取→蒸餾→回灌）。
設計見[拆包執行設計 spec](https://github.com/hamanpaul/paulshaclaw/blob/main/docs/superpowers/specs/2026-07-06-memory-extraction-hippo-design.md)。
> 已驗環境：WSL2＋systemd＋claude-headless。其他 backend（codex/copilot/openai-compatible headless）與無 systemd 主機為 opt-in，見 [#10](https://github.com/hamanpaul/paulsha-hippo/issues/10)。

## Quickstart

    pipx install git+https://github.com/hamanpaul/paulsha-hippo
    hippo init                          # 預設：~/.agents/memory + claude-headless（零 key 設定）
    hippo install hooks && hippo install service --enable
    hippo doctor                        # 健檢：路徑契約/hooks/服務/backend（--fix-backend 冪等遷移裸命令為絕對路徑；預設解析級檢查，--probe-live 才真實喚起 backend smoke probe）
    hippo dream run --dry-run --memory-root ~/.agents/memory
    hippo wakeup --project <slug>

## Install

- 支援 host：claude / codex / copilot（session hooks 隨包出貨）
- 常駐：systemd user units 自動偵測；不可用時 `hippo dream supervise` 前景模式
- WSL 注意：`loginctl enable-linger` 才能開機自起

## Usage

日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo index verify`／`hippo replay`／`hippo bundle`／`hippo requeue <session-key>|--all-parked`（parked session 修復後重排）。
蒸餾失敗顯性化：backend 不可用／重試超限的 session 進 `parked`（證據在 `runtime/queue/_failed/`），修復後 `hippo requeue` 恢復；`dream run` 以 global lock 保證單一 writer，並發第二實例記 log 後跳過。

設定：單一檔 `~/.config/paulsha-hippo/config.yaml` + `HIPPO_*` env 覆寫；密鑰一律 `secret.env`（0600）。
Project registry：設 `project_registry.auto_write: true`（預設 off）後，importer 自動把已解析的 project mapping 寫入 generated 檔 `~/.agents/config/paulsha/project-hippo.yaml`（勿手改；讀取端自動 union-read legacy `projects.yaml`）。契約見 `docs/project-registry-contract.md`。
蒸餾 LLM 三檔位：`claude-headless`（預設，零 key 管理）／`openai-compatible`（ollama、vLLM、內網端點）／`custom-argv`。

## 架構

pipeline：hooks ingress → raw → atomize 蒸餾 → ledger/moc → dream（清晨整理）→ wakeup（回灌）。
`paulsha_hippo/lib/`：自足共用件（lifecycle schema／idle／jsonl 原語），與 [paulshaclaw](https://github.com/hamanpaul/paulshaclaw) 共用。

## Version

目前 `0.1.0`（Phase 1 遷入：memory 31k LOC、CLI 全樹、三檔位蒸餾、installer）。版本記錄見 `CHANGELOG.md`；
發版採 semver，主 repo 以 commit SHA pin 依賴（tag 僅人讀標記）。

## 家族

`paulshaclaw`（agent 框架）｜`paulsha-hippo`（本 repo，記憶基座）｜`paulsha-conventions`（policy 引擎）
