### Added
- `contrib/local-harness/`：地端 vLLM 專用 harness（#55）——`co-gem` profile 的 direct API 引擎（guided decoding schema v1、`enable_thinking:false`、map-reduce 原子化、retry-with-repair、任務嗅探），含小模型版 atomize skill 與部署說明。不進 wheel、不被 package 引用，僅作版控與部署來源。
- `contrib/local-harness/launchers/`：全套 launcher 入版控——`cg`＋`hippo-copilot-headless-core`（copilot 鏈路 zero-tool 防禦）、`agy-headless`（契約橋接）、`co-gem`；直連引擎改讀專用最小 env `local-vllm.env`（BASE_URL＋MODEL，key 選填），附 `.tmpl`。
