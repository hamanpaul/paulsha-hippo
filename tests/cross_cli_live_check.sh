#!/usr/bin/env bash
# cross_cli_live_check.sh — #18 live 實證（補充證據；PR-F Task 8）
# CI 迴歸保護由 hermetic 整合測試 tests/test_cross_cli_funnel_integration.py 承擔；
# 本腳本補真實 CLI 的平台注入實證（#18 關單＝hermetic 鏈綠＋本腳本至少一次成功）。
# 真實 adapter E2E（Claude 平台；copilot 平台對稱 leg 見下半，capability matrix
# 2026-07-11 復測 supported 後接線）：
#   A. 相關 prompt → prompt-time hook 注入 shortlist → offered 事件（平台注入，非手動 recall）
#   B. agent Read/view 該 slice → PostToolUse(Read)/postToolUse(view) → read 事件（offered=true、同 session）
#   C. negative control：無關 prompt → 不新增 offered 事件
#   D. applied：agent 依 shortlist 尾行指引呼叫 hippo usage mark-applied → applied 事件
# 需本機已登入對應 CLI；手動執行、不進 CI；會消耗少量模型額度。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_BASE="$ROOT_DIR/.psc_tmp"
mkdir -p "$TMP_BASE"
TMP_DIR="$(mktemp -d "$TMP_BASE/crosscli-XXXXXX")"
trap 'rm -rf -- "$TMP_DIR"' EXIT

MEM="$TMP_DIR/memory"
PROJ="$TMP_DIR/proj"
mkdir -p "$MEM/knowledge/proj" "$PROJ"
git init -q "$PROJ"   # 讓 resolve_project 以 toplevel basename 解出 "proj"（避開外層 repo）

cat >"$MEM/knowledge/proj/serialwrap.md" <<'EOF'
---
memory_layer: knowledge
slice_id: sl-e2e0000000000001
project: proj
title: SerialWrap 埠設定
captured_at: '2026-07-10T00:00:00Z'
---
SerialWrap 的 UART 埠必須以 115200/8N1 開啟，否則靜默丟包。
EOF

PYTHONPATH="$ROOT_DIR" python3 - "$MEM" <<'PYEOF'
import sys
from pathlib import Path
from paulsha_hippo.moc import search as S
S.build_index(Path(sys.argv[1]), link_weights={})
print("[e2e] index built")
PYEOF

# 偽 hooks venv python：讓 shortlist 尾行的 mark-applied 指引在無正式安裝的暫存
# memory root 下也可被 agent 直接執行（正式部署由 install.sh 建真 venv）。
mkdir -p "$MEM/hooks/.venv/bin"
cat >"$MEM/hooks/.venv/bin/python" <<EOF
#!/usr/bin/env bash
PYTHONPATH="$ROOT_DIR" exec python3 "\$@"
EOF
chmod +x "$MEM/hooks/.venv/bin/python"

SETTINGS="$TMP_DIR/settings.json"
cat >"$SETTINGS" <<EOF
{
  "hooks": {
    "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command",
      "command": "PSC_MEMORY_ROOT=$MEM PYTHONPATH=$ROOT_DIR python3 $ROOT_DIR/paulsha_hippo/hooks/claude_user_prompt_submit.py",
      "timeout": 10}]}],
    "PostToolUse": [{"matcher": "Read", "hooks": [{"type": "command",
      "command": "PSC_MEMORY_ROOT=$MEM PYTHONPATH=$ROOT_DIR python3 $ROOT_DIR/paulsha_hippo/hooks/claude_post_tool_use.py",
      "timeout": 10}]}]
  }
}
EOF

OFFERED="$MEM/runtime/ledger/offered.jsonl"
USAGE="$MEM/runtime/ledger/memory_usage.jsonl"

echo "[e2e] A+B+D: 相關 prompt（真實 claude session）"
(cd "$PROJ" && printf '%s\n' \
  "我在設定 SerialWrap 的序列埠。若系統浮現相關記憶短清單，請用 Read 開啟清單中的絕對路徑；若其內容影響了你的建議，請依清單末行指示執行 mark-applied 回報，然後總結建議。" \
  | claude -p --settings "$SETTINGS" --allowedTools "Read,Bash") || true

test -s "$OFFERED" || { echo "[e2e] FAIL: offered.jsonl 空——shortlist 未注入"; exit 1; }
grep -Fq '"tool": "claude-code"' "$OFFERED"
grep -Fq 'sl-e2e0000000000001' "$OFFERED"
echo "[e2e] offered（平台注入）OK"

grep -Fq '"source": "read"' "$USAGE" || { echo "[e2e] FAIL: 無 read 事件"; exit 1; }
grep -Fq '"offered": true' "$USAGE"
PYTHONPATH="$ROOT_DIR" python3 - "$MEM" <<'PYEOF'
import json, sys
from pathlib import Path
mem = Path(sys.argv[1])
off = [json.loads(l) for l in (mem / "runtime/ledger/offered.jsonl").read_text().splitlines() if l.strip()]
use = [json.loads(l) for l in (mem / "runtime/ledger/memory_usage.jsonl").read_text().splitlines() if l.strip()]
reads = [e for e in use if e.get("source") == "read" and e.get("offered") is True]
assert off and reads, f"missing legs: offered={len(off)} reads={len(reads)}"
bound = {e["session_id"] for e in off} & {e["session_id"] for e in reads}
assert bound, "offered/read session_id 不一致——非同一 session 綁定"
print("[e2e] offered→read 同 session 綁定 OK:", sorted(bound)[0])
PYEOF

echo "[e2e] C: negative control（無關 prompt 不觸發 offer）"
BEFORE=$(wc -l <"$OFFERED")
(cd "$PROJ" && printf '%s\n' "請解釋 TCP 三次握手的流程，不需要讀任何檔案。" \
  | claude -p --settings "$SETTINGS") || true
AFTER=$(wc -l <"$OFFERED")
test "$BEFORE" -eq "$AFTER" || { echo "[e2e] FAIL: 無關 prompt 竟新增 offered（$BEFORE→$AFTER）"; exit 1; }
echo "[e2e] negative control OK（offered 行數 $BEFORE 不變）"

echo "[e2e] D: applied 實證檢查"
if grep -Fq '"kind": "applied"' "$USAGE" && grep -Fq '"slice_id": "sl-e2e0000000000001"' "$USAGE"; then
  echo "[e2e] applied 實證 OK"
else
  echo "[e2e] WARN: applied 未出現（agent 未遵循指引）——可整支重跑（上限 2 次）；"
  echo "       仍無 → PR 僅 Closes #17，#18 留 open 並留言記錄"
  echo "       （hermetic 鏈綠不足以單獨關 #18——平台實證仍缺；spec §3.6 關單條件）"
fi

echo "=== 去識別證據（claude；貼入 docs/cross-cli-capability-matrix.md）==="
sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" "$OFFERED" | sed 's/^/offered| /'
sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" "$USAGE"   | sed 's/^/usage  | /'
echo "[e2e] claude PASS（applied 見上方判定）"

# ==================================================================
# copilot 對稱 leg（userPromptSubmitted / postToolUse(view) adapters）
# ==================================================================
if ! command -v copilot >/dev/null 2>&1; then
  echo "[e2e] copilot：本機不可用，跳過 copilot leg"
  exit 0
fi

MEM2="$TMP_DIR/memory-cp"
# PROJ2 需獨立一層父目錄：claude leg 的 $PROJ 也是 git repo，若兩者同層，
# resolve_project 的 sibling-repo 消歧（≥2 個同層 repo → slug 帶父目錄前綴）
# 會把 slug 解成 "<tmpdir>/proj-cp"，對不上 slice 的 project=proj-cp → 空 shortlist。
PROJ2="$TMP_DIR/copilot/proj-cp"
mkdir -p "$MEM2/knowledge/proj-cp" "$PROJ2"
git init -q "$PROJ2"

cat >"$MEM2/knowledge/proj-cp/serialwrap.md" <<'EOF'
---
memory_layer: knowledge
slice_id: sl-e2e0000000000002
project: proj-cp
title: SerialWrap 埠設定
captured_at: '2026-07-10T00:00:00Z'
---
SerialWrap 的 UART 埠必須以 115200/8N1 開啟，否則靜默丟包。
EOF

PYTHONPATH="$ROOT_DIR" python3 - "$MEM2" <<'PYEOF'
import sys
from pathlib import Path
from paulsha_hippo.moc import search as S
S.build_index(Path(sys.argv[1]), link_weights={})
print("[e2e] copilot leg index built")
PYEOF

mkdir -p "$MEM2/hooks/.venv/bin"
cat >"$MEM2/hooks/.venv/bin/python" <<EOF
#!/usr/bin/env bash
PYTHONPATH="$ROOT_DIR" exec python3 "\$@"
EOF
chmod +x "$MEM2/hooks/.venv/bin/python"

# 隔離 HOME：複製本機 copilot auth/config，僅換 hooks 設定（正式部署由 install.sh 寫）
COPILOT_HOME_DIR="$TMP_DIR/copilot-home"
mkdir -p "$COPILOT_HOME_DIR/.copilot/hooks"
cp -a "$HOME/.copilot/." "$COPILOT_HOME_DIR/.copilot/" 2>/dev/null || true
rm -f "$COPILOT_HOME_DIR/.copilot/hooks/"*.json 2>/dev/null || true
cat >"$COPILOT_HOME_DIR/.copilot/hooks/paulsha-memory.json" <<EOF
{"version": 1, "hooks": {
  "userPromptSubmitted": [{"type": "command",
    "bash": "PSC_MEMORY_ROOT=$MEM2 PYTHONPATH=$ROOT_DIR python3 $ROOT_DIR/paulsha_hippo/hooks/copilot_user_prompt_submit.py",
    "timeoutSec": 10}],
  "postToolUse": [{"type": "command",
    "bash": "PSC_MEMORY_ROOT=$MEM2 PYTHONPATH=$ROOT_DIR python3 $ROOT_DIR/paulsha_hippo/hooks/copilot_post_tool_use.py",
    "timeoutSec": 10}]
}}
EOF

OFFERED2="$MEM2/runtime/ledger/offered.jsonl"
USAGE2="$MEM2/runtime/ledger/memory_usage.jsonl"

echo "[e2e] copilot A+B+D: 相關 prompt（真實 copilot session）"
CPLOGS="$TMP_DIR/cplogs"; mkdir -p "$CPLOGS"
(cd "$PROJ2" && HOME="$COPILOT_HOME_DIR" timeout 300 copilot --allow-all-tools --add-dir "$MEM2" \
  --log-level debug --log-dir "$CPLOGS" -p \
  "我在設定 SerialWrap 的序列埠。系統會浮現相關記憶短清單，請依序完成三個必要步驟，不可省略：(1) 用 view 工具開啟清單中列出的絕對路徑（讀完整檔案，不要用 bash cat）；(2) 若其內容影響了你的建議，用 bash 執行清單末行的 mark-applied 回報命令（--slice-id 用該筆記 frontmatter 的 slice_id）；(3) 總結建議。" 2>&1 | tail -3) || true

dump_copilot_diag() {  # 失敗 forensics：trap 會刪 TMP_DIR，先把線索印出來（去識別）
  echo "--- diag: hooks.log ---"
  sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" "$MEM2/log/hooks.log" 2>/dev/null || echo "(no hooks.log)"
  echo "--- diag: ledger dir ---"
  ls -la "$MEM2/runtime/ledger/" 2>/dev/null | sed -e "s|$TMP_DIR|<tmp>|g" || echo "(no ledger dir)"
  echo "--- diag: hooks config ---"
  sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" "$COPILOT_HOME_DIR/.copilot/hooks/paulsha-memory.json" 2>/dev/null || true
  echo "--- diag: copilot debug log（hook 相關行）---"
  grep -rhiE "hook" "$CPLOGS" 2>/dev/null | sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" | head -40 || echo "(no hook lines)"
}
test -s "$OFFERED2" || { echo "[e2e] FAIL: copilot offered.jsonl 空——shortlist 未注入"; dump_copilot_diag; exit 1; }
grep -Fq '"tool": "copilot-cli"' "$OFFERED2"
grep -Fq 'sl-e2e0000000000002' "$OFFERED2"
echo "[e2e] copilot offered（平台注入）OK"

grep -Fq '"source": "read"' "$USAGE2" || { echo "[e2e] FAIL: copilot 無 read 事件"; dump_copilot_diag; exit 1; }
grep -Fq '"offered": true' "$USAGE2"
PYTHONPATH="$ROOT_DIR" python3 - "$MEM2" <<'PYEOF'
import json, sys
from pathlib import Path
mem = Path(sys.argv[1])
off = [json.loads(l) for l in (mem / "runtime/ledger/offered.jsonl").read_text().splitlines() if l.strip()]
use = [json.loads(l) for l in (mem / "runtime/ledger/memory_usage.jsonl").read_text().splitlines() if l.strip()]
reads = [e for e in use if e.get("source") == "read" and e.get("offered") is True]
assert off and reads, f"missing legs: offered={len(off)} reads={len(reads)}"
bound = {e["session_id"] for e in off} & {e["session_id"] for e in reads}
assert bound, "offered/read session_id 不一致——非同一 session 綁定"
print("[e2e] copilot offered→read 同 session 綁定 OK:", sorted(bound)[0])
PYEOF

echo "[e2e] copilot C: negative control（無關 prompt 不觸發 offer）"
BEFORE2=$(wc -l <"$OFFERED2")
(cd "$PROJ2" && HOME="$COPILOT_HOME_DIR" timeout 300 copilot -p \
  "請解釋 TCP 三次握手的流程，不需要讀任何檔案。" >/dev/null 2>&1) || true
AFTER2=$(wc -l <"$OFFERED2")
test "$BEFORE2" -eq "$AFTER2" || { echo "[e2e] FAIL: copilot 無關 prompt 竟新增 offered（$BEFORE2→$AFTER2）"; exit 1; }
echo "[e2e] copilot negative control OK（offered 行數 $BEFORE2 不變）"

echo "[e2e] copilot D: applied 實證檢查"
if grep -Fq '"kind": "applied"' "$USAGE2" && grep -Fq '"slice_id": "sl-e2e0000000000002"' "$USAGE2"; then
  echo "[e2e] copilot applied 實證 OK"
else
  echo "[e2e] WARN: copilot applied 未出現（agent 未遵循指引）——可整支重跑（上限 2 次）"
fi

echo "=== 去識別證據（copilot；貼入 docs/cross-cli-capability-matrix.md）==="
sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" "$OFFERED2" | sed 's/^/offered| /'
sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" "$USAGE2"   | sed 's/^/usage  | /'
echo "[e2e] PASS（applied 見上方各平台判定）"
