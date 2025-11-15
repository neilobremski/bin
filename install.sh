#!/usr/bin/env bash

# Add the parent directory of this script to PATH
# Detect if running in zsh or bash
if [ -n "$ZSH_VERSION" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${(%):-%x}")" && pwd)"
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
echo "Neil's Bin: $SCRIPT_DIR"

export PATH="$SCRIPT_DIR:$PATH"

