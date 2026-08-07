---
type: fix
---
- 部分處理 issue #99（fail-fast 半）：tier-2 `cg` profile 宣告 `max_session_chunks: 6`。cg 對一般 session 是目前最穩的 profile（organic 勝出 80.9%、promote 100%），問題特定於大 payload——>6 chunks 的 session（實例 213KB／47+ fragments）燒 153s 後以 exit 3 吐出不可解析 JSON。宣告閘門後這類 session 以 `session_size` 立即不合格、直接落到 tier-3，而不是先浪費 153s 再失敗。
- 兩道天花板刻意相鄰：tier-1 為 7（#89），cg 為 6，因此 cg 實際只會接到「tier-1 因其他原因失敗的 ≤6 chunk session」。tier-3 維持不設限——它是最後一棒，若也能以 size 婉拒，大 session 就無處可去。
- 同步三處，避免「宣告了但沒同步」：`agent_profiles.default_profiles()` 的 canonical row（新增具名常數 `_CG_MAX_SESSION_CHUNKS`）、出貨模板 `paulsha_hippo/atomizer/atomizer.yaml`，以及既有的 template↔canonical parity 測試。該 parity 測試在本次實際擋下了只改一邊的版本。
- 新增測試：模板宣告值、啟用狀態下 7 chunks 判 `session_size` 不合格、6 chunks 不受此閘門影響。測試需以 `dataclasses.replace(cg, enabled=True)` 建構——出貨模板把 cg 設為 `enabled: false`（部署端才啟用），而 `eligible()` 的 `disabled` 檢查會先短路，直接用模板 profile 驗不到 size 閘門本身。
- **未涵蓋**：copilot 鏈路的輸出上限／截斷行為量測仍未進行，「cg 對大 payload 為何不可靠」未定調，issue #99 保持開啟。本次只是止血，不是根因修復。
- **部署**：live config（`~/.config/paulsha-hippo/config.yaml`）的 cg profile 目前 `max_session_chunks` 為未設定，且該檔為使用者值勝出、不會被模板刷新，需手動同步才會生效。
