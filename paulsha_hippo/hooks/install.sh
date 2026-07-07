#!/usr/bin/env bash
# install.sh — memory importer hook installer
#
# Modes:
#   --tree-only   Create the memory directory tree only (no hooks, no config)
#   --upgrade     Re-run the full install flow against an existing deployment
#   (default)     Full install: tree + venv + hook scripts + config files
#
# Options:
#   --memory-root PATH    Override memory root (default: ~/.agents/memory)
#   --config-root PATH    Override config root (default: ~); used for .claude / .codex / .copilot
#   --repo-root   PATH    Override repo root (default: git rev-parse --show-toplevel)
#   --skip-venv           Skip venv creation and pip install (useful for tests)
set -euo pipefail

memory_root="${HOME}/.agents/memory"
config_root="${HOME}"
repo_root=""
tree_only=false
skip_venv=false

while (($#)); do
  case "$1" in
    --tree-only)
      tree_only=true
      shift
      ;;
    --upgrade)
      shift
      ;;
    --memory-root)
      if (($# < 2)); then
        echo "install.sh: --memory-root requires a path" >&2
        exit 2
      fi
      memory_root="$2"
      shift 2
      ;;
    --config-root)
      if (($# < 2)); then
        echo "install.sh: --config-root requires a path" >&2
        exit 2
      fi
      config_root="$2"
      shift 2
      ;;
    --repo-root)
      if (($# < 2)); then
        echo "install.sh: --repo-root requires a path" >&2
        exit 2
      fi
      repo_root="$2"
      shift 2
      ;;
    --skip-venv)
      skip_venv=true
      shift
      ;;
    *)
      echo "install.sh: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

repo_root_file="${memory_root}/hooks/repo-root.txt"

# Validate memory_root
if [[ "$memory_root" =~ ^[[:space:]]*$ ]]; then
  echo "install.sh: --memory-root must not be empty" >&2
  exit 2
fi

if [[ "$memory_root" =~ ^/+$ ]]; then
  echo "install.sh: --memory-root must not be /" >&2
  exit 2
fi

if [[ "$memory_root" =~ [[:space:]] ]]; then
  echo "install.sh: --memory-root must not contain whitespace" >&2
  exit 2
fi

if [[ "$config_root" =~ ^[[:space:]]*$ ]]; then
  echo "install.sh: --config-root must not be empty" >&2
  exit 2
fi

if [[ "$config_root" =~ ^/+$ ]]; then
  echo "install.sh: --config-root must not be /" >&2
  exit 2
fi

if [[ "$config_root" =~ [[:space:]] ]]; then
  echo "install.sh: --config-root must not contain whitespace" >&2
  exit 2
fi

# Resolve repo_root if not provided
if [[ -z "$repo_root" ]]; then
  if git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel &>/dev/null; then
    repo_root="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  elif [[ -f "$repo_root_file" ]]; then
    repo_root="$(<"$repo_root_file")"
  else
    echo "install.sh: cannot determine repo root; pass --repo-root" >&2
    exit 2
  fi
fi

# ------------------------------------------------------------------
# Step 1: Create memory tree (shared between tree-only and full install)
# ------------------------------------------------------------------
dirs=(
  "inbox"
  "work-centric"
  "knowledge"
  "runtime"
  "log"
  "hooks"
  "archive"
  "inbox/sessions"
  "inbox/plans"
  "inbox/research"
  "inbox/reports"
  "work-centric/common-sense"
  "runtime/queue"
  "runtime/queue/_failed"
  "runtime/locks"
  "runtime/ledger"
  "runtime/indexes"
  "archive/queue"
)

for relative in "${dirs[@]}"; do
  path="${memory_root}/${relative}"
  install -d -m 700 -- "$path"
  touch -- "${path}/.gitkeep"
done

printf '%s\n' "$repo_root" >"$repo_root_file"
chmod 600 "$repo_root_file"

if [[ "$tree_only" == true ]]; then
  exit 0
fi

# ------------------------------------------------------------------
# Step 2: Venv + editable install (skip if --skip-venv)
# ------------------------------------------------------------------
venv_dir="${memory_root}/hooks/.venv"

if [[ "$skip_venv" != true ]]; then
  if python3 -m venv --help &>/dev/null 2>&1; then
    python3 -m venv "$venv_dir"
    if [[ -f "${repo_root}/pyproject.toml" ]]; then
      # 開發/checkout 情境：editable 安裝 repo
      "${venv_dir}/bin/pip" install --quiet -e "${repo_root}"
    else
      # wheel/pipx 情境（repo_root=site-packages 根）：直接把已解包的套件
      # 複製進 venv site-packages（零網路、與安裝版本一致）
      _hooks_pkg_src="${repo_root}/paulsha_hippo"
      _venv_site="$("${venv_dir}/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
      if [[ -d "$_hooks_pkg_src" && -n "$_venv_site" ]]; then
        "${venv_dir}/bin/pip" install --quiet pyyaml
        cp -r "$_hooks_pkg_src" "$_venv_site/"
      else
        echo "install.sh: WARN cannot locate packaged paulsha_hippo; importer venv incomplete" >&2
      fi
    fi
  else
    echo "install.sh: WARN python3 venv unavailable; skipping venv creation" >&2
  fi
fi

# ------------------------------------------------------------------
# Step 3: Deploy hook scripts
# ------------------------------------------------------------------
hooks_src_dir="${repo_root}/paulsha_hippo/hooks"

for script in install.sh uninstall.sh \
  claude_session_end.py codex_session_end.py copilot_session_end.py \
  _wakeup_common.py _bootstrap.py claude_session_start.py codex_session_start.py \
  copilot_session_start.py claude_precompact.py copilot_precompact.py \
  claude_user_prompt_submit.py claude_post_tool_use.py _shortlist_common.py; do
  src="${hooks_src_dir}/${script}"
  dst="${memory_root}/hooks/${script}"
  if [[ -f "$src" ]]; then
    install -m 700 -- "$src" "$dst"
  else
    echo "install.sh: WARN hook source not found: ${src}" >&2
  fi
done

# ------------------------------------------------------------------
# Helper: determine venv python path for config templates
# ------------------------------------------------------------------
if [[ "$skip_venv" == true ]]; then
  venv_python="${venv_dir}/bin/python"
else
  venv_python="${venv_dir}/bin/python"
fi
hook_dir="${memory_root}/hooks"
hook_env_prefix="PSC_MEMORY_ROOT=${memory_root} PSC_CONFIG_ROOT=${config_root}"

# ------------------------------------------------------------------
# Step 4: Claude settings.json — merge SessionEnd, SessionStart, PreCompact hook entries
# ------------------------------------------------------------------
claude_settings="${config_root}/.claude/settings.json"
install -d -m 700 "$(dirname "$claude_settings")"

# Build the hook commands we manage
claude_session_end_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/claude_session_end.py"
claude_session_start_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/claude_session_start.py"
claude_precompact_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/claude_precompact.py"
claude_user_prompt_submit_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/claude_user_prompt_submit.py"
claude_post_tool_use_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/claude_post_tool_use.py"

# Read existing JSON or start fresh
if [[ -f "$claude_settings" ]]; then
  existing_claude="$(cat "$claude_settings")"
else
  existing_claude="{}"
fi

python3 - "$claude_settings" "$existing_claude" \
  "$claude_session_end_cmd" "$claude_session_start_cmd" "$claude_precompact_cmd" \
  "$claude_user_prompt_submit_cmd" "$claude_post_tool_use_cmd" <<'PYEOF'
import json, sys

settings_path = sys.argv[1]
existing_json = sys.argv[2]
session_end_cmd = sys.argv[3]
session_start_cmd = sys.argv[4]
precompact_cmd = sys.argv[5]
user_prompt_submit_cmd = sys.argv[6]
post_tool_use_cmd = sys.argv[7]

try:
    settings = json.loads(existing_json)
except Exception as exc:
    print(f"install.sh: invalid JSON in {settings_path}: {exc}", file=sys.stderr)
    sys.exit(1)
if not isinstance(settings, dict):
    print(f"install.sh: expected JSON object in {settings_path}", file=sys.stderr)
    sys.exit(1)

hooks = settings.setdefault("hooks", {})

def _command_parts(command):
    parts = command.strip().split()
    while parts and "=" in parts[0]:
        key, value = parts[0].split("=", 1)
        if not key or not key.replace("_", "a").isalnum():
            break
        parts = parts[1:]
    return parts

def _is_managed_claude_hook(hook, script_markers):
    """Check if hook is managed by paulsha-memory for any of the given script names."""
    if not isinstance(hook, dict):
        return False
    if hook.get("managedBy") == "paulsha-memory":
        return True
    if hook.get("type") != "command" or hook.get("timeout") != 10:
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    parts = _command_parts(command)
    if len(parts) != 2 or not parts[0].endswith("/hooks/.venv/bin/python"):
        return False
    return any(parts[1].endswith(f"/hooks/{marker}") for marker in script_markers)

def _reconcile_event(event_name, hook_command, script_marker, matcher=""):
    """Reconcile a Claude hook event, removing old managed entries and adding the new one."""
    event_list = hooks.setdefault(event_name, [])
    if not isinstance(event_list, list):
        print(f"install.sh: hooks.{event_name} must be a list in {settings_path}", file=sys.stderr)
        sys.exit(1)
    
    # Remove old managed entries for this script
    updated_entries = []
    target_entry = None
    for entry in event_list:
        if not isinstance(entry, dict):
            updated_entries.append(entry)
            continue
        hooks_list = entry.get("hooks", [])
        if not isinstance(hooks_list, list):
            hooks_list = []
        kept_hooks = [
            hook
            for hook in hooks_list
            if not _is_managed_claude_hook(hook, [script_marker])
        ]
        updated_entry = dict(entry)
        updated_entry["hooks"] = kept_hooks
        if updated_entry.get("matcher", "") == matcher and target_entry is None:
            target_entry = updated_entry
        if kept_hooks:
            updated_entries.append(updated_entry)

    if target_entry is None:
        target_entry = {"matcher": matcher, "hooks": []}
        updated_entries.append(target_entry)
    
    target_entry["hooks"].append(
        {
            "type": "command",
            "command": hook_command,
            "timeout": 10,
            "managedBy": "paulsha-memory",
        }
    )
    if target_entry not in updated_entries:
        updated_entries.append(target_entry)
    hooks[event_name] = updated_entries

_reconcile_event("SessionEnd", session_end_cmd, "claude_session_end.py")
_reconcile_event("SessionStart", session_start_cmd, "claude_session_start.py")
_reconcile_event("PreCompact", precompact_cmd, "claude_precompact.py")
_reconcile_event("UserPromptSubmit", user_prompt_submit_cmd, "claude_user_prompt_submit.py")
_reconcile_event("PostToolUse", post_tool_use_cmd, "claude_post_tool_use.py", matcher="Read")

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF

# ------------------------------------------------------------------
# Step 5: Codex hooks.json — merge Stop / SubagentStop / SessionStart entries
# ------------------------------------------------------------------
codex_hooks="${config_root}/.codex/hooks.json"
install -d -m 700 "$(dirname "$codex_hooks")"

codex_stop_command="${hook_env_prefix} ${venv_python} ${hook_dir}/codex_session_end.py"
codex_subagent_command="${hook_env_prefix} ${venv_python} ${hook_dir}/codex_session_end.py --subagent"
codex_session_start_command="${hook_env_prefix} ${venv_python} ${hook_dir}/codex_session_start.py"

if [[ -f "$codex_hooks" ]]; then
  existing_codex="$(cat "$codex_hooks")"
else
  existing_codex="{}"
fi

python3 - "$codex_hooks" "$existing_codex" "$codex_stop_command" "$codex_subagent_command" \
  "$codex_session_start_command" <<'PYEOF'
import json, sys

codex_path = sys.argv[1]
existing_json = sys.argv[2]
stop_command = sys.argv[3]
subagent_command = sys.argv[4]
session_start_command = sys.argv[5]

try:
    data = json.loads(existing_json)
except Exception as exc:
    print(f"install.sh: invalid JSON in {codex_path}: {exc}", file=sys.stderr)
    sys.exit(1)
if not isinstance(data, dict):
    print(f"install.sh: expected JSON object in {codex_path}", file=sys.stderr)
    sys.exit(1)

hooks = data.setdefault("hooks", {})

def _command_parts(command):
    parts = command.strip().split()
    while parts and "=" in parts[0]:
        key, value = parts[0].split("=", 1)
        if not key or not key.replace("_", "a").isalnum():
            break
        parts = parts[1:]
    return parts

def _managed_hook(command, status_msg):
    return {
        "type": "command",
        "command": command,
        "statusMessage": status_msg,
        "managedBy": "paulsha-memory",
    }

def _is_managed_codex_hook(hook, managed_marker):
    if not isinstance(hook, dict):
        return False
    if hook.get("managedBy") == "paulsha-memory":
        return True
    if hook.get("type") != "command":
        return False
    status_msg = hook.get("statusMessage")
    if not isinstance(status_msg, str) or not status_msg.startswith("paulsha-memory:"):
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    parts = _command_parts(command)
    return (
        len(parts) in {2, 3}
        and parts[0].endswith("/hooks/.venv/bin/python")
        and parts[1].endswith(f"/hooks/{managed_marker}")
        and (len(parts) == 2 or parts[2] == "--subagent")
    )

def _reconcile_event(name, command, status_msg, managed_marker, matcher=".*"):
    event_list = hooks.setdefault(name, [])
    if not isinstance(event_list, list):
        print(f"install.sh: hooks.{name} must be a list in {codex_path}", file=sys.stderr)
        sys.exit(1)
    updated_entries = []
    target_entry = None
    for entry in event_list:
        if not isinstance(entry, dict):
            updated_entries.append(entry)
            continue
        hooks_list = entry.get("hooks", [])
        if not isinstance(hooks_list, list):
            hooks_list = []
        kept_hooks = [
            hook
            for hook in hooks_list
            if not _is_managed_codex_hook(hook, managed_marker)
        ]
        updated_entry = dict(entry)
        updated_entry["hooks"] = kept_hooks
        if updated_entry.get("matcher", ".*") == matcher and target_entry is None:
            target_entry = updated_entry
        if kept_hooks:
            updated_entries.append(updated_entry)
    if target_entry is None:
        target_entry = {"matcher": matcher, "hooks": []}
        updated_entries.append(target_entry)
    target_entry["hooks"].append(_managed_hook(command, status_msg))
    if target_entry not in updated_entries:
        updated_entries.append(target_entry)
    hooks[name] = updated_entries

_reconcile_event("Stop", stop_command, "paulsha-memory: capturing turn snapshot", "codex_session_end.py")
_reconcile_event("SubagentStop", subagent_command, "paulsha-memory: capturing subagent snapshot", "codex_session_end.py")
_reconcile_event(
    "SessionStart", session_start_command, "paulsha-memory: injecting wake-up brief",
    "codex_session_start.py", matcher="startup|clear|compact",
)

with open(codex_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF

# ------------------------------------------------------------------
# Step 6: Copilot hook config — write paulsha-memory.json
# ------------------------------------------------------------------
copilot_hook="${config_root}/.copilot/hooks/paulsha-memory.json"
install -d -m 700 "$(dirname "$copilot_hook")"

copilot_session_end_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/copilot_session_end.py"
copilot_session_start_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/copilot_session_start.py"
copilot_precompact_cmd="${hook_env_prefix} ${venv_python} ${hook_dir}/copilot_precompact.py"

python3 - "$copilot_hook" \
  "$copilot_session_end_cmd" "$copilot_session_start_cmd" "$copilot_precompact_cmd" <<'PYEOF'
import json, sys

hook_path = sys.argv[1]
session_end_cmd = sys.argv[2]
session_start_cmd = sys.argv[3]
precompact_cmd = sys.argv[4]

config = {
    "version": 1,
    "hooks": {
        "sessionEnd": [
            {
                "type": "command",
                "bash": session_end_cmd,
                "powershell": "Write-Host 'paulsha-memory: powershell path not supported in MVP'",
                "timeoutSec": 10,
            }
        ],
        "sessionStart": [
            {
                "type": "command",
                "bash": session_start_cmd,
                "powershell": "Write-Host 'paulsha-memory: powershell path not supported in MVP'",
                "timeoutSec": 10,
            }
        ],
        "preCompact": [
            {
                "type": "command",
                "bash": precompact_cmd,
                "powershell": "Write-Host 'paulsha-memory: powershell path not supported in MVP'",
                "timeoutSec": 10,
            }
        ]
    }
}

with open(hook_path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF

# ------------------------------------------------------------------
# Step 7: Write projects.yaml (only if absent)
# ------------------------------------------------------------------
projects_yaml="${config_root}/.agents/config/projects.yaml"
install -d -m 700 "$(dirname "$projects_yaml")"

if [[ ! -f "$projects_yaml" ]]; then
  sample_yaml="${repo_root}/config/agents-projects.sample.yaml"
  if [[ ! -f "$sample_yaml" ]]; then
    sample_yaml="$(dirname "${BASH_SOURCE[0]}")/../config-samples/agents-projects.sample.yaml"
  fi
  if [[ -f "$sample_yaml" ]]; then
    install -m 600 "$sample_yaml" "$projects_yaml"
  else
    echo "install.sh: WARN sample projects yaml not found: ${sample_yaml}" >&2
  fi
fi

# ------------------------------------------------------------------
# Done — print Codex /hooks trust reminder
# ------------------------------------------------------------------
echo ""
echo "paulsha-memory install complete."
echo ""
echo "  IMPORTANT (Codex): Run '/hooks' inside Codex CLI to review and trust"
echo "  the newly installed hook scripts. Hook script changes invalidate trust"
echo "  automatically and will require re-review."
echo ""
