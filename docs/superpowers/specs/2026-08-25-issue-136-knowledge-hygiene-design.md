---
status: accepted
work_item: issue-136-knowledge-hygiene
---

# Knowledge hygiene：provenance / supersedes / agent view / episodic / follow-ups 設計

- 日期：2026-08-25
- Issue：[#136](https://github.com/hamanpaul/paulsha-hippo/issues/136)；分支 `feature/136-knowledge-hygiene`
- 來源：ot-ti-mirror session 029101bb（MCU 知識資產盤點）對 hippo 產出的實測診斷
- 狀態：已核可（2026-08-25，建議值全數採用；TTL 維持 90 天），待實作；本文件只做設計，不含實作
- 對應 repo 慣例：核可後拆成 `openspec/changes/<slug>/{proposal,design,tasks}.md`，
  每個 fix 一個 `changelog.d/<slug>.md` 碎片（R-09）
- 基準程式碼：`paulsha-hippo` HEAD `f674aef`（v0.1.2 = `d7cbd37`，之後只有 policy 同步與
  memory-KPI skill，本文引用的模組在 0.1.2 與 HEAD 相同；`main` `b03db45` 與 `f674aef` 內容零差異）；live runtime 為 pipx 0.1.2
  （`~/.agents/memory/hooks/.hippo-build.json` `build_commit ddeba3a3`）

## 0. 現況與證據

### 0.1 資料流（本文所有 fix 的落點）

```
SessionEnd hook ──► runtime/queue/<tool>__<sid>__<cap>.json     (hooks/claude_session_end.py)
   payload keys: cwd, session_id, transcript_path, capture_scope, capture_id …（無 commit）
        │
        ▼ importer.ingest（hook 以 Popen 立即觸發）
inbox/<bucket>/<tool>/<day>/<sid>.md  memory_layer: inbox        (importer/pipeline.py, importer/frontmatter.py)
   provenance.repo  ← _git.git_remote(git_toplevel(cwd))   ← 有填
   provenance.commit ← session.get("commit")               ← payload 沒有 → "_unknown"
   provenance.path  ← archive/queue/…json
        │
        ▼ atomize（dream run / hippo atomize）
inbox/_slices/<proj>/<agent>__<sid>__NNN.md                       (atomizer/pipeline.py _split_pass)
        │ LLM promote（atomize-knowledge-slice.md skill + prompt.py）
        ▼
knowledge/<proj>/<slug>--<slice_id>.md  memory_layer: knowledge   (slice_frontmatter.build_from_proposal / render)
   supersedes: []  ← 只有 _attach_unambiguous_supersedes 會填（同 session 重蒸餾且標題相同）
   distiller: {…}  ← safe_provenance()，attempts/chunk_provenance 全量
        │
        ├─► moc/linker.py  fio.update(aliases, related)   ← 整份 frontmatter 重 dump
        ├─► moc/search.py  build_index → runtime/indexes/retrieval.db（FTS5: title/tags/body）
        └─► janitor/rules.py  superseded → decayed → search 的 active=0 → shortlist 看不到
        │
        ▼ UserPromptSubmit hook
hooks/_shortlist_common.py  bm25 − 0.1·link_weight − usage_boost(≤0.04)，fetch 12 → 去掉已 offer → 取 3
retrieval.format_shortlist → "- [title] — first body line — /abs/path"  +  applied 回報指引
agent 用 Read 開整檔 ──► hooks/claude_post_tool_use.py 記 memory_usage.jsonl(source=read)
```

### 0.2 量化證據（2026-08-25，`~/.agents/memory/knowledge`，唯讀統計）

| 項目 | 數值 | 來源 |
|---|---|---|
| knowledge notes（不含 MOC） | 4,178 | `find` |
| 其中帶 `distiller:` 區塊（LLM atomizer 產出） | 2,845 | grep |
| `provenance.commit: _unknown` | 3,218（MCU-Octopus + ot-ti-mirror：647/647） | grep |
| `supersedes` 非空 | 32（647 筆中 21） | grep |
| lifecycle.jsonl 事件 | 32 筆，全部是 `superseded` decay | ledger |
| 檔案總量 / frontmatter 佔比 | 11.8 MB / **79%**（MCU 兩專案 85–87%） | awk |
| `distiller:` 佔 frontmatter | **54%**（≈ 全檔 43%） | awk |
| 平均每檔 / 平均 body | ≈ 2.8 KB / ≈ 0.57 KB | 推算 |
| 同標題重複 notes（MCU 兩專案） | 32 筆冗餘；近似重複（雙目標建置隔離契約／雙 Profile 建置隔離契約／雙 Build Profile 隔離契約、Release_Button ×10）未計 | grep |
| body 含 session 狀態句（尚未 commit／session 結束時／handoff／目前狀態／本次修改僅限） | 104 | grep |
| body 含可行動句（需要更新／需加／缺口／TODO／should be updated） | 235 | grep |
| body 含 `path:line` 引用 | 337 處 / 159 notes | grep |

### 0.3 三個實際發生的失效（同一個 session 內觀察到）

1. **舊 note 蓋過新 note**：hook 對「flash layout」浮出 08-17 的
   `ot-ti-mirror-flash-layout-and-image-artifacts--sl-18fd8c783eae6408`（「NVS 0x54000 未顯式保留，
   需加 linker reservation」），但 08-21 的 `雙目標-flash-layout--sl-3799ea88ac969cf8`（MCU-Octopus）
   已寫「以 linker/map assertion 防止碰撞」。兩筆 `supersedes: []`，ranking 無新鮮度項，agent 若信第一筆
   會回報一個已修掉的問題。
2. **發現沒有閉環**：08-17 那筆明寫「`README-ARC.md:108` 的 133,604 B 已不符，需要更新」；08-25
   `README-ARC.md:109` 仍是 133,604。它是一筆記憶，不是一張票，沒有任何機制追。
3. **token 結構**：agent 依 hint 用 Read 開整檔，每筆付 ≈ 2.8 KB 拿 ≈ 0.57 KB 內容；hint 本身
   對 meta 問題（「怎樣的工具對你有幫助」）撈出 buzzer／FPC 硬體事實——bm25 對 prompt 全文 OR-join，
   無 kind／任務型別過濾。

### 0.4 與原診斷不符之處（讀 code 後修正）

- `supersedes` **不是**沒有機制：`atomizer/pipeline.py::_attach_unambiguous_supersedes` 會自動連結，
  但條件是 `distilled_from`（同一 session）＋同 project＋canonical title 相同＋checksum 不同——
  即只涵蓋「同一 session 重蒸餾」。跨 session 同主題永遠不會連。下游 plumbing 完整：
  supersedes → janitor `decay_superseded: true` → lifecycle `decayed` → `search(include_decayed=False)`
  隱藏。32 筆 lifecycle 事件證明這條路真的會走。問題在偵測，不在 plumbing。
- `provenance.commit` 的整條鏈已存在：`adapters/base.py:156` 讀 payload `commit`、`sanitizer.py:38`
  白名單、`importer/frontmatter.py:123` 寫入、atomizer `_split_pass` 原樣帶進 fragment →
  `build_from_proposal(session_meta["provenance"])`。缺的只是**生產者**：Claude SessionEnd payload
  沒有 `commit`，importer 也只從 cwd 探 remote 不探 HEAD。
- janitor 已有 `check_provenance_commit: false` 欄位（`janitor/lifecycle.yaml`），但 `rules.py` 只解析
  不使用——fix 1 可以給它語意。
- `distiller:` 的消費者只有：`slice_frontmatter.validate`（發布時要求存在）、
  `tests/test_distillation_provenance.py`（round-trip）。memory-KPI `report.py`、index／census／MOC／
  janitor／wakeup 都不讀它（grep 確認）。

## 1. Fix 1：`provenance.commit` 填實＋`cites` 結構化

### Problem
3,218 筆 `commit: _unknown`；commit 只以散文形式出現在 body（「HEAD `2a655c3`」），不可查詢；
`README-ARC.md:109` 這類 `path:line` 引用共 337 處，全是散文。

### Design
**1a. 擷取時填（主要）**：`hooks/claude_session_end.py`（及 codex/copilot 對應檔）在寫 queue payload 前，
best-effort（2 s timeout，任何失敗留空）以 `payload["cwd"]` 執行：
```
git -C <cwd> rev-parse HEAD            → payload["commit"]
git -C <cwd> status --porcelain | head  → payload["git_dirty"]: bool
git -C <cwd> rev-parse --abbrev-ref HEAD → payload["git_branch"]
```
`adapters/base.py` 已讀 `commit`；新增 `git_dirty`／`git_branch` 兩個 optional 欄位，`sanitizer`
白名單同步加入。`importer/frontmatter.py` 與 `_render_fragment` 的 `provenance:` 區塊增加
`dirty`／`branch`（缺省省略，不破壞舊讀者：`_read_fragment` 只取 repo/commit/path，需擴成 5 鍵）。
`slice_frontmatter.render` 的 `("repo","commit","path")` 迴圈同步擴成 5 鍵。

**1b. 匯入時補（fallback）**：`importer/pipeline.py::_preview_queue_item_unlocked` 在
`discovered_toplevel` 旁加 `_git.git_head(discovered_toplevel)`（新 helper，比照 `git_remote`），
payload 無 `commit` 時使用，並記 `provenance.commit_source: hook | import-discovery | backfill-approx`。

**1c. `cites`**：atomizer 發布前對 body 跑一次 regex（`[\w./-]+\.(md|py|c|h|lds|syscfg|cpp|yml|yaml|sh|json):\d+`，
保序去重、上限 32），寫入 frontmatter `cites: [{path, line}]`。純字串比對、不做路徑存在性檢查
（存在性交給 fix 5 的 verify）。`_SCALAR_ORDER` 加 `cites`，`frontmatter_io.dump` 已能輸出 list[dict]。

**1d. janitor `check_provenance_commit`**：給既有 flag 語意——true 時，`provenance.commit` 在
`projects.yaml` roots 對應 repo 中 `git cat-file -e <commit>` 失敗 ⇒ `source_invalid`（detail: `provenance_commit`）。
預設維持 false；本 fix 只實作、不開。

### Migration
`hippo knowledge backfill-provenance --memory-root <root> [--dry-run|--apply] [--project <slug>]`
（比照 `normalize-tags` 模板，D1–D4 契約：apply 走 `frontmatter_io.update()`，parse-equivalent、body 逐位元不變、冪等）：
- commit：讀 `provenance.path` 的 archive queue JSON 取 `cwd`＋`ended_at`，
  `git -C <cwd> rev-list -1 --before=<ended_at> HEAD` → 寫入並標 `commit_source: backfill-approx`；
  cwd 不存在／非 repo → 維持 `_unknown`，dry-run 報表列出原因。
- cites：對所有 body 跑 1c 的 regex；已有 `cites` 且相同 → no-op。
- dry-run 報表：可填 commit 數／不可填原因分佈／cites 命中數；預期 ≈ 3,218 筆候選、159 筆 cites。

### Tests
- `tests/test_hooks.py`：SessionEnd 在 git repo cwd 下 payload 含 `commit`（40-hex）、`git_dirty`；
  非 repo／`git` 不存在／timeout 三種情境 payload 無該鍵且 exit 0（fixture 用 `PATH` 遮蔽 git）。
- `tests/test_importer_capture_contract.py`：payload 帶 `commit` → inbox frontmatter `commit:` 非 `_unknown`；
  不帶 → `commit_source: import-discovery`。
- `tests/test_frontmatter.py` / `test_distillation_provenance.py`：5 鍵 provenance round-trip、
  `cites` round-trip、`validate()` 對缺 `cites` 的舊 note 仍通過（optional 欄位）。
- 新 `tests/test_backfill_provenance.py`：dry-run 零副作用、apply 冪等（apply→dry-run 0→apply bytes 相同）、
  `commit_source` 標記正確。
- janitor：`check_provenance_commit=true` 時 dangling commit → `source_invalid`；false 時無事件。

### Rollout / flag
1a 需 `hippo install hooks` 重新部署薄 hook 腳本（`deployment.py` 有 backup/rollback）；1b/1c 隨 wheel；
1d 由 `janitor.override.yaml` 開關。無新 feature flag：欄位 optional，舊讀者忽略未知鍵。

### Risks
- `rev-list --before` 是近似（分支可能不同）；用 `commit_source` 明確標記，永不假裝精確。
- `check_provenance_path: true` 目前實際檢查的是 archive queue JSON 是否存在（`Path(repo)/path` 中 path 為絕對路徑）；
  若日後清 archive，所有 note 會以 `source_invalid` 一次 decay——與本 fix 無直接關係，但改 provenance 語意時要記得。
- hook 多跑三個 git 子程序（各 ≤2 s，best-effort）；SessionEnd 不在互動路徑上，可接受。

## 2. Fix 2：同主題新鮮度／supersedes

### Problem
跨 session 同主題永不連結（0.4）；ranking 無新鮮度；MCU 兩專案 32 筆同標題冗餘＋大量近似標題；
08-17／08-21 flash layout 對立。

### Design
分兩層，第一層不動資料、立即見效；第二層做資料修復。

**2a. Shortlist 層近似重複折疊（keep-newest）**：`_shortlist_common.build_shortlist_and_record` 在
`claim` 前，對 12 筆 hits 依 `same_topic_key` 分組、每組只留 `captured_at` 最新者（其餘視為本輪已 offer，
寫進 offered ledger 的 `collapsed: [sl_id…]` 以保留可稽核性）。`same_topic_key` 定義（純函式，放
`paulsha_hippo/topic.py`，供 2b 共用）：
- 正規化標題：`_canonical_title`（既有）→ 去掉 project 前綴（`ot-ti-mirror `、`MCU-Octopus `）→
  以 `llm_promoter._grounding_units` 的規則切成 token（latin 詞＋CJK bigram）；
- 兩筆同組 ⇔ 同 `project`（或同 project family，見 open decision 1）且
  （canonical title 相等 ∨ 任一 `aliases` 相等 ∨ token Jaccard ≥ 0.6）。
`search()` 本身不加新鮮度（保持既有 determinism 契約與 KPI 分母穩定），折疊只在 shortlist 呈現層。

**2b. 跨 session supersedes backfill**：`hippo knowledge link-supersedes --memory-root <root> [--dry-run|--apply] [--tier auto|review]`
- **auto tier**（`--apply` 直接寫）：同 project＋canonical title 完全相等（或 aliases 相等）＋`captured_at` 嚴格較新
  ＋checksum 不同 → 新者 `supersedes: [舊]`，並 `relations.append_edge(type="supersedes")`。
  本質是把 `_attach_unambiguous_supersedes` 的 `distilled_from` 條件放寬成跨 session；同一時間戳或
  多對一（≥2 舊筆）留在 review。
- **review tier**（只出報表 `runtime/reports/link-supersedes-<ts>.md`）：Jaccard ≥ 0.6 或
  tag 交集 ≥ 2 或 `related` 互指；每行列 新／舊 slice_id、標題、captured_at、共同 token；使用者勾選後
  以 `--accept <report>` 套用。
- 新 note 路徑：`_attach_unambiguous_supersedes` 改呼叫 `topic.same_topic_key`，auto tier 條件即時套用。
- 下游不動：janitor `decay_superseded: true` 會在下一輪把舊筆 decay；`search` 已排除 decayed。

**flash layout 對的處理**：08-17（project `ot-ti-mirror`，title「ot-ti-mirror Flash Layout and Image Artifacts」）
vs 08-21（project `MCU-Octopus`，title「雙目標 Flash Layout」）——**不同 project、標題不等**，auto tier
不會連；若 open decision 1 把兩者設為同 family，review tier 會因共同 token（flash／layout）＋tag
`flash-layout` 列入報表，由人核可。2a 的 shortlist 折疊在同 family 下會直接只給 08-21。
這個例子說明：沒有 family 設定時，fix 2 對這對**無效**——這是誠實的邊界，不是設計疏漏。

### Migration
即 2b 本身。dry-run 預期：auto tier ≈ 32 筆同標題（MCU 兩專案）＋其他專案未知；review tier 報表人工過。
冪等：已有 `supersedes` 指向同一舊筆 → no-op；舊筆已 decayed → 跳過。

### Tests
- `tests/test_topic.py`：same_topic_key 對（同標題／別名／Jaccard 邊界 0.59 vs 0.60／不同 project）的判定表。
- `tests/test_shortlist_collapse.py`：12 hits 含 3 筆同主題 → 只 claim 最新、offered ledger 記 `collapsed`、
  下一輪不再 offer 被折疊者。
- `tests/test_link_supersedes.py`：auto／review 分流、多對一留 review、apply 冪等、relations edge 寫入、
  janitor 下一輪產生 `superseded` decay 事件（整合）。
- 回歸：`tests/test_atomizer_pipeline.py` 既有 `_attach_unambiguous_supersedes` 案例不變。

### Rollout / flag
2a 隨 wheel，`config.yaml` 新增 `shortlist.collapse_same_topic: true`（預設開；關掉即回到現行行為）。
2b 為一次性命令，永遠先 dry-run。

### Risks
- Jaccard 0.6 對短標題（≤3 token）過鬆；規則：token < 4 時只接受相等／別名。
- 折疊後 KPI「Agent 看過率」分母縮小（offered 數變少）；`collapsed` 記錄保留原始候選，report.py 可選擇是否計入。

## 3. Fix 3：Agent-facing view

### Problem
每次 Read 付 ≈ 2.8 KB 拿 ≈ 0.57 KB；`distiller:` 佔 43%、其餘 frontmatter 36%。wakeup `build_brief`
也逐檔讀 frontmatter（64 KB 上限）——I/O 而非 token，但同樣被拖慢。

### Options
| | (a) sidecar 拆檔 | (b) `hippo show --agent` | (c) 兩者 |
|---|---|---|---|
| 資料遷移 | 4,178 檔改寫＋新 sidecar | 無 | 有 |
| 每次讀省 | ≈ 43%（distiller 出走） | ≈ 70%（只印 6 行 header＋body） | ≈ 70% |
| Obsidian/MOC | frontmatter 變薄，MOC 不讀 distiller → 無影響；Obsidian properties 面板變乾淨 | 無變化 | 同 (a) |
| 破壞面 | `slice_frontmatter.validate` 要求 `distiller` 存在；`test_distillation_provenance` round-trip（KPI report.py 不讀 distiller，無影響） | Read hook 的 read 歸因失效（agent 不再 Read）→ 必須由 `show` 自己記 usage | 兩者 |
| 風險 | 中（改寫全部檔案） | 低 | 中 |

### Design（推薦 (b) 先做，(a) 列為後續可選）
`hippo show <slice_id|path> [--memory-root] [--agent] [--tool <t> --session-id <s>]`
- `--agent` 輸出：
  ```
  # <title>
  slice_id: … | project: … | captured_at: … | kind/artifact_kind: … | supersedes: […] | commit: … (source)
  cites: path:line, …
  ---
  <body>
  ```
  ≈ 250 B header＋body；不印 distiller／hash／attempts。
- **read 歸因**：`show --agent` 帶 `--tool/--session-id` 時，比照 `claude_post_tool_use.py` 邏輯
  （查 offered map by_id → `offered` bool）append `memory_usage.jsonl` `source=read`，
  否則 KPI 看過率會歸零。hint 提供的命令必須把這兩個參數填好（同 `_applied_hint` 做法）。
- hint 文字：`retrieval._SHORTLIST_HINT` 改為「相關項用 `hippo show --agent <slice_id> …` 取內容
  （比 Read 省 ~70% token）」；shortlist 行在既有 `— /abs/path` 之外加印 `slice_id`（保留路徑：
  offered map 以 path 為鍵，Read hook 歸因不受影響）。Copilot 的 `view` hook 路徑同理。
- (a) 若之後要做：`hippo knowledge split-distiller [--dry-run|--apply]` 把 `distiller:` 移到
  `<same-stem>.distiller.json`，frontmatter 留 `distiller_ref: <file>`；`validate()` 接受 `distiller` 或
  `distiller_ref` 其一；`safe_provenance` 不變。

### Tests
- `tests/test_show_cli.py`：`--agent` 輸出不含 `distiller`／`checksum`／`publication_id`；byte 數 ≤ body＋400；
  帶 tool/session 時 memory_usage.jsonl 多一筆 `source=read` 且 `offered` 與 map 一致；不帶時不寫。
- `tests/test_retrieval.py`：新 hint 文字、行格式含 slice_id。
- KPI：`tests/test_hippo_memory_kpi_skill.py` 加案例——經 `show` 的 read 進入看過率分子。

### Rollout / flag
隨 wheel；hint 文字切換由 `config.yaml` `shortlist.read_hint: read|show`（預設 `show`；舊 agent
skill 文件未更新前可切回 `read`）。

### Risks
- agent 不照 hint 仍用 Read：無害（回到現況）。
- 若 `show` 的 read 歸因與 Read hook 同時觸發（agent 兩者都做）→ 同一 slice 兩筆 read 事件；KPI 以 unique
  note 計，不重複計數。

## 4. Fix 4：Session-state 過濾

### Problem
104 筆 body 含 session 狀態句；例：`ot-ti-mirror-本地建置環境重現與-sop` 的「本次修改僅限
README-ARC.md… session 結束時尚未 commit」、`session-handoff-2026-08-12`。寫入即過期，卻以
`memory_layer: knowledge` 進索引與 shortlist。

### Design（三層，任一層命中即生效）
1. **Skill 規則**（`atomizer/skills/atomize-knowledge-slice.md` CONCEPT_ANALYSIS）：新增
   「排除 session 進度／狀態陳述（尚未 commit、已 push、session 結束時、handoff、下一步）；
   若整個候選只剩狀態陳述則不產生 slice」。純 prompt 變更，`skill_hash` 會變（provenance 記錄）。
2. **發布前 post-filter**（`noise.py` 新增 `episodic_reason(body) -> str|None`，**非** deletion-grade）：
   - 標題匹配 `^session-handoff` / `handoff` / `狀態` 結尾；或
   - 散文行（`_content_lines`）中 ≥ 50% 命中狀態 pattern（尚未 commit｜尚未 push｜session 結束｜
     本次修改僅限｜目前狀態｜handoff｜待 push｜下一步）；
   命中 → atomizer 在 `build_from_proposal` 後把 `memory_layer` 改為 `episodic`，照常發布到
   `knowledge/<proj>/`（檔案保留、可稽核）。`build_index` 既有分支 `non-knowledge-layer:<layer>`
   自動排除；MOC builder 同樣跳過；`slice_frontmatter.validate` 放寬為 `memory_layer in {knowledge, episodic}`。
3. **不改 `classify_noise`**：它同時被 `knowledge prune-noise` 用來**刪檔**；session 狀態不該被刪，
   只該不被撈。

### Migration
`hippo knowledge mark-episodic --memory-root <root> [--dry-run|--apply]`：對既有 knowledge notes 跑
`episodic_reason`，dry-run 列 104 筆候選（title／命中行／比例）供人略讀；apply 用 `fio.update(memory_layer=episodic)`
並 `lifecycle.append_event(event_type="archived", reason="episodic")`（用既有事件型別，不新增）。
冪等：已 episodic → no-op。回復：`--revert <slice_id>`。

### Tests
- `tests/test_noise.py`：`episodic_reason` 對「狀態句 60%」「狀態句 30%＋實質步驟」「handoff 標題」的判定；
  確認 `classify_noise` 對同一 body 仍回 `is_noise=False`。
- `tests/test_atomizer_pipeline.py`：命中的 proposal 發布為 `memory_layer: episodic`，`validate()` 通過，
  `build_index` coverage 記 `pool_excluded[non-knowledge-layer:episodic]`。
- `tests/test_mark_episodic.py`：dry-run／apply／revert／冪等。

### Rollout / flag
`config.yaml` `atomizer.episodic_filter: true`（預設開）。skill 文字變更需 `hippo upgrade apply`
把新 skill 部署到 runtime（`atomizer_config_path()` 旁）。

### Risks
- 50% 門檻誤判有實質步驟的 handoff 筆記 → 只降層不刪，且 dry-run 先過人眼；`--revert` 可救。
- `episodic` 是新的 `memory_layer` 值，`wakeup/builder` 只收 knowledge → 不受影響；
  `syncback/gate._check_schema_unextended` 只檢查 REQUIRED 集合，不受影響。

## 5. Fix 5：Follow-up ledger

### Problem
235 筆 body 含可行動句；`README-ARC.md:109` 的 133,604 已知過期 8 天無人處理。發現只存在記憶裡。

### Design
- **抽取**（純 regex，不依賴 LLM；對新舊 note 一致）：body 每行若含 actionable pattern
  （需要更新｜需加｜缺口｜TODO｜should be updated｜尚未修｜需修正｜待補）且同行或前後一行含
  `path:line`（fix 1c 的 `cites`），產生一筆 follow-up：
  ```json
  {"id": "fu-<sha16>", "slice_id": "...", "project": "...", "target": {"path": "README-ARC.md", "line": 108},
   "expected_stale": "133,604", "claim": "<該行原文>", "status": "open", "created_at": "...", "source": "regex"}
  ```
  `expected_stale`：同行中的數字／反引號字串（第一個）；無則 null（只能人工驗）。
  無 `path:line` 的可行動句也記，但 `target: null`、`verify` 一律 `unverifiable`。
- **儲存**：`runtime/ledger/followups.jsonl` append-only，狀態以事件 fold（比照 `ledger/processing.py`）：
  `opened` / `verified-open` / `resolved-in-source` / `closed-manual` / `unverifiable`。
- **CLI** `hippo followups`：
  - `list [--project] [--status open]`：fold 後表格；
  - `verify [--project]`：對 `target != null` 者，解析 path → 以 `projects.yaml` roots（`_project_roots`）為
    base 逐一嘗試（相對路徑）或絕對路徑；讀該行±2 行，`expected_stale` 仍在 ⇒ `verified-open`，
    不在 ⇒ `resolved-in-source`（自動關），檔案／行不存在 ⇒ `unverifiable`；唯讀、不改任何 repo 檔案；
  - `close <id> --reason`：人工關閉；
  - `extract [--dry-run|--apply]`：對既有 notes 回填（migration）。
- **排程**：`dream/cli.py` 在 janitor 之後加 `followups_fn`（verify，唯讀＋ledger append；失敗只記 warning
  不影響 dream 結果等級）。**呈現**：`wakeup/builder.build_brief` Recent 區塊後加一行
  「本專案 open follow-ups：N（`hippo followups list --project X`）」；不進 per-prompt shortlist（避免噪音）。
  `custom-skills/hippo-memory-kpi/scripts/report.py` 加 `followups_open / resolved_in_source_7d` 兩欄。

### README-ARC.md:109 端到端
1. `followups extract --apply` 掃到 08-17 note 的行「README-ARC.md 記載的舊 FLASH 數字 `133,604 B`
   已與 fresh build 的 `133,372 B` 不符，需要更新（`README-ARC.md:108`）」→
   `fu-…` target `README-ARC.md:108`，`expected_stale` = `133,604`，`opened`。
2. 下一次 dream `followups verify`：roots 含 `<ot-ti-mirror 專案根目錄>` → 讀 108±2 行
   → 第 109 行含 `133,604` → `verified-open`。
3. session start brief：「ot-ti-mirror open follow-ups：1」。使用者修 README 後下一輪 verify →
   `resolved-in-source` 自動關。
4. 若使用者決定不改（例如數字改由 build 產生），`followups close fu-… --reason "moved to generated"`。

### Migration
`followups extract` 即 migration；dry-run 預期 ≈ 235 筆候選，其中有 target 的 ≤ 159。冪等：`id` 由
（slice_id, target, claim）hash 派生，重跑不重複 `opened`。

### Tests
- `tests/test_followups_extract.py`：有／無 target、`expected_stale` 抽取、id 冪等。
- `tests/test_followups_verify.py`：tmp repo fixture——stale 值仍在／已移除／檔案不存在／行號漂移（±2 內找到）
  四種狀態轉換；verify 不寫 repo（fixture mtime 不變）。
- `tests/test_dream_cli.py`：followups 階段失敗不改變 dream 結果等級。
- `tests/test_wakeup_builder.py`：brief 含 open 計數行；0 筆時不印。

### Rollout / flag
`config.yaml` `followups.enabled: true`；dream 階段受同 flag 控制。ledger 新檔，無 schema migration。

### Risks
- regex 抽取召回率有限（235 是 pattern 命中，不是真值）；先求精確（有 target 者），LLM 抽取列為後續
  （每個 finding 加 optional `followups: []` 欄位——`llm_output` 對未知 proposal 鍵目前是 soft-drop，
  前向相容）。
- verify 讀的是 roots 下的工作樹（可能是使用者未 commit 的狀態）；狀態只作提示，不作判定依據。

## 6. Later：recall precision（`kind` / prompt 型別過濾 / `verify:`）

不在本批範圍，因為需要兩個目前不存在的東西：(i) 對 user prompt 的型別分類（meta／實作／查證），
(ii) 對既有 4,178 筆的 `kind` 回填（需 LLM 或啟發式，準確度未證）。草案：
- frontmatter `kind: fact | procedure | lesson | meta | episodic`（episodic 由 fix 4 先落地）；
  新 note 由 skill 輸出、舊 note 以 tags／artifact_kind 啟發式回填並標 `kind_source: heuristic`。
- shortlist：prompt 含「工具／流程／記憶／skill／hippo／怎樣…幫助」→ 只撈 `meta|lesson`；含 `DIO|net|pin|0x` → 優先 `fact`。
- `procedure` note 可帶 `verify: "<shell>"`（唯讀命令），由 fix 5 的 verify 階段執行，失敗 ⇒ 降權
  （search 加 `stale` 欄位，score +0.05），不 decay。
先做 1–5 之後，用 KPI（看過率／採用率）判斷是否值得。

## 7. 優先順序

| 序 | 項目 | 理由 |
|---|---|---|
| 1 | 1a hook 填 commit | ≤ 30 行、無遷移、所有新 note 立即受益；其他 fix 的 provenance 基礎 |
| 2 | 3b `hippo show --agent`＋hint | 無資料變更，每次讀省 ≈ 70% token，今天就能用 |
| 3 | 2a shortlist 折疊 keep-newest | 無資料變更，直接解「舊蓋新」；flag 可關 |
| 4 | 1c cites＋1b import fallback | 5 的前置；小 |
| 5 | 5 follow-ups | 解「發現不閉環」；依賴 cites |
| 6 | 4 episodic | 104 筆一次性＋prompt 規則；獨立 |
| 7 | 2b supersedes backfill | 需人工 review tier；效益在 2a 之後遞減 |
| 8 | 1d、3a、6 | 可選／後續 |

## 8. paulsha-cortex 相容性

grep `paulsha-cortex/paulsha_cortex`：不讀 hippo note 檔案的 `distiller`／`provenance`／`supersedes`；
唯一耦合是 `lib/lifecycle/schema.PHASES` 與 cortex `persona/contract.PHASES` 的常數對齊測試
（本文不動 PHASES／ARTIFACT_KINDS／REQUIRED_FRONTMATTER_FIELDS）。`memory_layer: episodic` 為新值，
cortex 不讀該欄位。`paulshaclaw` 亦無消費者。結論：零相容性變更；`syncback/gate` 的 schema-unextended
條件（只檢 REQUIRED 集合）維持通過。

## 9. Open decisions（附建議）

1. **Project family**：`projects.yaml` 是否加 `family: [MCU-Octopus, ot-ti-mirror, …]` 讓 fix 2 跨兄弟
   repo 判同主題？**建議加**（opt-in 清單，預設空），否則 flash layout 這類「父目錄 vs 子 repo」對永遠連不上。
2. **3a sidecar 拆檔**：做不做？**建議先不做**；3b 已拿到主要收益，3a 只在 Obsidian 使用體驗或 wakeup
   I/O 成為問題時再上。
3. **episodic 去向**：降層保留 vs 刪除？**建議保留**（可逆、可稽核、不影響 KPI 分母定義）。
4. **follow-ups 呈現位置**：wakeup brief 一行 vs 每 prompt shortlist？**建議 brief**；shortlist 已有噪音問題。
5. **backfill 的近似 commit**：寫入並標 `backfill-approx` vs 維持 `_unknown`？**建議寫入並標記**；
   下游（fix 1d、未來 board-facts 連結）需要一個可用的錨點，標記保證不被誤讀為精確。

## 10. 範圍外：TTL 90 天（決定：維持預設，不加 override）

`janitor/lifecycle.yaml` `default_decay_age_days: 90`，`_ttl_base = max(captured_at, active_since, valid last_read_at)`。
2026-08-25 驗證：knowledge 層最早 `created_at` 為 2026-06-17（60–90 天 bucket 153 筆、30–60 天 966 筆），
首波 `ttl_expired` 約 **2026-09-15**；當日 `hippo janitor scan --dry-run` scanned 3,244 / decayed 0；janitor 只在
`hippo dream run` 或手動 `hippo janitor scan` 執行，無 cron。decay 為軟性（檔案留在磁碟、`search --include-decayed`
可取回、只在同 source 重新 import 時 reactivation）。**使用者決定：90 天未被使用即降級是合理政策，不加 override。**
已知量測盲區（只有 agent 實際 Read 才記 read event，shortlist 出現不算）由 fix 3b 的 `show --agent` read 歸因修正。
若日後要調整，override 的實際路徑是 `paths.config_path("janitor.override.yaml")` =
`~/.config/paulshaclaw/janitor.override.yaml`（不是 `~/.config/paulsha-hippo/`）。fix 2b 依賴同一套 decay，實作前先
`hippo janitor scan --dry-run` 保留報表，逐筆看 `plan[]` 的 reason。

## 11. 部署面對照

| 變更 | 載體 | 生效方式 |
|---|---|---|
| hook 薄腳本（1a） | `paulsha_hippo/hooks/*.py` → `~/.agents/memory/hooks/` | `hippo install hooks`（冪等、含 backup） |
| shortlist／show／topic／followups／noise | wheel | `hippo upgrade plan/prepare/apply` |
| skill 文字（4） | `atomizer/skills/*.md` → runtime skill 路徑 | 同 upgrade；`skill_hash` 變更會反映在新 note provenance |
| config 新鍵 | `~/.config/paulsha-hippo/config.yaml` | `hippo config migrate plan/apply`（既有 fail-closed 機制；新鍵皆有預設值） |
| janitor 1d | `janitor.override.yaml` | 手動 |
