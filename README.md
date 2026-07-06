# paulsha-hippo 🦛

> 跨 LLM vendor 的經驗筆記基座——session 自動蒸餾成原子筆記，睡眠期（dream）整理，隔天喚醒（wakeup）回灌 context。
> 命名取自海馬迴（hippocampus）：大腦在睡眠時做記憶固化的器官。

**狀態：🧪 骨架期。** 程式碼遷入受 [paulshaclaw#125](https://github.com/hamanpaul/paulshaclaw/issues/125) 站穩閘約束；
設計見 [拆包執行設計 spec](https://github.com/hamanpaul/paulshaclaw/blob/main/docs/superpowers/specs/2026-07-06-memory-extraction-hippo-design.md)。
下述安裝與使用流程為遷入後的目標介面（**尚未可用**）。

## Quickstart（規劃中）

    pipx install git+https://github.com/hamanpaul/paulsha-hippo
    hippo init                          # 三個問題：memory 資料夾、蒸餾 LLM、agent host
    hippo install hooks --host claude && hippo install service --enable
    hippo dream run --dry-run
    hippo wakeup

## Install

（規劃中——遷入後啟用）

- 支援 host：claude / codex / copilot（session hooks 隨包出貨）
- 常駐：systemd user units 自動偵測；不可用時 `hippo dream supervise` 前景模式
- WSL 注意：`loginctl enable-linger` 才能開機自起

## Usage

（規劃中——遷入後啟用）

日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo search`／`hippo replay`／`hippo bundle`。

設定：單一檔 `~/.config/paulsha-hippo/config.yaml` + `HIPPO_*` env 覆寫；密鑰一律 `secret.env`（0600）。
蒸餾 LLM 三檔位：`claude-headless`（預設，零 key 管理）／`openai-compatible`（ollama、vLLM、內網端點）／`custom-argv`。

## 架構

pipeline：hooks ingress → raw → atomize 蒸餾 → ledger/moc → dream（清晨整理）→ wakeup（回灌）。
`paulsha_hippo/lib/`：自足共用件（lifecycle schema／idle／jsonl 原語），與 [paulshaclaw](https://github.com/hamanpaul/paulshaclaw) 共用。

## Version

目前 `0.1.0`（骨架：CLI 入口、lib 護欄、conventions 1.0.12 + R-21）。版本記錄見 `CHANGELOG.md`；
發版採 semver，主 repo 以 commit SHA pin 依賴（tag 僅人讀標記）。

## 家族

`paulshaclaw`（agent 框架）｜`paulsha-hippo`（本 repo，記憶基座）｜`paulsha-conventions`（policy 引擎）
