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

# Check if /usr/lib/wsl/lib exists (WSL2 GPU support) and make sure it's in PATH
# (Necessary for cron jobs to access GPU on WSL2)
if [ -d "/usr/lib/wsl/lib" ] && [[ ":$PATH:" != *":/usr/lib/wsl/lib:"* ]]; then
  echo "Adding /usr/lib/wsl/lib to PATH for WSL2 GPU support"
  export PATH="/usr/lib/wsl/lib:$PATH"
fi

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

# Update Neil's Bin Here repo
pushd $NEIL_BIN; git pull; popd

