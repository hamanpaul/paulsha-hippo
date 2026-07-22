## Context

Issue #34 涵蓋兩種不同但相關的 deployment profile：

- `current-pipx`：dream service 使用較新的 package，但 hooks 仍可能由舊 package 複製到獨立 venv。service 能寫 note，卻可寫出 `_unknown`、generic-title、未 indexed 的 promoted note；dream 持續 partial。
- `stale-system`：package、hooks 與 service 都落後，service-effective PATH 找不到 backend，split/poisoned cache 大量累積，且既有 status 只呈現 raw inbox，嚴重低估 backlog。

現行資料流為：

```text
CLI hook/session archive
  -> importer normalize/title/render
  -> inbox raw session
  -> atomizer split + LLM promote
  -> knowledge write + processing promoted
  -> MOC rename/link
  -> retrieval index
  -> prompt shortlist -> Read -> applied
```

根因依 ProblemMap route-first 判定為 **F4 Execution & Contract Integrity**，次要為 **F5 Observability**；broken invariant 是 `execution_skeleton_closure_broken`。資料並未消失，所以不是單純 F3 state loss；真正破裂的是資料欄位、config/deployment、publish 與 health oracle 沒有形成閉合 execution skeleton。對應 global fix 為 deployment-deadlock 型的拓撲化修復：先恢復 test truth 與資料契約，再收斂部署/config，最後才允許 bounded recovery 與 release canary。

## Audited Baseline and Authority

`v0.1.0` 是目前唯一權威 release。PR #35 的 merge commit 只建立 source baseline，不構成 `v0.1.1` release。曾指向 `d04ba59` 的 local `v0.1.1` tag 不在 `main` ancestry，已移除；其 commit object 即使在 Git object database 中暫時仍可解析，也不具 release authority。

本 change 區分四種證據：

1. **Merged source baseline**：可對 source contract 繼續有效，但不證明當前 production artifact。
2. **Artifact-bound evidence**：必須 pin candidate commit 與 wheel SHA-256；candidate 改變就失效。
3. **Runtime-state evidence**：必須 pin deployed surfaces、config/registry hashes、recovery manifest 與時間。
4. **Issue-closure evidence**：可包含不阻擋 producer release 的 consumer offered → Read，但在缺少時必須降級 capability 並保持 Issue #34 open。

## Goals / Non-Goals

**Goals:**

- 新 session 的原始 assistant outcome 在 title generation 與 atomization 全程不被覆寫。
- 每張新 atom 具有單一可復用概念、具體 canonical title、正確 project identity、合法 checksum/frontmatter 與誠實 distiller provenance。
- runtime 只有一份 canonical distiller config；package、hooks 與 service 可證明來自同一 release/build。
- current 與 stale deployment 都能 dry-run、受控升級、分批恢復及 rollback，且有 no-data-loss manifest。
- release gate 能證明 pytest 真正執行、artifact 可 clean install、installed service 走真 ingress 產出並索引正確 atom。
- Issue #34 的 producer、ingress、recovery 與 consumer acceptance 都有可重跑證據。

**Non-Goals:**

- 不重寫已存在的 shard-lock、retrieval index、funnel ledger 與 park/requeue 的 durable data contract；external-agent profile registry、direct-provider transport removal、fallback routing 與 retry/park transition 明確在本 change scope。
- 不在這份 planning change 中修改 production runtime、probe 真 backend、requeue、cleanup、commit、push 或建立 PR/release。
- 不回填無法由 source archive 或 ledger 證明的歷史 summary/project/model；不以猜測填補 provenance。
- 不新增未驗證的 backend/model，亦不把互動式 shell 的 latent model 設定宣稱為 service 實際模型。
- 不承諾所有模型都會主動消費 shortlist；release 只接受有實際 offer → Read 證據的平台能力宣稱。

## Decisions

### 1. Release success 是閉合鏈，不是 `systemd exit 0` 或 `processing=promoted`

新 note 的 per-session promotion 必須先通過 content/title/project/frontmatter/checksum gate。Dream 完成 MOC/index 後，run health 再對本輪產物做 disk/frontmatter/index 三方對賬；缺任一層即不得回報 `ok`。systemd service 可因 operational skip 保留 exit 0，但 machine-readable status 必須明確區分 `ok`、`degraded/partial`、`failed`、`skipped`，release oracle 讀 ledger/status 而不是只讀 process exit code。

### 2. Session title 與 semantic content 是不同欄位

Normalized session 增加 `session_title`（或等價 canonical 欄位）。Title generator 只能寫這個欄位與 `title_source`，不可覆寫 adapter 提供的 `assistant_summary`。Inbox frontmatter 使用 `session_title`，`## Summary` body 保留原始 `assistant_summary`。對舊資料只在 source archive 仍可重建 summary 時回填；否則標記 evidence unavailable，禁止用 title 冒充 summary。

Normalized session 同時保留所有有序、完整的 `assistant_messages[]`，並將最後一筆非空訊息映射到相容欄位 `assistant_summary`。每次 hook snapshot 有唯一 `capture_id`；legacy payload 由 raw payload SHA-256 衍生，只有 source 明確提供時才設定 `parent_session_id`。Importer 以 `tool:session_id:capture_id` 作為 capture identity，再以包含全部有序 prompts/outcomes、files、artifacts、scope 與 parent ID 的 semantic hash 排除真正重複 snapshot；不再用粗略 completeness 捨棄內容改變。

### 3. Atom title 在 write 前成為 canonical `title`

LLM proposal 的 title 同時寫入 `title` 與相容欄位 `atom_title`；MOC naming 依序使用 `title`、`atom_title`、heading、最後才 fallback。`is_generic_title` 與 retrieval pool 共用單一判定；首次 generic output 以同一 canonical backend 做一次 bounded repair，仍 generic 則該 session 留在 bounded retry/park 流程，不寫一張注定不可檢索的 promoted note。Repair 使用獨立、versioned、包含 proposal index/original title/config/skill/prompt hash 的 cache key，只 immutable-replace title，並計入同一次 promotion attempt；不得命中原本 full-proposal cache 後假裝修復。

### 4. Project identity 與目錄 key 分離

Project registry 的 rich ID（例如 remote-form identifier）是 frontmatter/ledger 的權威 metadata；只在 `_knowledge_path_for` 等 filesystem boundary 轉成 collision-resistant 的 `readable-prefix--p-<canonical-id-hash>` directory key。LLM 預設繼承 importer 已解析的 source project，不得自行 re-home；只有 source project 為 `_unknown` 時才可從 legacy + generated registry 的 union candidate 中選擇。歷史 path migration 與 `_unknown` 修復必須有 source-session 證據、collision pair test 與 dry-run manifest，無證據者維持 `_unknown`。

### 5. Canonical config 唯一真源，legacy 只進 migration

`~/.config/paulsha-hippo/config.yaml` 完整承載 atomizer/distiller 設定。Runtime loader 不再 deep-merge legacy override；upgrade planner 讀 legacy 只為產生 migration diff。Hippo 不再擁有 provider API key、key-env 名稱、OAuth、provider base URL 或 direct HTTP transport；legacy `openai-compatible`、`api_key_env`、`base_url` 與 `HttpAgentClient` 設定必須由 migration 明確移除或拒絕。若任一 prohibited direct-provider field（credential value/env-name、OAuth、secret path、provider URL）含非空值，migration 只回報 field/path 與 `operator-redaction-required`，不得備份、複製、套用或記錄該值；operator 必須先在 Hippo 外去敏，之後 Hippo 才能對 sanitized input 進行可 rollback migration。canonical 與 legacy 衝突、外部 CLI profile 不完整、或 argv 在 service environment 不可解析時 fail closed。Migration 必須可重入：第二次 apply 產生零 semantic diff。

### 6. Provenance 區分 requested 與 observed

Processing ledger與 atom frontmatter保存：

```text
backend_id, provider,
profile_id, profile_revision, tier, attempt_index,
requested_model, requested_effort,
observed_model, model_verification,
command_fingerprint, fallback_reason,
config_hash, skill_hash,
hippo_version, build_commit
```

不得保存 secret、token、完整 endpoint query 或個人 executable path。Custom argv 若 response/CLI 沒有可驗證 model identity，`observed_model=null` 且 `model_verification=unverified`；config label 只能是 `requested_model`。Agent non-zero evidence 保存固定上限、去 control character/credential pattern 的 stderr tail，raw prompt/output 仍不得落 log。

### 7. Package、hooks、service 是 atomic deployed surfaces

Release artifact 以 version + build commit + wheel SHA-256 識別。Candidate wheel 先安裝到獨立 bootstrap venv，由不依賴 active target 的 runner 在第一個 mutation 前 fsync write-ahead manifest、把 hooks fence 成 durable-spool-only、停止 timer 與 active service、等待 importer/dream writers drain 並取得 maintenance/dream locks。Manifest 必須保存可離線還原的舊 wheel/venv 或等價 package-manager restore input。之後才由 profile-specific adapter 切換 active package、遷移 config、重裝 hooks/service、逐 surface attestation、service-effective probe、bounded canary、恢復 writers/timer。任何一步失敗即停止；rollback runner 必須位於 target 外。舊版本只有通過新增 ledger/quarantine schema 的 forward-compatibility test 才可重新接管，否則使用隔離 snapshot 或 roll forward，既有 knowledge/ledger 與 post-upgrade delta 都不得被截斷。

### 8. Recovery 只能在 backend 與部署 attestation 通過後開始

Recovery planner 盤點 raw/split/retrying/parked/quarantined、oldest age、poisoned cache、legacy filename/index/lock。Apply 先處理 deterministic repair（安全 temp name、quarantine malformed input、legacy rename/index rebuild），再以小 batch requeue；每批重新驗證 backend 與健康指標。Malformed input 原檔移至 quarantine 並記 hash/reason/source path，不刪除。Legacy lock cleanup 只有在 hooks 已 attested 為新 shard-lock build 後才可執行。

### 9. CI 與 candidate artifact 必須可證明真的測過

GitHub Actions 不再用會因 unmatched glob 失敗的 `ls` 組合判斷 tests，安裝不得以 `|| true` 吞錯。Workflow 必須顯示 collected/executed test count 大於零。因 repo 版號只允許 `X.Y.Z[-fix.N]`，候選不建立 `-rc` tag。Final untagged candidate commit 已包含 `0.1.1` version、正式 changelog 與 strict-valid active OpenSpec；所有 artifact/upgrade/rollback/canary gate 跑同一 wheel hash，通過後只把 `v0.1.1` tag 加到同一 commit，不再改檔或 rebuild。Release evidence 完成後再以官方 `openspec archive` 做 docs/spec closeout；該 post-tag metadata commit 不 rebuild release wheel。

### 10. Installed-service canary 是 release 必要條件

Hermetic tests 可使用 fake backend 驗 deterministic contract，但 release gate 另須由 wheel clean install/upgrade 後的真正 hooks 與 service 執行。對宣稱支援的 Claude/Codex/Copilot 各送至少一個真 session；每條鏈需有 import → processing → knowledge → index evidence。Copilot 必須涵蓋目前的 `session-state/<sid>/events.jsonl` layout；若某平台不能通過，release notes/capability matrix 必須降級宣稱。Producer release 可在 auto-consumption claim 降級後發布；Issue #34 close gate 另要求至少一條支援 consumer 鏈留下真 offer → Read，applied 只有真實 structured acknowledgement 才可計入。

### 11. Per-session publication 以 journal/commit marker 閉合

Atomizer 在任何 visible write 前先驗證全批 proposals，將 atoms 寫入 same-filesystem staging 並 fsync，append `publish_prepare` journal（target/checksum/relations），再 materialize targets。新 schema atoms 只有在 transaction commit marker 存在時才對 MOC/index eligible；中途 crash 留下的 targets 因此不會被檢索。下次 run 在新 work 前先依 journal idempotently finish 或 rollback，relation edges 以 publication ID 去重，最後才 append `promoted` commit record。Dream 把 `run_id` 傳入 atomizer，atomizer 回傳/persist 精確 `produced_slice_ids` 供 run-level metadata/FTS reconciliation。

### 12. 最低 32K provider context 下使用 bounded sequential chunks

Distiller 有效 provider context 必須至少為 32,768 tokens；32,768 是預設與最低支援的地端基準。即使 operator 宣告更大的 context，每 chunk 仍只允許固定的 12,000 estimated input tokens（另有 10% safety margin）與 2,048 output tokens，且實際 stdin prompt UTF-8 不得超過 48 KiB。固定 prompt 成本先計入 skill、schema 與 registry，剩餘空間按原 fragment 順序打包。單一 fragment 過大時先依段落穩定分割並標示 `part n/m`；不得截尾或遺漏任何字元。Chunks 依序以 parallelism 1 執行，每 chunk timeout 300 秒、最多 2 次嘗試，結果先進 staging；全部成功後才做決定性 local dedup 與 per-session atomic publication，不另呼叫 reducer。任一 budget gate 無法滿足時以 `context_budget_exceeded` fail closed。

每個可供 Dream 使用的外部 CLI profile 都必須在實際 CLI 能力範圍內啟用等價的 zero-tool/no-MCP/no-custom-instructions/no-ask-user/no-remote 約束；若 CLI 無法證明這些限制，該 profile 不得進入 Dream eligible set。不得以 `--yolo`、`--autopilot`、permission bypass 或 tool-enabled mode 換取 headless 執行。Prompt 一律經 stdin 傳入，不得出現在 argv、process list、shell interpolation 或 `{PROMPT}` template。Canonical response 為 `{"schema_version":1,"disposition":"findings|no_findings","reason":null|string,"findings":[...]}`。相容窗只接受非空 legacy array；空 array、空 wrapper、錯誤型別、噪音或未知欄位都是 invalid output。`no_findings` 必須有非空理由，並以獨立 terminal `no-findings` 狀態結案；`promoted` 必須 `accepted_slices >= 1`。

### 13. Recovery 以 frozen sources 與 hash pins 為邊界

`hippo recovery plan|apply|resume|rollback` 只允許使用 frozen `archive/queue/**/*.json` 與其可驗證 transcript pointer。Plan 時把 transcript bytes 凍結到 transaction root，重抽與 apply/resume 只使用並驗證該 snapshot hash；外部仍在追加的 live transcript 不會讓既定 plan 漂移，也不會改變 planned artifact。Transaction root 由 code/config/registry/source pins 與 batch size 共同定址，不同 candidate 即使 source census 相同也不得覆寫或共用舊 manifest/snapshots/journal。Plan 固定 code/config/registry/source/transcript-snapshot hashes，列出 winner、舊新路徑/hash、decision 與預計 ledger delta。Apply 先寫 same-filesystem staging 與 preimage，以 journal、`fsync` 與 `os.replace` 逐項提交；resume 前重新驗證全部 pins，rollback 只補償本批已提交變更，永不 rewrite 舊 JSONL。Importer recovery 與 LLM replay 分離，預設 batch size 5，不盲目重跑既有 promoted sessions。只有 source/project/canonical title 相同且 body hash 改變時才自動建立 `supersedes`，否則並列並留給人工審核。

### 14. Readiness matrix 是 evidence-bound release oracle

Readiness matrix 每一 gate 只允許 `not-started`、`in-progress`、`passed` 或 `blocked`。`passed` 必須同時有 gate ID、candidate commit、wheel SHA-256（若該 gate 與 artifact 相關）、evidence path/URL、執行時間與重跑指令。單獨 checkbox、PR merge、exit code 0 或人工口頭確認不構成 pass。Candidate commit 或 wheel hash 改變時，所有 artifact-bound gates 回到 `not-started`；只有純 source baseline 且經 applicability 重驗的 evidence 可保留。Hard AR gate 不允許以 waiver 改寫為 passed。

### 15. Tag authority fail closed，不在錯誤 commit 上重定位

Candidate gates 完成前，local、remote 與 GitHub release 不得存在 `v0.1.1`。若預先存在的 tag 指向非 candidate commit，release 必須 blocked；先留下 audit evidence 再移除錯誤 tag，不得靜默 force-move。Final tag 只能在所有 hard AR gates 通過後加到已測試的同一 commit，並必須驗證 GitHub release 與已發布 artifact hash。在此之前，README 與 install docs 必須保持 RC/untagged 語意，不得提供不存在的 `@v0.1.1` install pin。

### 16. Production recovery、scheduled soak 與 issue closure 分層收口

PR #35 的 first-5 importer recovery 與 isolated canaries 是 prerequisite evidence，不取代 production closure。Recovery manifest 必須覆蓋所有剩餘 batches，並對審計基線中 53 個 high-risk sessions 逐一給出 `recovered`、`retained`、`quarantined`、`parked` 或 `manual-review` disposition 與證據，不得留下無理由 unknown。

Release soak 只計入真正由 systemd timer 觸發的週期；直接呼叫 service、手動執行 pipeline 或 isolated canary 不計入三輪 scheduled cycles。每輪都必須有唯一新 ingress、至少一張 accepted atom 與完整 index reconciliation。Consumer offered → Read 可在 capability 降級後不阻擋 producer release，但繼續阻擋 Issue #34 closure。

### 17. Hippo 只路由外部 CLI，不管理 provider credentials

`claude`、`codex`、`agy`、`cg`、`co-gem`、`claude-gem` 與後續自定 agent 都是 Hippo 外部的 headless CLI/launcher。OAuth、API key、endpoint、provider SDK 與登入生命週期由各 CLI 擁有；Hippo config、manifest、provenance、log 與 issue evidence 都不得保存 credential value、credential env-name 或 secret file path。Atomizer、importer title generation 與 SkillOpt 必須共用同一個 external-agent router，不得保留 Gemma TCP/direct HTTP side channel。Repo 內既有 provider proxy/launcher 只能刪除、移出 release ownership，或明確標示為不再受支援的外部範例。

### 18. Profile 是宣告式能力，tier order 是決定性的

每個 profile 至少宣告 `id`、`tier`、`priority`、`traits`、`task_classes`、`model`、`effort`、`supported_efforts`、`argv`、`timeout` 與 fallback policy。`traits` 是可審查 metadata，不是讓模型動態改 route 的自由文字。Dream 只選 `task_classes` 包含 `atomization` 且通過 zero-tool eligibility 的 profile。

| Tier | Default profiles | Intended traits |
|---|---|---|
| 1 | `claude`, `codex` | 難以決斷任務的 judge/reasoner |
| 2 | `agy`, `cg` | 快速反應或大量重型實作 |
| 3 | `co-gem`, `claude-gem`, custom-local | 低成本 fallback/備援 |

同 tier 以顯式 `priority` 排序，不得依 unordered mapping 或模型自行判斷。`argv` 必須是 `shell=False` 的 token list，executable 在 systemd service environment 中可解析，且不得是 shell alias/function、`bash -c` 或 `sh -c`。`cg` 若要供 service 使用，必須由 repo 外提供真正 executable launcher；`.bashrc` alias 不構成可部署 profile。Template 只允許完整 token 的 `{MODEL}` 與 `{EFFORT}`，並依 profile 的 allowlist/renderer 驗證；prompt 只走 stdin。

### 19. Fallback 是 bounded state machine，不是透明重試

Hippo 擁有跨 profile route；Dream profile 必須關閉外部 CLI 的 native auto-model/fallback，無法關閉或無法由 preflight 證明已關閉者不得進入 eligible set。每個完整 session attempt 固定使用一個 profile；切換 profile 前丟棄未 commit staging，從 frozen input 重新執行，禁止混合不同 agent 的 chunks/output。`no_findings` 是成功，不觸發 fallback。只有 profile ineligible、auth、rate-limit、capacity、timeout、transport/process、empty/invalid output 等 allowlisted failure 可前進；input-contract、policy/config、unsafe 與 context-budget 等 deterministic failure 立即 fail closed。

Fallback graph 必須無環、受全域 deadline/max-attempts/max-agent-calls 約束，並對反覆失敗 profile 實作 circuit breaker/cooldown。fallback 後成功必須標為 `degraded-success` 並保留先前 attempt chain；全鏈耗盡才把 session park 一次。Cache identity 至少包含 task class/operation、response schema hash/version、router contract version、profile ID/revision、tier、model、effort、command/config/skill/prompt hashes，避免不同 operation 或 agent 共用輸出。

### 20. `--force` 是 ownership-manifest transaction

Release install flow 提供 `hippo install all --force --dry-run` 與 `hippo install all --force`（需要時另加 `--enable`），只可依上一版 install manifest 或 versioned legacy allowlist 移除 Hippo 明確擁有且新版已退休的 config 欄位、hook、unit、venv、cache/temp。僅因「新版 package 沒這個檔案」不足以證明 ownership。Dry-run 必須列出 keep/update/remove/backup/conflict 且零 mutation；apply 必須 fence/drain writers、取得 lock、建立權限受限 backup、staged atomic replace、檢查 `daemon-reload`/doctor/probe，失敗可由 target 外 rollback。Shared config 只保存 whole-file hash 與 Hippo-owned entry 的結構化 preimage/inverse patch，禁止複製整檔 bytes；每次 commit 前須驗 preimage hash，rollback 永遠只對 Hippo-owned entries 做三方補償，無法安全合併時 BLOCKED 並保留使用者並行變更。只有 sanitized、Hippo-exclusive file 才可做整檔 backup/restore。修改過的 managed file 是 conflict + backup，不得盲刪；第二次 force run 必須零 semantic diff。

Force 永不觸碰 memory corpus、raw/archive/inbox/knowledge、append-only ledgers/index/recovery/logs/locks、project registry、外部 agent launcher、shell rc、OAuth/key stores、secret env 或未知/共享設定。所有 target 必須先拒絕 symlink/path traversal 與 broad root/home scope；共享 Claude/Codex settings 只能移除 Hippo 自己建立且 manifest 可證明的 entries。

## Parallelization and Merge Topology

```text
Wave 0  verified baseline + readiness matrix
   |
   +---- Wave 1A  session/atom correctness gaps
   +---- Wave 1B  external-agent router/tier/fallback + force install
                     |
Wave 2  candidate deploy/attestation             <--- 1A + 1B
   |
Wave 3  production recovery + 53 dispositions    <--- 2
   |
Wave 4  installed ingress E2E + scheduled soak   <--- 2 + 3
   |
Wave 5  publish v0.1.1 -> consumer closeout
```

Wave 1A/1B 可由不同 worktree/subagent 平行；Wave 2/3 只在契約與部署介面 merge 後開始。Master agent 先宣告 file boundaries；任何跨 boundary 寫入先停下協調。每波 merge 到 integration branch 後必跑 unit/integration tests，未通過不得進下一波。

## Risks / Trade-offs

- **Schema drift:** 增加 provenance/session title 欄位可能讓舊 reader 失敗。Mitigation：reader 對缺欄 backward-compatible；writer 單向新增；OpenSpec contract 與 fixtures 同步。
- **Recovery load:** stale profile backlog 很大，立即全量 requeue 會造成資源與成本尖峰。Mitigation：canary batch、age/attempt ordering、每批 stop conditions、timer quiesce。
- **Model identity 不可觀測:** custom CLI 常不回報 effective model。Mitigation：誠實標 unknown/unverified，不以猜測阻擋正確資料產出；release gate仍驗 backend round-trip。
- **Multi-file publication 無單一 filesystem transaction:** atom files、relations 與 processing ledger 無法靠一次 rename 全部提交。Mitigation：same-filesystem staging + write-ahead journal + eligibility commit marker + idempotent recovery；run-level reconciliation 再閉合 index publication。
- **Consumer 行為非 deterministic:** prompt 提示不能保證模型一定 Read。Mitigation：以真 trace 決定 capability，不偽造 applied；producer correctness artifact gates 可在 auto-consumption claim 降級後發布，但 Issue #34 維持 open 直到 consumer close gate 通過。
- **Agent CLI drift:** CLI flag、model/effort vocabulary 與 OAuth 狀態會獨立變動。Mitigation：profile-specific capability probe、service-effective executable resolution、strict renderer allowlist 與清楚 failure category。
- **Fallback 隱藏主要故障:** 備援成功可能掩蓋 Tier 1 長期失效。Mitigation：`degraded-success`、完整 attempt provenance、circuit-breaker metrics 與 release gate 內的 primary-profile smoke。
- **Force cleanup 誤刪:** 舊檔或共享設定的 ownership 可能含糊。Mitigation：manifest/legacy allowlist positive ownership、dry-run、backup、drift conflict 與 protected-state denylist。
- **大變更整合風險:** 若以單一巨型 PR 實作會難以 review。Mitigation：依 wave 拆小 PR/commits，以一個 integration/release gate 收斂；所有非 closing PR 都使用明確的 issue-link exemption reason，tag/evidence 完成後才關 Issue #34。
