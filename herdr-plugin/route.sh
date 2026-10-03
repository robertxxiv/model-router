#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

printf 'Task: '
read -r task
printf '\n'

if [[ -z "$task" ]]; then
    printf 'No task entered.\n'
else
    "$PYTHON" "$ROOT/router.py" run "$task"
fi

printf '\n'
wait_for_keypress
