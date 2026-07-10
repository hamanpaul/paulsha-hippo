#!/usr/bin/env bash
# cross_cli_probe_check.sh — 跨 CLI capability matrix 實查（PR-F Task 1）
#
# 方法：隔離 HOME + marker 檔。對每個平台同時註冊 session-start（對照組）與
# prompt-time（受測組）兩個 hook，各自 touch 一個 marker，跑一輪 headless turn：
#   - prompt marker 出現            → 支援 prompt-time hook（FIRED）
#   - 只有 session-start marker 出現 → harness 有效、prompt-time 不支援（NOT-FIRED）
#   - 兩個都沒出現                  → harness/auth/hook-trust 問題（INCONCLUSIVE，未能實測）
# 手動執行、不進 CI。會把本機 auth 複製到暫存目錄（trap 退出即刪），不落任何持久檔。
set -uo pipefail   # 刻意不用 -e：單一平台失敗仍要跑完並記錄

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_BASE="$ROOT_DIR/.psc_tmp"
mkdir -p "$TMP_BASE"
TMP_DIR="$(mktemp -d "$TMP_BASE/probe-XXXXXX")"
trap 'rm -rf -- "$TMP_DIR"' EXIT

sanitize() { sed -e "s|$TMP_DIR|<tmp>|g" -e "s|$ROOT_DIR|<repo>|g" -e "s|$HOME|~|g"; }

verdict() { # $1=標籤 $2=受測 marker $3=對照 marker
  local label="$1" marker="$2" control="$3"
  if [[ -f "$marker" ]]; then
    echo "[probe] ${label}: FIRED（支援）"
  elif [[ -f "$control" ]]; then
    echo "[probe] ${label}: NOT-FIRED（對照組有 fire → 判定不支援）"
  else
    echo "[probe] ${label}: INCONCLUSIVE（對照組也沒 fire → harness/auth/trust 問題，未能實測）"
  fi
}

# ---------------- codex ----------------
if command -v codex >/dev/null 2>&1; then
  echo "=== codex version: $(codex --version 2>&1 | head -1 | sanitize) ==="
  CODEX_HOME_DIR="$TMP_DIR/codex-home"
  mkdir -p "$CODEX_HOME_DIR/.codex"
  cp -a "$HOME/.codex/auth.json" "$CODEX_HOME_DIR/.codex/" 2>/dev/null || true
  cp -a "$HOME/.codex/config.toml" "$CODEX_HOME_DIR/.codex/" 2>/dev/null || true
  M_PROMPT="$TMP_DIR/codex-prompt.marker"; M_START="$TMP_DIR/codex-start.marker"
  cat >"$CODEX_HOME_DIR/.codex/hooks.json" <<EOF
{"hooks": {
  "SessionStart": [{"matcher": "startup|clear|compact", "hooks": [
    {"type": "command", "command": "touch $M_START", "statusMessage": "probe: session-start control"}]}],
  "UserPromptSubmit": [{"matcher": ".*", "hooks": [
    {"type": "command", "command": "touch $M_PROMPT", "statusMessage": "probe: prompt-time"}]}]
}}
EOF
  (cd "$TMP_DIR" && HOME="$CODEX_HOME_DIR" timeout 120 \
    codex exec --skip-git-repo-check "reply with the single word ok" 2>&1 | tail -5 | sanitize) || true
  verdict "codex SessionStart（對照）" "$M_START" "$M_START"
  verdict "codex prompt-time hook"     "$M_PROMPT" "$M_START"
else
  echo "[probe] codex：本機不可用（matrix 標『未實測』）"
fi

# ---------------- copilot ----------------
if command -v copilot >/dev/null 2>&1; then
  echo "=== copilot version: $(copilot --version 2>&1 | head -1 | sanitize) ==="
  COPILOT_HOME_DIR="$TMP_DIR/copilot-home"
  mkdir -p "$COPILOT_HOME_DIR/.copilot/hooks"
  cp -a "$HOME/.copilot/." "$COPILOT_HOME_DIR/.copilot/" 2>/dev/null || true
  rm -f "$COPILOT_HOME_DIR/.copilot/hooks/"*.json 2>/dev/null || true
  M2_PROMPT="$TMP_DIR/copilot-prompt.marker"; M2_START="$TMP_DIR/copilot-start.marker"
  cat >"$COPILOT_HOME_DIR/.copilot/hooks/probe.json" <<EOF
{"version": 1, "hooks": {
  "sessionStart": [{"type": "command", "bash": "touch $M2_START", "timeoutSec": 10}],
  "userPromptSubmit": [{"type": "command", "bash": "touch $M2_PROMPT", "timeoutSec": 10}]
}}
EOF
  (cd "$TMP_DIR" && HOME="$COPILOT_HOME_DIR" timeout 120 \
    copilot -p "reply with the single word ok" 2>&1 | tail -5 | sanitize) || true
  verdict "copilot sessionStart（對照）" "$M2_START" "$M2_START"
  verdict "copilot prompt-time hook"     "$M2_PROMPT" "$M2_START"
else
  echo "[probe] copilot：本機不可用（matrix 標『未實測』）"
fi

echo "[probe] done — 將上列輸出（已去識別）貼入 docs/cross-cli-capability-matrix.md 證據區"
