## Why

Issue #34 顯示目前 Hippo 雖可由 dream service 寫出 knowledge 檔，仍不能保證把經驗正確原子化成可用筆記。現行 production profile 已觀察到 `promoted` note 同時為 `_unknown` project、generic title 且未進 retrieval index；另一個舊部署 profile 則因 package、hooks、service 與 backend 設定不同步，累積大量 split backlog。除此之外，CI 的 test-suite detection 會把實際存在的 pytest suite 判成不存在，導致近期綠燈沒有執行測試。

Code trace 另確認資料正確性缺口：importer 生成 session title 時覆寫原始 `assistant_summary`、只保留單筆且最多 2,000 字的 assistant outcome，並以不含實際 prompts/outcomes 的粗略 hash 去重；LLM 的 `atom_title` 沒有成為 canonical `title`；rich project ID 被誤套 filesystem path-component 驗證而落入 `_unknown`；整個 session 會一次送入 distiller，沒有在最低 32K provider baseline 下仍固定的 12K input budget 與完整 fragment coverage；合法空陣列又會被誤記為 `promoted, slices=0`；MOC 更新 255-byte 合法檔名時，暫存檔名額外加前後綴而再次觸發 `ENAMETOOLONG`。因此，這次不能只做 runtime 清理，必須交付一個可識別、可升級、可回復且通過真實 installed-service canary 的 patch release。

## Audited Release Baseline (2026-07-22)

- 目前唯一權威 GitHub release 與 tag 是 `v0.1.0`；`0.1.1` 仍是 release candidate。
- 本機曾有一個不在 `main` ancestry 的過時 `v0.1.1` tag，指向 `d04ba59`；該 tag 已移除，且不得作為 release evidence。發布前必須重新檢查 local/remote tag 與 GitHub release 的一致性。
- PR #35 已合併 source baseline，包含 CI truth、clean-install harness、session/capture 保真、minimum-32K zero-tool distillation、canonical disposition 與 hash-pinned recovery CLI；這不等於 `0.1.1` 已發布。
- 已有 runtime evidence 只能證明首批 5 個 importer recovery 與三個 isolated installed canary。尚未閉合的 hard gates 包含：部署目前 candidate wheel、完成剩餘 production recovery batches、對審計基線中 53 個 high-risk sessions 給出完整 disposition，以及三輪真正由 systemd timer 觸發、各有新 ingress 與 accepted atom 的 scheduled canary。
- 本 change 的 readiness matrix 是剩餘工作的單一權威清單；沒有 candidate commit、wheel SHA-256 與可重跑 evidence 的 checkbox 不得視為通過。
- Issue #39 承接 distiller 架構收斂：Hippo 不再管理 API key/OAuth 或直連 provider HTTP，改以三梯隊 external CLI profiles 與 bounded deterministic fallback 作為唯一運行邊界。

## What Changes

- 修正 session/atom 資料契約：分離 `session_title` 與不可被 title generation 覆寫的 `assistant_summary`；將 LLM proposal title 寫入 canonical `title`；在 publish 前拒絕 generic title；將 rich project identity 與 collision-resistant filesystem directory key 分離。
- 擴充 normalized capture 契約：保留有序且完整的 `assistant_messages[]`，以 `capture_id` / `parent_session_id` 區分同 session snapshots，semantic hash 覆蓋全部 prompts/outcomes/files/artifacts/scope/provenance，legacy capture ID 由 byte-preserved raw payload hash 決定性衍生。
- 要求每個 Dream-eligible external CLI profile 的 provider context 至少為 32,768 tokens；12,000 estimated input tokens、2,048 output tokens、48 KiB stdin prompt-transport、300 秒/chunk、每 chunk 最多 2 次與 parallelism 1 維持固定，較大的 provider context 不得放寬這些界線；按 fragment 原順序分批，單一過大 fragment 穩定分段且禁止截尾，所有 profile 都需證明 zero-tool/no-MCP/no-custom-instruction/no-interaction/no-remote eligibility。
- LLM canonical response 改為 versioned disposition wrapper；只有明確 `no_findings` 且有非空理由才可零 slice 結案。空陣列、空 stdout、錯誤 wrapper、噪音或未知欄位一律 fail closed。
- 以 per-session publication journal/commit marker 實作 logical all-or-nothing，確保中途 write/edge failure 不會留下可被 MOC/index 看見的半批 atoms；dream 以 run ID + exact slice IDs 做 publication reconciliation。
- 讓 `~/.config/paulsha-hippo/config.yaml` 成為 distiller/atomizer 唯一 runtime 真源；legacy override 僅作可逆 migration input，衝突或不完整設定 fail closed。移除 Hippo-owned `api_key_env`、provider base URL、`HttpAgentClient` 與 `openai-compatible` direct transport，OAuth/API key/endpoint 全由外部 CLI/launcher 管理；若 legacy source 的任一 prohibited direct-provider field 仍含非空值，Hippo 只回報 field/path 並 BLOCKED，operator 必須先在外部去敏。
- 將 `claude`/`codex` 定義為 Tier 1 adjudicators，`agy`/`cg` 為 Tier 2 fast/heavy workers，`co-gem`/`claude-gem` 與 operator-defined local launchers 為 Tier 3 low-cost fallback。每個 profile 可設 typed traits/task classes、requested model、profile-specific effort、`shell=False` headless argv、stdin prompt transport、priority 與 fallback allowlist；`.bashrc` alias、`{PROMPT}`、shell interpolation、`--yolo`/`--autopilot` 均不合法。Child process 只取得固定 minimal non-secret env，CLI-native fallback 必須可證明已關閉。
- 加入誠實且可稽核的 distillation provenance：profile revision/tier/attempt、requested model/effort、observed model（無法證明時為 unknown/unverified）、command/config/skill/build identity、fallback reason；non-zero agent failure保留有界且去敏的 stderr evidence。
- 將 package、hook venv/scripts、systemd unit 視為同一部署單元，提供 dry-run、apply、rollback manifest；升級後逐 surface 驗證相同 release/build identity。
- Release install flow 新增 manifest-driven `--force`：只清理有 positive ownership evidence 的退役 config keys/files、managed hooks 與 units，且必須先 dry-run、backup、drain writers、可 rollback 並保證 memory/ledger/knowledge/project registry/credential stores 不變；shared config 只保存 whole-file hash 與 Hippo-owned entry inverse patch，不複製整檔 bytes，rollback 只做 owned-entry three-way compensation。
- 修正 MOC NAME_MAX 暫存檔、Copilot 新舊 session layout reader、malformed inbox quarantine、完整 backlog/health metrics，並提供 bounded recovery 與 no-data-loss manifest。
- 修正 GitHub Actions false-green detection 與吞安裝錯誤行為；以執行過的完整 pytest、wheel clean install、舊部署 upgrade、rollback、真 backend canary 作為 release gate。
- 以符合本 repo `flat` profile 的 `0.1.1` PATCH release 收口；release PR 使用 policy 已定義的 `release:patch` label。Final untagged candidate 已是 `0.1.1`，所有 gate 跑同一 commit/wheel hash，通過後 tag 同一 commit，不建立不符合版號規範的 `-rc` tag。
- 新增 evidence-bound readiness matrix，分開 verified baseline、contract/code gaps、deployed-surface attestation、production recovery、scheduled soak、publication 與 Issue closure；candidate commit 或 wheel hash 改變時，artifact-bound evidence 自動失效。
- Issue #34 的 9 項問題全部進入 traceability matrix；producer correctness artifact 可在 automatic-consumption claim 降級後發布，但只有 installed hook → service → atom → index → recall/read、受控 recovery 與連續健康週期都有證據時才關閉 issue。

## Capabilities

### New Capabilities

- `atomization-release-integrity`: canonical runtime config、atomic deployed surfaces、可逆升級/恢復、完整 health semantics、release identity 與 installed-service acceptance contract。

### Modified Capabilities

- `stage2-llm-distillation`: 強化 session content preservation、canonical title、rich project identity、search eligibility、distiller provenance 與 failure evidence requirements。

## Impact

- 核心路徑：`paulsha_hippo/importer/{title,frontmatter}.py`、`paulsha_hippo/atomizer/**`、`paulsha_hippo/moc/**`、`paulsha_hippo/dream/**`、`paulsha_hippo/ops.py`、`paulsha_hippo/paths.py`、`paulsha_hippo/lib/session_readers.py`、`paulsha_hippo/cli.py`。
- 部署與 release：`hooks/install.sh`、systemd templates、package data、版本檔、GitHub Actions、README/operations docs、`changelog.d/`。
- Runtime migration：canonical/legacy config、hook venv、copied hooks/service units、raw/split/parked/quarantine、legacy filenames/locks、knowledge/index。`hippo recovery plan|apply|resume|rollback` 必須 pin code/config/registry/source hashes，以 staging/preimage/journal/fsync/atomic replace 提供可重啟與僅補償本批的 rollback；不得刪除原始 session 或截斷 append-only ledger。
- 相容性：讀取端須容忍舊 note 缺 distiller provenance；舊 config 只在 migration 階段讀取。無法證明 effective model 的 custom argv 記為 `unknown`/`unverified`，不得把 config label 冒充實際模型。
