#!/usr/bin/env bash
set -uo pipefail

input=$(cat) || true
[[ -z "$input" ]] && exit 0

agent_id=$(printf '%s' "$input" | jq -r '.agent_id // empty') || exit 0
[[ -n "$agent_id" ]] && exit 0

msg=$(cat "${CLAUDE_PROJECT_DIR:-.}/.temp/reinforce.txt" 2>/dev/null) || true
[[ -z "$msg" ]] && exit 0

jq -n --arg m "$msg" '{hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$m}}'
