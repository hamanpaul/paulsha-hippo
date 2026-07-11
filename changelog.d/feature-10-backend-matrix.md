### Added
- Backend preset registry（`paulsha_hippo/backends.py`，契約 7）：`claude/codex/copilot-headless` 三個實測 argv presets（custom-argv 機制包裝、機制零新增）＋`gemini-headless`（僅 rc=41 觀察、無 round-trip 實證——spec §8 不猜 argv，候選 argv 降記錄）／`antigravity-headless`（命令契約未確認）標 unavailable；`hippo init --backend` 選單由 registry 驅動（unavailable 顯示不可選）、寫入時 argv[0] 絕對路徑化（fail-closed 不落半套 config）；`hippo doctor` 新增 per-preset probe 報告（service-effective 環境）——裸 doctor 解析級（`shutil.which`，不喚起 backend），`--probe-live`／`--fix-backend`／`HIPPO_DOCTOR_LIVE_PROBE=1` 才逐 preset 實跑 `<exe> --version`（暴露 node-shebang 類 service PATH 故障），與 configured-backend probe 共用同一 opt-in 閘門；probe 目標吐非 UTF-8 位元組（跑錯 binary／crash dump／locale 錯亂）時 fail-closed 判 FAIL、不讓 `UnicodeDecodeError` 逸出崩潰整支 doctor。
- `hippo dream supervise` 新增 `--once`／`--max-load`／`--promoter`／`--agent-command`：無 systemd 主機可前台單輪驗收（#10 原始 checklist 項）。
- 測試矩陣：mock 情境 ×4（散文包 JSON／截斷／non-zero／timeout→promoted/parked(invalid_output)/transient）、真蒸餾 smoke ×3 available preset（`PSC_ATOMIZE_LIVE` gate、probe 失敗轉 skip 回報）＋available⊆smoke 矩陣覆蓋 guard、openai-compatible 真端點 integration smoke（`HIPPO_SMOKE_OPENAI_*` gate）、supervise 無 systemd E2E。

### Docs
- 新增 `docs/backend-matrix.md`（preset argv 契約／probe／前置條件／實測狀態／unavailable 升級前提與 gemini rc=41 證據）；README backend 段同步（R-18）。
