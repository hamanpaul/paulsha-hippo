# hippo-local-harness（地端 vLLM 專用 harness）

> 設計與實測依據：[#55](https://github.com/hamanpaul/paulsha-hippo/issues/55)

`co-gem` external-agent profile 的地端 direct-vLLM 引擎。**不屬於 hippo package**（不進 wheel、不被 `paulsha_hippo/**` 引用）；`contrib/` 僅作版控與部署來源。

## 邊界（#55 但書，2026-07-23 修訂）

- hippo core 不管理 model／entry point／API key；地端直連引擎讀**專用最小 env**
  `~/.config/paulsha-hippo/local-vllm.env`（見 `local-vllm.env.tmpl`；僅兩個必要值：
  `HIPPO_LOCAL_VLLM_BASE_URL` / `HIPPO_LOCAL_VLLM_MODEL`，key 為選填）。此檔只被
  harness 讀取，hippo core 不碰。
- 路由決策全在 `config.yaml` profiles：哪個 profile 做 atomization、fallback 順序、
  各 profile 的 launcher argv——launcher 只是 transport 執行者。
- 對 hippo 的介面即 backend-matrix 的 tokenized argv 契約：
  `co-gem --model {MODEL} --effort {EFFORT} --headless --stdin`（prompt 走 stdin、回應走 stdout、fail-closed exit）。
- zero-tool 為結構性事實：對 vLLM 的請求不含任何 tools 欄位。

## 檔案

| 檔案 | 部署位置 | 說明 |
|---|---|---|
| `harness.py` | `~/.local/share/hippo-local-harness/` | 直連引擎：guided decoding（schema v1）、`enable_thinking:false`、temp 0 + seed、map-reduce 原子化（枚舉→逐概念撰寫）、retry-with-repair、任務嗅探（title/skillopt 走 plain completion） |
| `schema-v1.json` | 同上 | hippo canonical response schema v1（guided decoding 用） |
| `atomize-skill-smallmodel.md` | 同上 | 小模型版 atomize skill（#55 方案 4；`config.yaml` `skill_path` 支援絕對路徑，窗口期切換） |
| `local-vllm.env.tmpl` | `~/.config/paulsha-hippo/local-vllm.env`（填值、`chmod 600`） | 直連引擎專用 env：BASE_URL + MODEL（key 選填） |
| `launchers/co-gem` | `~/.local/bin/co-gem` | 直連 harness 薄殼 |
| `launchers/cg` | `~/.local/bin/cg` | copilot 鏈路薄殼（llm-share BYOK；secret env `~/.config/paulshaclaw/cg-llmshare.env`，結構同 copilot-local-vllm.env.tmpl 慣例） |
| `launchers/hippo-copilot-headless-core` | `~/.local/bin/` | copilot 鏈路共用引擎：zero-tool（`--available-tools=__none__ --disable-builtin-mcps`）、拋棄式 HOME（斷 session 遞迴）、平衡 JSON 抽取、fail-closed |
| `launchers/agy-headless` | `~/.local/bin/` | agy（Antigravity）契約橋接：stdin→argv、model sentinel 映射（operator override 註記見檔頭） |

## 部署

```bash
mkdir -p ~/.local/share/hippo-local-harness
cp harness.py schema-v1.json atomize-skill-smallmodel.md ~/.local/share/hippo-local-harness/
cp launchers/* ~/.local/bin/ && chmod +x ~/.local/bin/{co-gem,cg,hippo-copilot-headless-core,agy-headless}
cp local-vllm.env.tmpl ~/.config/paulsha-hippo/local-vllm.env  # 填入實際值後 chmod 600
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
