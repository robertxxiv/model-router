#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

entrypoint="${1:-}"

if [[ "$entrypoint" == "route" || "$entrypoint" == "models" || \
    "$entrypoint" == "status" || "$entrypoint" == "cache-stats" ]]; then
    HERDR_COMMAND="${HERDR_BIN_PATH:-herdr}"
    PLUGIN_ID="${HERDR_PLUGIN_ID:-model-router}"
    "$HERDR_COMMAND" plugin pane open \
        --plugin "$PLUGIN_ID" \
        --entrypoint "$entrypoint" \
        --placement overlay \
        --focus
else
    printf 'Usage: %s {route|models|status|cache-stats}\n' "$0" >&2
    exit 2
fi

printf '\n'
wait_for_keypress
