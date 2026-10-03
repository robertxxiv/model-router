#!/bin/bash
# PreToolUse hook (matcher: Agent|Task) for Claude Code. Picks the subagent
# model with the model-router instead of leaving it to whoever wrote the spawn.
#
# Configure with environment variables, or edit the three lines below:
#   MODEL_ROUTER            path to route.py
#   MODEL_ROUTER_PYTHON     interpreter that has typesafe-sdk installed
#   MODEL_ROUTER_NEXT_HOOK  optional hook to delegate to in every case this one
#                           does not handle, so an existing hook is not lost
#
# Install by pointing settings.json at it:
#   "hooks": { "PreToolUse": [ { "matcher": "Agent|Task", "hooks": [
#     { "type": "command", "command": "bash /path/to/route-subagent-model.sh",
#       "timeout": 12 } ] } ] }
#
# This runs BEFORE pin-subagent-model.sh and delegates to it in every case it
# does not handle itself, so the existing premium-tier protection is never
# lost. It only ever takes over when all of these hold:
#   * the project has routing enabled   (.herdr/router.json, absent = enabled)
#   * no explicit model was requested   (a deliberate choice always wins)
#   * the spawn is not a fork           (fork ignores `model`; pin denies it)
#   * the router answered inside its timeout
# Anything else - router missing, no API key, Jev slow or down, bad JSON -
# falls through to pin. A spawn is never blocked by this hook.
#
# The router is asked for a Claude Code alias (haiku|sonnet|opus|fable), not a
# full identifier, because that is all the Agent tool accepts.

ROUTER="${MODEL_ROUTER:-$HOME/model-router/route.py}"
PYTHON="${MODEL_ROUTER_PYTHON:-python3}"
PIN="${MODEL_ROUTER_NEXT_HOOK:-}"      # optional: hook to delegate to
TIMEOUT=8

INPUT=$(cat)

fallthrough() {            # hand the untouched input to the next hook, if any
  if [ -n "$PIN" ] && [ -f "$PIN" ]; then
    printf '%s' "$INPUT" | bash "$PIN"
  fi
  exit 0
}

command -v jq >/dev/null 2>&1 || fallthrough
[ -f "$ROUTER" ] && [ -x "$PYTHON" ] || fallthrough

REQ=$(printf '%s' "$INPUT" | jq -r '.tool_input.model // ""')
KIND=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // ""')
PROMPT=$(printf '%s' "$INPUT" | jq -r '.tool_input.prompt // ""')
DESC=$(printf '%s' "$INPUT" | jq -r '.tool_input.description // ""')
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // ""')

# An explicit model, a fork, or nothing to judge: not ours.
case "$REQ" in ""|inherit|default) ;; *) fallthrough ;; esac
[ "$KIND" = "fork" ] && fallthrough
[ -z "$PROMPT" ] && PROMPT="$DESC"
[ -z "$PROMPT" ] && fallthrough

# The assignment can be long; the router only needs its shape.
TASK=$(printf '%s' "$PROMPT" | head -c 4000)

ARGS=(--for-subagent --no-herdr)
[ -n "$CWD" ] && ARGS+=(--cwd "$CWD")

# `--` first: a prompt beginning with a dash must never be read as an
# option. Without it, a task of "--task-file=/path/.env" would make the
# router read that file and send it to the API.
OUT=$(timeout "$TIMEOUT" "$PYTHON" "$ROUTER" "${ARGS[@]}" -- "$TASK" 2>/dev/null)
[ -z "$OUT" ] && fallthrough          # disabled for this project, or it failed

MODEL=$(printf '%s' "$OUT" | jq -r '.model // ""' 2>/dev/null)
case "$MODEL" in
  haiku|sonnet|opus|fable) ;;
  *) fallthrough ;;
esac

ROLE=$(printf '%s' "$OUT" | jq -r '.role // "?"')
SCORE=$(printf '%s' "$OUT" | jq -r '.score // 0')
WHY=$(printf '%s' "$OUT" | jq -r '.why // ""')
CONFIRM=$(printf '%s' "$OUT" | jq -r 'if .needs_confirmation then " Close call on high-risk work - worth confirming." else "" end')

printf '%s' "$INPUT" | jq -c \
  --arg m "$MODEL" --arg r "$ROLE" --arg s "$SCORE" --arg w "$WHY" --arg x "$CONFIRM" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",
  permissionDecision:"allow",
  permissionDecisionReason:("model-router chose " + $m + " (" + $r + ", score " + $s + "): " + $w + "." + $x + " Pass model explicitly to override, or disable routing for this project with `route.py --disable`."),
  updatedInput:(.tool_input + {model:$m})}}'
exit 0
