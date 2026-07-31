# 0.1.2 batch closeout（candidate f5df394）

> IC-02 gate 證據。彙編：sonnet subagent（GraphQL 批次查證）＋主線程覆核。範圍：0.1.1 之後關閉的 17 個 issue（18, 20, 32, 38, 40, 41, 55, 57, 63, 64, 67, 69, 70, 77, 80, 86, 87）。

## 總表

| Issue | 標題（一句話問題） | Closing PR | Merge commit | OpenSpec archive | 驗收證據 |
|---|---|---|---|---|---|
| [#18](https://github.com/hamanpaul/paulsha-hippo/issues/18) | 跨 CLI offered→read→applied 漏斗：先前「幾乎零使用」的觀察是否為統計假象 | 功能 PR **#59**；治理收尾 PR **#60**（首次關閉）→ reopen → **#62**（最終關閉） | 功能：`194f9ff0…`；#60：`6aefe080…`；#62（final）：`b4a317f9…` | `archive/2026-07-26-issue-18-consumption-funnel-closeout` **且** `archive/2026-07-26-issue-18-luna-closeout-followup-v7`（兩個 archive，對應兩輪收尾） | **已附**——PR#59 新增 `test_usage_funnel.py`（7 passed）+ 真實 23k+ session 實測（排噪後 read-through 15.17%／applied 9.66%，兩個 CLI 收斂）。PR#60/#62 為 Cortex workflow 收尾 PR，內容單薄（僅 checklist），實質驗收落在 #59 |
| [#20](https://github.com/hamanpaul/paulsha-hippo/issues/20) | runtime 健康快照 record issue：原子化停擺＋檢索覆蓋僅 42% | 無 closing PR | N/A | N/A（record issue，非 code 變更） | **已附**——關閉留言量化列出：原子化停擺根因（裸命令在 systemd 環境 `exit 127`）已修復；MOC coverage 226(42%)→881(100% eligible)；knowledge base 227→1,056 slices。關閉方式：作者手動關閉（該 record issue 本身無單一 closing PR，症狀分別由其他既有 PR 修復） |
| [#32](https://github.com/hamanpaul/paulsha-hippo/issues/32) | 承接 #10：gemini-headless round-trip 前提／openai-compatible 真端點尚未驗證 | 無 closing PR（相關 code 見 PR **#56**，但 #56 body 明示「Refs #55，不關閉」，且未提及 #32） | N/A | N/A | **已附**——關閉留言附量化實證：local-vllm profile 完成 Phase B 23,324 session 全量真蒸餾；agy(gemini) 完成 94 個真內容 session promoted。關閉方式：作者手動關閉（驗證性 issue，本身無新增 code，證據記於 comment） |
| [#38](https://github.com/hamanpaul/paulsha-hippo/issues/38) | 部署環境 dream timer unit 與 repo template drift（hourly vs 週五 03:00） | 無 closing PR | N/A | N/A（純部署 ops 動作，非 repo code 變更） | **已附**——關閉留言附 `systemctl --user cat` 實際輸出，確認 timer 已回到 repo 預設 hourly。關閉方式：作者手動關閉（部署環境修正，非 code PR） |
| [#40](https://github.com/hamanpaul/paulsha-hippo/issues/40) | Stage 2 收尾：canary 判過關＋Phase 2b 全量回填 | 無 closing PR（本 repo 無對應；核心遷移見 cross-repo `hamanpaul/paulshaclaw#125`） | N/A | N/A | **已附**——關閉留言列出三項完成證據：17 個 subagent 大規模知識庫去重覆核、janitor heal pass 驗證 MOC 100% eligible、Phase 2b `requeue --all-parked` 23,324 session 全量重蒸（23,364 no-findings／518 promoted，inbox 歸零）。關閉方式：作者手動關閉 |
| [#41](https://github.com/hamanpaul/paulsha-hippo/issues/41) | memory 消費端可觀測性：usage 訊號是否真回饋 relevance ranking／janitor decay | **#65** | `f03f99ed…` | `openspec/changes/issue-41-usage-feedback-loop-v5-sonnet` — **未歸檔**（仍在 active `openspec/changes/`，未搬進 `archive/`） | **已附**——focused 測試 101 passed + 3 subtests；local canonical preflight PASS（含 OpenSpec strict + 完整 tests + clean-wheel fixture） |
| [#55](https://github.com/hamanpaul/paulsha-hippo/issues/55) | 地端 vLLM 專用 harness：以 direct API + guided decoding 取代 copilot CLI 鏈路 | 無 closing keyword PR——**PR #56** 明確聲明「Refs #55，不關閉」並附 `policy-exempt:issue-link`（因 issue 驗收含 Phase B bulk 窗口，PR 本身無法終結）；issue 最終由作者手動關閉 | PR#56：`a677c7d7…` | N/A | **已附**——PR#56 驗證 checklist 全勾，`changelog.d/55-local-vllm-harness.md`；實測 48KB session map-reduce 6 findings/160s；關閉留言另附 Phase B 23,324 session 全量蒸餾佐證 |
| [#57](https://github.com/hamanpaul/paulsha-hippo/issues/57) | redaction `openai_key` pattern 過寬，`task-*` slug 被誤遮蔽劣化 recall | **#58** | `2ed893fe…` | N/A | **已附**——新增測試：`task-routing-map--sl-…` slug 不再觸發（hit_count=0）；`OPENAI_API_KEY=sk-…` 仍觸發；既有 redaction 測試全綠 |
| [#63](https://github.com/hamanpaul/paulsha-hippo/issues/63) | 治理：Spark 修訂超過 3 輪分析與 issue sizing gate | 無 closing PR，`stateReason: NOT_PLANNED` | N/A | N/A | **缺**——issue body 內「流程改進／驗收條件」6 條 checklist 全數未勾選；關閉留言僅稱「已合併至 `paulsha-cortex#208`」。內容為治理分析、整併至跨 repo issue 追蹤，本 repo 範圍內無可查驗的驗收證據 |
| [#64](https://github.com/hamanpaul/paulsha-hippo/issues/64) | `import.jsonl` 撕裂寫入令 dream 永久 partial，並修 health 指標假陽性 | **#66** | `0c8c34e1…` | `openspec/changes/issue-64-ledger-torn-line-repair` — **未歸檔** | **已附**——新增 29 個測試（`test_ledger_integrity.py`）；全套 1577 passed；對正式 production ledger 唯讀重現根因（line 230, 577 NUL bytes）並以逐位元組比對驗證修復不動非 NUL 內容；`invalid_frontmatter` 16→0。PR 誠實揭露「尚未完成的 acceptance」：merge 後才在維護窗口實際執行修復與 requeue |
| [#67](https://github.com/hamanpaul/paulsha-hippo/issues/67) | 直讀 read 被整批丟棄，`read_without_offered` 令 dream 永久 partial | **#68** | `dc70821c…` | N/A | **已附**——先寫 6 個 RED 測試再實作；全套 1620 passed；對真實 memory root 驗證 `janitor scan --dry-run` warnings 由非空變空、retention 覆蓋 slice 31→572。PR 揭露待部署後由下一輪 timer 觀察 `status: ok` |
| [#69](https://github.com/hamanpaul/paulsha-hippo/issues/69) | atomization 無可用 external agent backend，session 持續被 park | **#72** | `c1472183…` | N/A | **已附**——三案 TDD RED→GREEN；全套 1633 passed；adversarial review 攔下 1 個 BLOCKING（redaction 順序缺陷）並經邊界掃描複驗修復。環境面成因（codex quota、local-vllm 啟用）不在本 PR 範圍，留待自然恢復 |
| [#70](https://github.com/hamanpaul/paulsha-hippo/issues/70) | dream 執行期間併發 ledger 寫入被誤判 `future_event`，令整輪 partial | **#71** | `f8f5b158…` | N/A | **已附**——RED 7 failed→GREEN 13 passed；全套 1623 passed；三路確認（人工 diff／全套測試／adversarial reviewer） |
| [#77](https://github.com/hamanpaul/paulsha-hippo/issues/77) | claude profile `--permission-mode plan` 與單一 JSON 輸出契約衝突，tier-1 主蒸餾失效 | **#78** | `2f3dc981…` | N/A | **已附**——全套 1646 passed；A/B 實測對照表（帶旗標 35s/563B/解析失敗 vs 移除後 244s/22,778B/解析成功）。PR 揭露 AR-11 soak 需部署後自然累積 |
| [#80](https://github.com/hamanpaul/paulsha-hippo/issues/80) | chunk 序列總時間超出 chain 預算，已完成 chunk 被整批丟棄 | **#85** | `bd858bd2…` | `openspec/changes/issue-80-atomize-chunk-budget` — **未歸檔** | **已附（誠實部分交付）**——Task 1／2（chain 預算隨 chunk 數縮放、`max_session_chunks` 宣告）full 全套 1656 passed、openspec strict 14 passed，並在驗收階段補修一個新缺陷（`deadline_seconds` 靜默忽略）；**Task 3（已驗證 chunk 跨 profile 保留）PR 內明文標註未做**，由 #86 承接 |
| [#86](https://github.com/hamanpaul/paulsha-hippo/issues/86) | #80 Task 3 承接：已驗證 chunk 仍被 all-or-nothing 丟棄 | **#90** | `76edb932…` | 無獨立 archive（沿用 #80 已 merge 的 spec delta，PR #82） | **已附**——三個 commit 分工清楚；全套 1667 passed；獨立行為探針 12/12；驗收輪額外抓到並修復一個真實快取洩漏（park 路徑清不掉已落地 envelope），獨立重現＋清零驗證 |
| [#87](https://github.com/hamanpaul/paulsha-hippo/issues/87) | `window_older` 老化出窗被誤判 diagnostic，令 dream 永久 partial（同型第四例） | **#88** | `e4043bd4…` | N/A | **已附**——RED 8 failed→GREEN；全套 1661 passed；獨立行為探針 10/10；對正式 production memory-root 執行 dry-run 驗證 warning 由非空變 `null` |

### Issue #18 額外時序註記

`#18` 曾被關閉兩次：PR #60 於 2026-07-26T13:05 首次關閉 → 13:18 reopen → PR #62 於 21:58 最終關閉。實質功能交付在 PR #59（未直接標 closing keyword，body 內以「Refs #18 #41，不關閉」註記，因兩個 issue 完整範圍尚含 relevance 排序與 usage-based decay）；PR #60／#62 是 Cortex 派工流程的收尾／重試 PR，body 內容單薄（僅一句摘要＋checklist）。

## Known issues（open，不擋 0.1.2）

| Issue | 現況一句話 |
|---|---|
| [#89](https://github.com/hamanpaul/paulsha-hippo/issues/89) | `FIXED_MAX_AGENT_CALLS=6` 未隨 #85 的 chain 預算縮放同步調整——7+ chunk 的 session 即使時間預算充足，仍會在第 7 次呼叫前撞上固定的 call budget 而 park；PR #90（#86 的交付）body 內已明文列為「相鄰缺口，本 PR 刻意不碰」。 |
| [#74](https://github.com/hamanpaul/paulsha-hippo/issues/74) | `contrib/local-harness/`（不進 wheel、不被 package 引用）的 map-reduce 只切輸出、不切輸入——每次 per-concept write 都重送全量 payload，大 session（實測 48 fragments/100KB）在三種 effort 下皆因 `max_tokens` 截斷或逾時而失敗；因僅屬 contrib harness，不影響 core wheel／CI，故不擋 0.1.2。 |

## 本 batch 之後、candidate 之前的 PR（91–95）

無對應 issue，屬 spec 明文化／治理／release 簿記性質：

| PR | 標題 | Merge commit |
|---|---|---|
| #91 | docs(spec): 明文化 AR-11 soak 窗口語意（中性輪不歸零、回歸輪重置） | `ed1ea37e…` |
| #92 | fix(dream): 放寬 require-idle 的 max-load 預設至 4.0 並補 skip 觀測值 | `478661bd…` |
| #93 | feat(shortlist): offer 早停——8 事件無讀取即停止自動 offer，顯式 recall 不受限 | `bd5a34ae…` |
| #94 | feat(importer): trivial-session gate——攔截 title 自捕捉遞迴（#7 第二型），零誤殺回測 23.9k session | `f5df3949…` |
| #95 | chore(release): 0.1.2 candidate 重綁至 f5df394 並重跑 AR-01，AR-11 改寫為窗口語意 | `bcbfe90d…` |

**時序準確性註記（重要）**：candidate commit `f5df394` 與 **PR #94 的 merge commit 逐位元組相同**——即 candidate 快照本身就是 PR #94 落地後的狀態。PR #95（`bcbfe90d…`）merge 時間（07-30 12:48）**晚於** `f5df394`（PR #94, 07-30 11:24），其內容正是「把 candidate 正式重綁至 f5df394」的治理宣告 commit，**不包含在 `f5df394` 這個快照裡**（本 worktree 未見 `bcbfe90d` 於 `git log`，僅存在於 `git log --all`）。換言之：PR #95 是「關於 f5df394 是 candidate」的紀錄，而不是 f5df394 快照的一部分。撰寫最終版報告時建議明確區分「candidate 內容範圍（含 PR #91–94）」與「candidate 認定的治理紀錄（PR #95）」，避免誤讀成 95 個 PR 全數落在快照內。

## 資料截止時間戳與產生方式聲明

- 資料查詢時間：2026-07-31T01:27:08Z（UTC）。
- Repo 快照：`/home/paul_chen/prj_pri/hippo-gates-test`（worktree），`HEAD` 為 detached，commit `f5df39496de17c8b4fb3aa88dfa698b7c547aaf4`（`v0.1.1-58-gf5df394`），`VERSION` 檔仍為 `0.1.1`（未 bump）。本次彙編未修改該 worktree 任何檔案。
- 產生方式：
  - Issue 標題／關閉時間／`stateReason`／body／comments／closing 與 cross-reference timeline：`gh api graphql`（`repository.issue.timelineItems`，型別 `CLOSED_EVENT`／`CROSS_REFERENCED_EVENT`／`REOPENED_EVENT`）批次查詢 17 個 issue 一次取得。
  - PR 標題／merge commit／body（含「驗證」章節逐字引用）：`gh pr view <N> -R hamanpaul/paulsha-hippo --json ...`，並以 `git merge-base --is-ancestor <commit> HEAD` 在上述 worktree 逐一核對每個 closing PR 的 merge commit 確實為 candidate 的祖先提交。
  - OpenSpec archive 對照：`ls openspec/changes/archive/` 全量列出後，逐 issue 以 `grep -rl "#<N>\b" openspec/` 交叉核對是否有專屬或被提及的 change 目錄；同時列出 `openspec/changes/`（非 archive）確認哪些 change 仍在 active 狀態未歸檔。
  - `changelog.d/` 目錄全量 `ls` 作為輔助佐證（非必要判準，僅用於交叉核對 R-09 碎片是否存在）。
  - 任何欄位無法由上述指令查得或需要人工推斷之處，均已於表格內以粗體註明「缺」或於文字說明中明列判斷依據；未見無法查得而未標註的欄位。
