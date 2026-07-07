---
name: atomize-knowledge-slice
description: "把單一 session 的 fragments 蒸餾成可驗證的 knowledge slices；允許語意拆分、跨 fragment 合併、標籤繼承與關聯推導。"
triggers:
  - atomize knowledge slice
  - llm atomizer
  - 語意原子化
---

# Atomize Knowledge Slice

## 用途
- 把同一個 session 的 fragments 轉成一批 knowledge slices。
- 每個 slice 只保留一個可獨立理解、可單獨重用的概念。
- 允許一個 fragment 拆成多個 slices，也允許多個 fragments 合併成一個 slice。

## Seed principles
本 skill 依 TechVault / WorkVault 的 `atomized_from` 樣本模式手工蒸餾，只保留可重用原則，不複製原筆記內容；PersonalVault 不在此 skill 範圍。

1. **One concept per slice**
   - 一個 slice 只講一個主題、決策、流程、對照、規格或結論。
   - 如果同段內容同時混了背景、實作、風險、FAQ，應拆成多個 slices。
2. **Cross-fragment merge**
   - 若兩個以上 fragments 其實在補同一概念，合併成單一 slice。
   - `source_fragment_indices` 必須列出所有被合併的 fragment index。
3. **Minimum atomic size**
   - 太薄、只剩一句結論且無法單獨重用時，不要硬拆成獨立 slice。
   - 優先併入最近的主題 slice；只有能獨立被引用時才保留獨立 slice。
4. **Shared preamble handling**
   - 共用背景只保留支撐該概念所必需的最小上下文。
   - 若同一背景會被多個 slices 重複依賴，改抽成一個 overview / summary slice，其餘 slices 以 `relates_to` 連回去。
5. **Naming**
   - `title` 要短、穩定、可直接當別的 slice 的參照名稱。
   - 優先用「主題前綴 + 概念後綴」或「領域 + 子題」命名，避免模糊標題如 `misc`、`notes-1`。
6. **Tag inheritance**
   - 保留 session / fragment 中反覆出現的全域 tag，再加上該概念專屬 tag。
   - 避免把只在局部出現的細節 tag 套到所有 slices，也避免重複同義 tag。
7. **Relation patterns**
   - 明確的同批次主題關聯用 `relates_to`。
   - 明確提到的人名、系統、模組、公司、元件等可用 `mentions`。
   - 關聯要節制；只保留對後續檢索或導覽有幫助的強關聯。

## 六階段工作流

### 1. SESSION_SCAN
- 讀完整個 session fragments，不要逐段獨立輸出。
- 先找出有哪些主題群、哪些 fragments 屬於同一概念、哪些只是共用背景。

### 2. CONCEPT_ANALYSIS
- 為每個候選概念判斷：
  - 核心命題是什麼。
  - 需不需要跨 fragment 合併。
  - 是否只是前言、摘要、FAQ、比較、規格、步驟、風險之一。
- 去除贅詞、寒暄、重複背景與無法驗證的延伸推論。

### 3. SLICE_PLANNING
- 規劃最小但足夠的 slice 集合。
- 若多個候選只差同一主題的不同片段，應合併。
- 若一大段同時包含 overview 與多個子題，先保留 overview slice，再把子題各自獨立。

### 4. DRAFT_SLICES
- 每個 slice 的 `body` 使用蒸餾後 markdown，保留可重用的結論、結構、步驟、對照或規格。
- 不要照抄 fragments；改寫成精簡、可單獨閱讀的知識切片。
- `body` 必須非空，且能在不回看原 session 的情況下成立。

### 5. PROJECT_TAG_RELATION_PASS
- **Project attribution**
  - 每個 slice 必須從提供的 known projects 中選一個最貼切的 `project`。
  - 若根本沒有提供 known projects 清單，或沒有可信歸屬、內容跨多專案且無單一主軸時，才用 `_unknown`。
- **Tag strategy**
  - `tags` 先放全域 tag，再放概念 tag。
  - tag 應偏向檢索鍵，而不是句子或過長描述。
- **Relation guidance**
  - `relates_to` 只能指向同一批輸出的另一個 slice，且 `target_title` 必須精確等於對方 `title`。
  - `mentions` 使用 `{ "type": "mentions", "entity": "NAME" }`；entity 用穩定名稱，不加多餘敘述。

### 6. VALIDATE
- 檢查是否真的做到 one-concept-per-slice。
- 檢查是否漏列合併來源的 `source_fragment_indices`。
- 檢查 `project` 是否為已知 project 或 `_unknown`。
- 檢查 `relations` 是否只含 `relates_to` / `mentions`。
- 檢查所有 `title` 在同一批輸出內唯一且可互相引用。

## Output contract
Return ONLY an inline JSON array.
The first character of your response must be `[` and the last character must be `]`.
Do NOT create files, write files, save files, or claim that you updated any file or index.
Do NOT return prose, narration, summaries, markdown fences, or any text before or after the JSON array.

Each item in the array must be an object with these fields:

- `title`: slice 標題；短、穩定、可被其他 slices 以 `target_title` 參照。
- `artifact_kind`: 必須是下列其中之一：`research`、`spec`、`roadmap`、`test`、`task`、`todo`、`plan`、`report`、`review`、`ship-record`、`gate-report`。
- `project`: 從 known projects 擇一；若未提供清單或無法可靠歸屬才用 `_unknown`。
- `tags`: 字串陣列；包含全域 tag 與概念 tag。
- `body`: 非空 markdown；為蒸餾後內容，不可只是標題重複。
- `source_fragment_indices`: 整數陣列；列出此 slice 取材的所有 fragment index。
- `relations`: 關聯陣列；只允許下列兩種物件：
  - `{ "type": "relates_to", "target_title": "<another slice title>" }`
  - `{ "type": "mentions", "entity": "<stable entity name>" }`

Inline example shape (for reference only; your actual response must still be only the array):
`[{"title":"example overview","artifact_kind":"report","project":"paulshaclaw","tags":["atomizer","overview"],"body":"Distilled markdown content.","source_fragment_indices":[0,1],"relations":[{"type":"relates_to","target_title":"example detail"},{"type":"mentions","entity":"BRCM"}]}]`
