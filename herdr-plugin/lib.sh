#!/usr/bin/env bash
set -euo pipefail

PLUGIN_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${MODEL_ROUTER_ROOT:-$(cd -- "$PLUGIN_DIR/.." && pwd)}"

# Find an interpreter that has the SDK, without baking anyone's home directory
# into a committed file: a venv beside the project, then one inside it, then
# whatever python3 is on PATH. MODEL_ROUTER_PYTHON overrides all of it.
default_python() {
    local candidate
    for candidate in "$ROOT/../.venv/bin/python" "$ROOT/.venv/bin/python"; do
        if [[ -x "$candidate" ]]; then
            printf '%s' "$candidate"
            return
        fi
    done
    printf 'python3'
}
PYTHON="${MODEL_ROUTER_PYTHON:-$(default_python)}"

wait_for_keypress() {
    if [[ -t 0 ]]; then
        read -r -n 1 -s -p "Press any key to close..." _key
        printf '\n'
    else
        read -r _key || true
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'Python: %s\nRoot: %s\n\n' "$PYTHON" "$ROOT"
    wait_for_keypress
fi
