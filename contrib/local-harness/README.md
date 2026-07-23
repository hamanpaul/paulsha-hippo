# hippo-local-harness（地端 vLLM 專用 harness）

> 設計與實測依據：[#55](https://github.com/hamanpaul/paulsha-hippo/issues/55)

`co-gem` external-agent profile 的地端 direct-vLLM 引擎。**不屬於 hippo package**（不進 wheel、不被 `paulsha_hippo/**` 引用）；`contrib/` 僅作版控與部署來源。

## 邊界（#55 但書）

- hippo 不管理 model／entry point／API key；本 harness 沿用既有 BYOK env file
  `~/.config/paulshaclaw/copilot-local-vllm.env` 為唯一真源。
- 對 hippo 的介面即 backend-matrix 的 tokenized argv 契約：
  `co-gem --model {MODEL} --effort {EFFORT} --headless --stdin`（prompt 走 stdin、回應走 stdout、fail-closed exit）。
- zero-tool 為結構性事實：對 vLLM 的請求不含任何 tools 欄位。

## 檔案

| 檔案 | 部署位置 | 說明 |
|---|---|---|
| `harness.py` | `~/.local/share/hippo-local-harness/` | 引擎：guided decoding（schema v1）、`enable_thinking:false`、temp 0 + seed、map-reduce 原子化（枚舉→逐概念撰寫）、retry-with-repair、任務嗅探（title/skillopt 走 plain completion） |
| `schema-v1.json` | 同上 | hippo canonical response schema v1（guided decoding 用） |
| `atomize-skill-smallmodel.md` | 同上 | 小模型版 atomize skill（#55 方案 4；`config.yaml` `skill_path` 支援絕對路徑，窗口期切換） |
| `co-gem.launcher` | `~/.local/bin/co-gem`（去掉 `.launcher` 副檔名，`chmod +x`） | 薄殼 launcher |

## 部署

```bash
mkdir -p ~/.local/share/hippo-local-harness
cp harness.py schema-v1.json atomize-skill-smallmodel.md ~/.local/share/hippo-local-harness/
cp co-gem.launcher ~/.local/bin/co-gem && chmod +x ~/.local/bin/co-gem
# 驗收
echo '測試: 僅回 canonical no_findings JSON' | co-gem --model local --effort low --headless --stdin
```

## 實測（2026-07-23，gemma4-26b-a4b-nvfp4）

- 噪音 session 判定：2–3s、合法 `no_findings`（舊 copilot 鏈路 7–20s）
- 真內容 session（ce7c515a, 48KB）：map-reduce 6 findings／160s（舊鏈路 >320s 逾時＋JSON 畸形；單呼叫模式 1 個巨型 slice）
- 對照 cg（glm-5.2）同 session：12 findings／115.7s

## 已知邊界

- gemma4 為 reasoning 模型：thinking 無上限、會吃光 `max_tokens`——guided pass 一律 `enable_thinking:false`；`{EFFORT}` low/medium/high 對應 thinking off／reasoning_effort low（僅 plain 任務）／模型預設。
- copilot CLI 鏈路的教訓（不要走回頭路）：`--available-tools=`（空值）+`--allow-all-tools` 下工具**真的會執行**；`--deny-tool='*'` 會扼殺回應（空 stdout）。
