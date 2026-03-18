#!/usr/bin/env bash
# Eye — thin launcher. The real work is in eye.py.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/eye.py"
