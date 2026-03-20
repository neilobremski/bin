#!/usr/bin/env bash
# Comms — thin launcher. The real work is in comms.py.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/comms.py"
