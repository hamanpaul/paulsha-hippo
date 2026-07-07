#!/usr/bin/env bash
# uninstall.sh — remove managed paulsha-memory hook config entries
#
# Removes:
#   - The managed SessionEnd entry from ~/.claude/settings.json
#   - The managed Stop/SubagentStop/SessionStart entries from ~/.codex/hooks.json
#   - The entire ~/.copilot/hooks/paulsha-memory.json file
#
# Preserves:
#   - inbox content, projects.yaml, and all other memory tree contents
#   - Unrelated keys/entries in claude settings.json and codex hooks.json
#
# Options:
#   --memory-root PATH    (unused here but accepted for script symmetry)
#   --config-root PATH    Override config root (default: ~)
set -euo pipefail

config_root="${HOME}"
# memory_root accepted but not currently needed; kept for CLI symmetry
memory_root="${HOME}/.agents/memory"

while (($#)); do
  case "$1" in
    --config-root)
      if (($# < 2)); then
        echo "uninstall.sh: --config-root requires a path" >&2
        exit 2
      fi
      config_root="$2"
      shift 2
      ;;
    --memory-root)
      if (($# < 2)); then
        echo "uninstall.sh: --memory-root requires a path" >&2
        exit 2
      fi
      memory_root="$2"
      shift 2
      ;;
    *)
      echo "uninstall.sh: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$config_root" =~ ^[[:space:]]*$ ]]; then
  echo "uninstall.sh: --config-root must not be empty" >&2
  exit 2
fi

if [[ "$config_root" =~ ^/+$ ]]; then
  echo "uninstall.sh: --config-root must not be /" >&2
  exit 2
fi

if [[ "$config_root" =~ [[:space:]] ]]; then
  echo "uninstall.sh: --config-root must not contain whitespace" >&2
  exit 2
fi

# ------------------------------------------------------------------
# Remove managed Claude SessionEnd, SessionStart, PreCompact hook entries
# ------------------------------------------------------------------
claude_settings="${config_root}/.claude/settings.json"

if [[ -f "$claude_settings" ]]; then
  python3 - "$claude_settings" <<'PYEOF'
import json, sys

settings_path = sys.argv[1]
try:
    with open(settings_path, encoding="utf-8") as f:
        settings = json.load(f)
except Exception:
    sys.exit(0)

script_markers = ["claude_session_end.py", "claude_session_start.py", "claude_precompact.py",
                  "claude_user_prompt_submit.py", "claude_post_tool_use.py"]

def _command_parts(command):
    parts = command.strip().split()
    while parts and "=" in parts[0]:
        key, value = parts[0].split("=", 1)
        if not key or not key.replace("_", "a").isalnum():
            break
        parts = parts[1:]
    return parts

def _is_managed_claude_hook(hook):
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

def _filter_event(event_name):
    event_list = settings.get("hooks", {}).get(event_name, [])
    filtered = []
    for entry in event_list:
        if not isinstance(entry, dict):
            filtered.append(entry)
            continue
        hooks_list = entry.get("hooks", [])
        if not isinstance(hooks_list, list):
            hooks_list = []
        kept_hooks = [
            hook
            for hook in hooks_list
            if not _is_managed_claude_hook(hook)
        ]
        if kept_hooks:
            updated_entry = dict(entry)
            updated_entry["hooks"] = kept_hooks
            filtered.append(updated_entry)
    return filtered

settings.setdefault("hooks", {})
for event_name in ["SessionEnd", "SessionStart", "PreCompact", "UserPromptSubmit", "PostToolUse"]:
    settings["hooks"][event_name] = _filter_event(event_name)

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF
fi

# ------------------------------------------------------------------
# Remove managed Codex Stop / SubagentStop hook entries
# ------------------------------------------------------------------
codex_hooks="${config_root}/.codex/hooks.json"

if [[ -f "$codex_hooks" ]]; then
  python3 - "$codex_hooks" <<'PYEOF'
import json, sys

codex_path = sys.argv[1]
try:
    with open(codex_path, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

def _command_parts(command):
    parts = command.strip().split()
    while parts and "=" in parts[0]:
        key, value = parts[0].split("=", 1)
        if not key or not key.replace("_", "a").isalnum():
            break
        parts = parts[1:]
    return parts

def _remove_managed(event_list, marker):
    def _is_managed_codex_hook(hook):
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
            and parts[1].endswith(f"/hooks/{marker}")
            and (len(parts) == 2 or parts[2] == "--subagent")
        )

    filtered = []
    for entry in event_list:
        if not isinstance(entry, dict):
            filtered.append(entry)
            continue
        hooks_list = entry.get("hooks", [])
        if not isinstance(hooks_list, list):
            hooks_list = []
        kept_hooks = [
            hook
            for hook in hooks_list
            if not _is_managed_codex_hook(hook)
        ]
        if kept_hooks:
            updated_entry = dict(entry)
            updated_entry["hooks"] = kept_hooks
            filtered.append(updated_entry)
    return filtered

hooks = data.get("hooks", {})
if "Stop" in hooks:
    hooks["Stop"] = _remove_managed(hooks["Stop"], "codex_session_end.py")
if "SubagentStop" in hooks:
    hooks["SubagentStop"] = _remove_managed(hooks["SubagentStop"], "codex_session_end.py")
if "SessionStart" in hooks:
    hooks["SessionStart"] = _remove_managed(hooks["SessionStart"], "codex_session_start.py")

with open(codex_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF
fi

# ------------------------------------------------------------------
# Remove Copilot paulsha-memory.json
# ------------------------------------------------------------------
copilot_hook="${config_root}/.copilot/hooks/paulsha-memory.json"
if [[ -f "$copilot_hook" ]]; then
  rm -f -- "$copilot_hook"
fi

echo "paulsha-memory hooks uninstalled."
