### Added
- `contrib/local-harness/`：地端 vLLM 專用 harness（#55）——`co-gem` profile 的 direct API 引擎（guided decoding schema v1、`enable_thinking:false`、map-reduce 原子化、retry-with-repair、任務嗅探），含小模型版 atomize skill 與部署說明。不進 wheel、不被 package 引用，僅作版控與部署來源。
