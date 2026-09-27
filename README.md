> **Fact:** `paulsha-hippo` 是 outcome-linked engineering experience lifecycle 的唯一 authority，負責跨 vendor 經驗的 provenance、recall、applied attribution、reinforce、contradict 與 retire。

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

日常命令：`hippo dream run|status`／`hippo wakeup`／`hippo recall`（跨 CLI 任務相關檢索）／`hippo show --agent <slice_id>`（精簡 note 視圖，省 ~70% token）／`hippo followups list|verify|close|extract`（可行動語句 ledger）／`hippo knowledge backfill-provenance|link-supersedes|mark-episodic`（一次性 migration，先 dry-run）／`hippo knowledge bucket-report`（knowledge bucket 合併的唯讀 impact report）／`hippo search`／`hippo usage`（漏斗報表；`mark-applied` 回報 applied）／`hippo index verify`／`hippo replay`／`hippo bundle`／`hippo requeue <session-key>|--all-parked`（parked session 修復後重排）／`hippo shortlist eval|freeze`（push shortlist BM25 基準線：凍結 query 集評估與待標註骨架）／`hippo h2 freeze`（#148 H2 離線 benchmark 的凍結 as-of BM25 基準線，見 `docs/h2-offline-baseline.md`）／`hippo recovery plan|apply|resume|rollback`（hash-pinned、預設 5-session 的 importer recovery，不自動重播 LLM）。
跨 CLI 消費能力（codex/copilot 的 prompt-time／read attribution 實測）見 `docs/cross-cli-capability-matrix.md`。
唯讀 7／30 天記憶 KPI 稽核可使用 repo-local `custom-skills/hippo-memory-kpi/`；它分開呈現 session→atomic note、machine-valid/searchable 與嚴格 `offer → read → applied` 漏斗，另含 `followups` 區塊統計每個 window 的 open／resolved-in-source 筆數，且不呼叫 `hippo recall` 污染分母。
蒸餾失敗顯性化：backend 不可用／重試超限的 session 進 `parked`（證據在 `runtime/queue/_failed/`），修復後 `hippo requeue` 恢復；`dream run` 以 global lock 保證單一 writer，並發第二實例記 log 後跳過。
維運：`hippo doctor`（含 dream lock 持鎖狀態與 dream/supervise 進程健康報告——PID/start/cmdline、非 canonical 標記，只報告不自動 kill）；`hippo locks cleanup-legacy --memory-root <root> [--apply]`（legacy per-session lock 一次性清理，預設 dry-run，僅維護窗口使用）。
Store 佈局（#151）：`memory_root` 請放在持續同步／被掃描的目錄（如 Obsidian vault）**外**，只把 `knowledge/` 以 symlink 借回 vault；落在同步樹內時 `hippo doctor`、`hippo dream run`、`hippo init` 會警示（偵測可用 `HIPPO_SYNC_MARKERS`／`HIPPO_SYNC_ROOTS` 設定）。`hippo archive gc --memory-root <root> [--retention-days N] [--include-no-findings] [--list-out FILE] [--apply]` 以 processing ledger 為準回收已落成 knowledge 之 session 的 atomizer 衍生副本（`archive/sessions`、`archive/fragments`；預設 dry-run、冪等；`archive/queue` raw capture 是 recovery／backfill 來源，一律保留）。遷移與回滾步驟見 `docs/storage-layout.md`。

設定：runtime distiller 唯一來源為 `~/.config/paulsha-hippo/config.yaml`；`HIPPO_*` 僅覆寫路徑。外部 CLI 自行負責登入與 launcher，Hippo 不讀取外部 agent 的認證狀態。
Project registry：設 `project_registry.auto_write: true`（預設 off）後，importer 自動把已解析的 project mapping 寫入 generated 檔 `~/.agents/config/paulsha/project-hippo.yaml`（勿手改；讀取端自動 union-read legacy `projects.yaml`）。只登記 roots 的既有 project 可用 `hippo registry backfill-remotes`（預設 dry-run；`--apply` 寫前備份並輸出回復指令）補上 remotes，讓 worktree session 收斂回同一 slug。契約見 `docs/project-registry-contract.md`。
Push shortlist 雜訊基準線（#158）：設 `shortlist.push_shadow.enabled: true`（預設 off）後，prompt hook 照舊注入，另把「BM25 分數門檻＋最多 0–3 則」的確定性收窄結果寫進 `runtime/ledger/push_shadow.jsonl`（注入內容逐位元不變、不呼叫 LLM）；`hippo shortlist eval` 在凍結 query 集上輸出 Precision@3／Noise@3／Relevant-missed@12／注入字元量並校準門檻。格式、指標與校準方式見 `docs/push-shadow-baseline.md`。
Task memory provider：`hippo task-memory provide|fetch` 提供給外部 adapter 使用的 stdin/stdout JSON protocol；它只接受明確授權 Hippo 且能由 project registry 唯一映射的 repo，並以 manifest/hash 限制 note fetch。完整 envelope、錯誤碼與接線方式見 `docs/task-memory-provider.md`。
蒸餾只使用宣告式 external headless profiles：Tier 1 `claude`/`codex`、Tier 2 `agy`/`cg`、Tier 3 `co-gem`/`claude-gem`/custom local。每個 profile 自訂 traits、task classes、model、effort 與 tokenized argv；prompt 一律走 stdin，fallback 順序與 bounded budget 見 `docs/backend-matrix.md`。

## 架構

pipeline：hooks ingress → raw → atomize 蒸餾 → ledger/moc → dream（清晨整理）→ wakeup（回灌）。
`paulsha_hippo/lib/`：自足共用件（lifecycle schema／idle／jsonl 原語），與 [paulshaclaw](https://github.com/hamanpaul/paulshaclaw) 共用。

[互動式架構圖](docs/architecture/architecture.html)（架構事實權威：[`facts.json`](docs/architecture/facts.json)）

## Version

目前 release candidate 為 `0.1.1`（Issue 34 語意保全、外部 CLI atomization 與可逆 recovery；尚未 tag/release）。版本記錄見 `CHANGELOG.md`；
發版採 semver，主 repo 以 commit SHA pin 依賴（tag 僅人讀標記）。
Repo policy 由 canonical `.project-policy.yml` 宣告，並 pin paulsha-conventions v1.0.17。

## 家族

`paulshaclaw`（agent 框架）｜`paulsha-hippo`（本 repo，記憶基座）｜`paulsha-conventions`（policy 引擎）
