#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_DIR="$SCRIPT_DIR/.temp/macros"
SHARED_DIR="$SCRIPT_DIR/.claude/macros"

usage() {
    echo "Usage: m [ID [TEXT | --clear | --promote | --demote]]" >&2
    echo "  (no args)       List all macros" >&2
    echo "  ID               Recall macro (prints text to stdout)" >&2
    echo "  ID TEXT...       Save macro (local)" >&2
    echo "  ID --clear       Delete macro (both tiers)" >&2
    echo "  ID --promote     Copy local → shared, remove local" >&2
    echo "  ID --demote      Copy shared → local, remove shared" >&2
}

# Resolve a macro file: local overrides shared
resolve() {
    local id="$1"
    if [ -f "$LOCAL_DIR/$id.txt" ]; then
        echo "$LOCAL_DIR/$id.txt"
    elif [ -f "$SHARED_DIR/$id.txt" ]; then
        echo "$SHARED_DIR/$id.txt"
    fi
}

list_macros() {
    local found=0 seen=""
    for dir in "$LOCAL_DIR" "$SHARED_DIR"; do
        [ -d "$dir" ] || continue
        for f in "$dir"/*.txt; do
            [ -f "$f" ] || continue
            local id
            id=$(basename "$f" .txt)
            case " $seen " in *" $id "*) continue ;; esac
            seen="$seen $id"
            found=1
            local tier="local"
            if [ -f "$SHARED_DIR/$id.txt" ] && [ ! -f "$LOCAL_DIR/$id.txt" ]; then
                tier="shared"
            elif [ -f "$SHARED_DIR/$id.txt" ] && [ -f "$LOCAL_DIR/$id.txt" ]; then
                tier="local*"
            fi
            local text
            text=$(cat "$(resolve "$id")")
            printf "  %-12s [%-7s] %s\n" "$id" "$tier" "$text" >&2
        done
    done
    if [ "$found" -eq 0 ]; then
        echo "No macros defined." >&2
    fi
}

case "${1:-}" in
    "")
        list_macros
        ;;
    --help|-h)
        usage
        ;;
    *)
        id="$1"
        shift

        if [ $# -eq 0 ]; then
            file=$(resolve "$id")
            if [ -z "$file" ]; then
                echo "Macro '$id' not found." >&2
                exit 1
            fi
            cat "$file"
        elif [ "$1" = "--clear" ]; then
            rm -f "$LOCAL_DIR/$id.txt" "$SHARED_DIR/$id.txt"
            rmdir "$LOCAL_DIR" 2>/dev/null || true
            rmdir "$SHARED_DIR" 2>/dev/null || true
            echo "Macro '$id' cleared." >&2
        elif [ "$1" = "--promote" ]; then
            if [ ! -f "$LOCAL_DIR/$id.txt" ]; then
                echo "Macro '$id' not found in local." >&2
                exit 1
            fi
            mkdir -p "$SHARED_DIR"
            cp "$LOCAL_DIR/$id.txt" "$SHARED_DIR/$id.txt"
            rm -f "$LOCAL_DIR/$id.txt"
            echo "Macro '$id' promoted to shared." >&2
        elif [ "$1" = "--demote" ]; then
            if [ ! -f "$SHARED_DIR/$id.txt" ]; then
                echo "Macro '$id' not found in shared." >&2
                exit 1
            fi
            mkdir -p "$LOCAL_DIR"
            cp "$SHARED_DIR/$id.txt" "$LOCAL_DIR/$id.txt"
            rm -f "$SHARED_DIR/$id.txt"
            rmdir "$SHARED_DIR" 2>/dev/null || true
            echo "Macro '$id' demoted to local." >&2
        else
            mkdir -p "$LOCAL_DIR"
            echo "$*" > "$LOCAL_DIR/$id.txt"
            echo "Macro '$id' saved." >&2
        fi
        ;;
esac
