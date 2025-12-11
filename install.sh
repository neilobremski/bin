#!/usr/bin/env bash

# Add the parent directory of this script to PATH
# Detect if running in zsh or bash
if [ -n "$ZSH_VERSION" ]; then
  NEIL_BIN="$(cd "$(dirname "${(%):-%x}")" && pwd)"
else
  NEIL_BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# Add Neil's Bin Here to PATH
echo "Neil's Bin: $NEIL_BIN"
export PATH="$NEIL_BIN:$PATH"

# If $HOME/repos exists then `git pull` each repository
if [ -d "$HOME/repos" ]; then
  for repo in "$HOME/repos"/*; do
    if [ -d "$repo/.git" ]; then
      echo "Updating $repo"
      git -C "$repo" pull &
    fi
  done
  wait  # Wait for all background git pull processes to finish
fi
