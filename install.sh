#!/usr/bin/env bash
# Runs every time a new shell is opened to setup *Neil's Bin Here*

# Add the parent directory of this script to PATH
# Detect if running in zsh or bash
if [ -n "$ZSH_VERSION" ]; then
  NEIL_BIN="$(cd "$(dirname "${(%):-%x}")" && pwd)"
else
  NEIL_BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# Add Neil's Bin Here to PATH
echo "Neil's Bin Here: $NEIL_BIN/README.md (common scripts and tools)"
export PATH="$NEIL_BIN:$PATH"

# Check if /usr/lib/wsl/lib exists (WSL2 GPU support) and make sure it's in PATH
# (Necessary for cron jobs to access GPU on WSL2)
if [ -d "/usr/lib/wsl/lib" ] && [[ ":$PATH:" != *":/usr/lib/wsl/lib:"* ]]; then
  echo "Adding /usr/lib/wsl/lib to PATH for WSL2 GPU support"
  export PATH="/usr/lib/wsl/lib:$PATH"
fi

# Set NEILS_BIN_CACHE to ~/.cache/neil/bin and create it if it doesn't exist
export NEILS_BIN_CACHE="$HOME/.cache/neil/bin"
if [ ! -d "$NEILS_BIN_CACHE" ]; then
  echo "Creating $NEILS_BIN_CACHE"
  mkdir -p "$NEILS_BIN_CACHE"
fi

# Install Claude Code skills (symlink docs as skills)
if [ -d "$HOME/.claude" ]; then
  SKILLS_HASH=$(ls "$NEIL_BIN/docs/"*.md 2>/dev/null | sort | shasum | cut -d' ' -f1)
  SKILLS_CACHE="$NEILS_BIN_CACHE/skills-hash"
  if [ ! -f "$SKILLS_CACHE" ] || [ "$(cat "$SKILLS_CACHE" 2>/dev/null)" != "$SKILLS_HASH" ]; then
    echo "Updating Claude Code skills..."
    # Remove stale skill symlinks
    for skill_dir in "$HOME/.claude/skills/"*/; do
      if [ -L "$skill_dir/SKILL.md" ]; then
        target=$(readlink "$skill_dir/SKILL.md")
        if [[ "$target" == "$NEIL_BIN/docs/"* ]] && [ ! -f "$target" ]; then
          echo "  Removing stale skill: $(basename "$skill_dir")"
          rm -rf "$skill_dir"
        fi
      fi
    done
    # Create/update symlinks for each doc
    for doc in "$NEIL_BIN/docs/"*.md; do
      name=$(basename "$doc" .md)
      # Only install docs with YAML frontmatter (first line is ---)
      if [ "$(head -1 "$doc")" != "---" ]; then
        continue
      fi
      # Lowercase the name for consistency (NMP → nmp)
      name_lower=$(echo "$name" | tr '[:upper:]' '[:lower:]')
      skill_dir="$HOME/.claude/skills/$name_lower"
      mkdir -p "$skill_dir"
      ln -sf "$doc" "$skill_dir/SKILL.md"
    done
    echo "$SKILLS_HASH" > "$SKILLS_CACHE"
  fi
fi

# Update git repos if $NEILS_BIN_CACHE/git-check is more than 24 hours old
if [ ! -f "$NEILS_BIN_CACHE/git-check" ] || [ $(find "$NEILS_BIN_CACHE/git-check" -mmin +1440) ]; then
  echo "Updating git repositories..."
  touch "$NEILS_BIN_CACHE/git-check"

  # If $NEILS_BIN_CACHE/repos exists then `git pull` each repository
  if [ -d "$NEILS_BIN_CACHE/repos" ]; then
    for repo in "$NEILS_BIN_CACHE/repos"/*; do
      if [ -d "$repo/.git" ]; then
        echo "Updating $repo"
        git -C "$repo" pull &
      fi
    done
    wait  # Wait for all background git pull processes to finish
  fi

  # Update Neil's Bin Here repo
  pushd $NEIL_BIN; git pull; popd
fi
