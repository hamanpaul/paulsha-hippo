---
name: atomize-knowledge-slice-smallmodel
description: "地端小模型版 atomize skill（issue #55 方案 4）：前置輸出契約、精簡流程、findings 上限。僅供 hippo-local-harness（co-gem）窗口期使用。"
triggers:
  - atomize knowledge slice
  - llm atomizer
  - 語意原子化
---

# Atomize Knowledge Slice（small-model variant）

## Output contract（最重要，先讀這段）
Return ONLY a canonical JSON object. No prose, no markdown fences, no text before or after.
The first character of your response must be `{` and the last character must be `}`.

Valid no-findings example:
`{"schema_version":1,"disposition":"no_findings","reason":"no durable findings","findings":[]}`

Valid findings example (shape reference only):
`{"schema_version":1,"disposition":"findings","reason":null,"findings":[{"title":"serialwrap broker single-writer 模型","artifact_kind":"spec","project":"serialwrap","tags":["serialwrap","uart","arbitration"],"body":"broker 以單一 writer 仲裁多 agent UART 存取；讀取共享、寫入序列化，session-safe 命令執行由 broker 排隊。","source_fragment_indices":[2,3],"relations":[{"type":"mentions","entity":"serialwrap"}]}]}`

Rules:
- `disposition=findings` → `reason` 必須是 `null`、`findings` 至少 1 個。
- `disposition=no_findings` → `findings` 必須是 `[]`、`reason` 必須是非空字串。
- 每個 finding 必備欄位：`title`、`artifact_kind`、`project`、`tags`、`body`、`source_fragment_indices`、`relations`；不得有其他欄位。
- `artifact_kind` 只能是：`research`、`spec`、`roadmap`、`test`、`task`、`todo`、`plan`、`report`、`review`、`ship-record`、`gate-report`。
- `project` 從 known projects 擇一；無法歸屬才用 `_unknown`。
- `source_fragment_indices` 為非空整數陣列。
- `relations` 只允許 `{"type":"relates_to","target_title":"<同批另一 slice 的 title>"}` 或 `{"type":"mentions","entity":"<穩定名稱>"}`；可以是空陣列。
- Do NOT create/write/save files, and do NOT claim you updated any file or index.

## 工作原則（精簡版）
1. **One concept per slice（最重要）**：一個 slice 只講一個主題／決策／流程／規格／結論。**嚴禁把不相關的概念合併成一個大 slice**；若 title 需要用「+」「與」「and」連接兩個主題，就必須拆成兩個 slices。
2. **內容豐富的 session 通常有 3–8 個獨立概念**：工作 session 常同時包含（a）遇到的 bug 與 workaround、（b）設計決策與規格、（c）操作流程或環境坑、（d）待辦與範圍界定——這些各自都是獨立 slice。上限 8 個；寧可多個小而準的 slice，不要一個大雜燴。
3. **可獨立重用才算數**：body 要在不回看原 session 下成立；太薄（一句話）就併入相近 slice 或捨棄。
4. **跨 fragment 合併**：多個 fragments 補同一概念時合併，`source_fragment_indices` 列出全部來源。
5. **蒸餾不照抄**：body 用精簡 markdown 改寫，去除寒暄、贅詞、重複背景。
6. **標題短而穩**：「主題前綴 + 概念後綴」，可被其他 slice 以 `target_title` 引用；禁止 `misc`、`notes-1` 這類模糊標題。
7. **判斷噪音**：若整個 session 只有問候、標題生成請求、純 metadata、系統樣板，輸出 `no_findings` 並在 `reason` 一句話說明。

## 流程（三步）
1. **SCAN**：讀完全部 fragments，找出主題群與純背景。
2. **DISTILL**:為每個值得保留的概念寫一個 slice（≤5 個），跨 fragment 合併、去噪、改寫。
3. **VALIDATE**：逐項核對 Output contract 的 Rules；任何欄位不合規就修正後再輸出。
