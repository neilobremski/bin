#!/usr/bin/env bash
# Brain — thin launcher. The real work is in brain.py.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/brain.py"
