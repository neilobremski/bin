#!/usr/bin/env bash
# spark-organ.sh <organ_dir> — spark a single organ with locking and env.
# Finds the nearest life.conf by walking up from the organ dir.
set -euo pipefail

ORGAN_DIR="$(cd "${1:-.}" && pwd)"
[ -x "$ORGAN_DIR/live.sh" ] || { echo "error: $ORGAN_DIR/live.sh not found or not executable" >&2; exit 1; }

# Find nearest life.conf by walking up
dir="$ORGAN_DIR"
CONF=""
while [ "$dir" != "/" ]; do
    if [ -f "$dir/life.conf" ]; then
        CONF="$dir/life.conf"
        break
    fi
    dir="$(dirname "$dir")"
done

if [ -z "$CONF" ]; then
    echo "warning: no life.conf found, running with minimal env" >&2
    CONF_DIR="$(dirname "$ORGAN_DIR")"
else
    CONF_DIR="$(dirname "$CONF")"
    set -a
    # shellcheck source=/dev/null
    . "$CONF"
    set +a
fi

export CONF_DIR
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PATH="$BIN_ROOT:$PATH"
export PYTHONPATH="$BIN_ROOT:${PYTHONPATH:-}"

NAME="$(basename "$ORGAN_DIR")"
LOCK_DIR="${HOME}/.life/locks"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/$NAME.lock"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "[$NAME] already running — skipping" >&2
    exit 0
fi

echo "[$NAME] sparking..." >&2
(cd "$ORGAN_DIR" && bash live.sh) 2>&1
echo "[$NAME] finished" >&2
