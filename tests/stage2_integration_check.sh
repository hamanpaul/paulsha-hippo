#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_BASE="$ROOT_DIR/.psc_tmp"
mkdir -p "$TMP_BASE"
TMP_DIR="$TMP_BASE/stage2-$(date +%s)-$$"
mkdir -p "$TMP_DIR"
trap 'rm -rf -- "$TMP_DIR"; rmdir -- "$TMP_BASE" 2>/dev/null || true' EXIT

require_text() {
  local label="$1"
  local file="$2"
  shift 2

  echo "[stage2] ${label}"
  test -f "$file"
  for needle in "$@"; do
    grep -Fq "$needle" "$file"
  done
}

require_hook_dry_run() {
  local label="$1"
  local hook_script="$2"
  local fixture="$3"
  local queue_pattern="$4"
  local output
  local queue_item
  local -a queue_matches=()

  echo "[stage2] ${label}"
  PSC_MEMORY_ROOT="$TMP_DIR/memory" \
    PSC_CONFIG_ROOT="$TMP_DIR/config-root" \
    python3 "$ROOT_DIR/paulsha_hippo/hooks/${hook_script}" <"$fixture"
  while IFS= read -r match; do
    queue_matches+=("$match")
  done < <(find "$TMP_DIR/memory/runtime/queue" -maxdepth 1 -type f -name "$queue_pattern" | sort)
  test "${#queue_matches[@]}" -eq 1
  queue_item="${queue_matches[0]}"
  test -s "$queue_item"
  output="$(
    PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.importer.cli ingest \
      --queue-item "$queue_item" \
      --memory-root "$TMP_DIR/memory" \
      --dry-run
  )"
  grep -Fq '"status": "written"' <<<"$output"
  grep -Fq '"dry_run": true' <<<"$output"
  grep -Fq '"classifier_bucket"' <<<"$output"
}

require_text \
  "validate scope" \
  "$ROOT_DIR/openspec/specs/stage2/scope.md" \
  "inbox -> work-centric -> knowledge" \
  "decayed/reactivation"

require_text \
  "validate memory routing" \
  "$ROOT_DIR/paulsha_hippo/routing.md" \
  "inbox" \
  "knowledge"

require_text \
  "validate janitor service" \
  "$ROOT_DIR/paulsha_hippo/janitor/service.md" \
  "systemd" \
  "reactivation"

require_text \
  "validate sync-back gate" \
  "$ROOT_DIR/custom-skills/paulsha-memory/README.md" \
  "sync-back gate" \
  "stage 測試" \
  "Stage 3 frontmatter schema"

require_text \
  "validate Stage 3 frontmatter field names named explicitly" \
  "$ROOT_DIR/openspec/specs/stage2/scope.md" \
  "slice_id" \
  "artifact_kind" \
  "supersedes" \
  "checksum"

require_text \
  "validate evidence template" \
  "$ROOT_DIR/docs/superpowers/workstreams/stage2-paulsha-memory/evidence/stage2-integration-template.md" \
  "測試命令" \
  "證據檔名"

require_text \
  "validate review result" \
  "$ROOT_DIR/docs/superpowers/workstreams/stage2-paulsha-memory/review.md" \
  "無阻斷性問題" \
  "Stage 3 frontmatter schema"

echo "[stage2] validate memory policy consumer lint"
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.lint.policy_consumer_lint "$ROOT_DIR/paulsha_hippo"

require_hook_dry_run \
  "fixture dry-run claude" \
  "claude_session_end.py" \
  "$ROOT_DIR/tests/fixtures/claude/session_end/payload.json" \
  "claude-code__claude-session-end-001.json"

require_hook_dry_run \
  "fixture dry-run codex" \
  "codex_session_end.py" \
  "$ROOT_DIR/tests/fixtures/codex/stop/payload.json" \
  "codex__codex-stop-001*.json"

require_hook_dry_run \
  "fixture dry-run copilot" \
  "copilot_session_end.py" \
  "$ROOT_DIR/tests/fixtures/copilot/session_end/payload.json" \
  "copilot-cli__copilot-session-end-001.json"

echo "[stage2] janitor dry-run over fixtures"
JANITOR_ROOT="$TMP_DIR/janitor-fixtures"
mkdir -p "$JANITOR_ROOT"
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.cli janitor scan \
  --memory-root "$JANITOR_ROOT" \
  --knowledge-root "$ROOT_DIR/tests/fixtures/knowledge/ttl" \
  --now "2026-05-31T00:00:00Z" \
  --dry-run | grep -Fq '"decayed": 1'

echo "[stage2] atomizer dry-run over fixtures"
ATOMIZE_ROOT="$TMP_DIR/atomize-fixtures"
mkdir -p "$ATOMIZE_ROOT/inbox/research/claude/2026-05-31"
cp "$ROOT_DIR/tests/fixtures/atomizer/raw/s1.md" \
   "$ATOMIZE_ROOT/inbox/research/claude/2026-05-31/s1.md"
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.cli atomize \
  --memory-root "$ATOMIZE_ROOT" --now "2026-05-31T03:00:00Z" --dry-run \
  --promoter identity | grep -Fq '"slices":'

echo "[stage2] atomizer llm stub dry-run"
ATOMIZE_LLM_ROOT="$TMP_DIR/atomize-llm-fixtures"
mkdir -p "$ATOMIZE_LLM_ROOT/inbox/research/claude/2026-05-31"
cp "$ROOT_DIR/tests/fixtures/atomizer/raw/s1.md" \
   "$ATOMIZE_LLM_ROOT/inbox/research/claude/2026-05-31/s1.md"
cat >"$ATOMIZE_LLM_ROOT/projects.yaml" <<'EOF'
projects:
  - paulshaclaw
EOF
cat >"$ATOMIZE_LLM_ROOT/atomizer.override.yaml" <<EOF
promoter: llm
known_projects_file: "$ATOMIZE_LLM_ROOT/projects.yaml"
agent_exec:
  command:
    - python3
    - $ROOT_DIR/tests/fixtures/atomizer/fake-agent.py
  timeout_seconds: 30
  model: fake-agent
EOF
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.cli atomize \
  --memory-root "$ATOMIZE_LLM_ROOT" \
  --now "2026-05-31T03:00:00Z" \
  --promoter llm \
  --override "$ATOMIZE_LLM_ROOT/atomizer.override.yaml" \
  --dry-run | grep -Fq '"slices": 1'

echo "[stage2] dream dry-run + bundle over fixtures"
DREAM_ROOT="$TMP_DIR/dream-fixtures"
mkdir -p "$DREAM_ROOT/inbox/research/claude/2026-06-02"
cp "$ROOT_DIR/tests/fixtures/atomizer/raw/s1.md" \
   "$DREAM_ROOT/inbox/research/claude/2026-06-02/s1.md"
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.cli dream run \
  --memory-root "$DREAM_ROOT" --now "2026-06-02T05:00:00Z" --promoter identity --dry-run \
  | grep -Fq '"status"'
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.cli dream run \
  --memory-root "$DREAM_ROOT" --now "2026-06-02T05:00:00Z" --promoter identity >/dev/null
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.cli bundle \
  --memory-root "$DREAM_ROOT" --project paulshaclaw --out "$DREAM_ROOT/bundle" \
  --now "2026-06-02T06:00:00Z" >/dev/null
grep -Fq '"raw_excluded": true' "$DREAM_ROOT/bundle/manifest.json"

echo "[stage2] dream(moc) + search over fixtures"
MOC_ROOT="$(mktemp -d "$TMP_BASE/moc-XXXXXX")"
mkdir -p "$MOC_ROOT/inbox/research/claude/2026-06-03"
cp "$ROOT_DIR/tests/fixtures/atomizer/raw/s1.md" \
   "$MOC_ROOT/inbox/research/claude/2026-06-03/s1.md"
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.cli dream run \
  --memory-root "$MOC_ROOT" --now "2026-06-03T05:00:00Z" --promoter identity >/dev/null
test -f "$MOC_ROOT/knowledge/wiki-moc.md"
grep -Fq "memory_layer: moc" "$MOC_ROOT/knowledge/wiki-moc.md"
PYTHONPATH="$ROOT_DIR" python3 -m paulsha_hippo.cli search alpha \
  --memory-root "$MOC_ROOT" | grep -Fq '"results"'

echo "[stage2] ok"
