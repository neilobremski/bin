#!/usr/bin/env bash
MODEL="qwen3.5"
p="$1"
[ -z "$p" ] && p=$(cat)
total_output=""

# Show an immediate prompt preview before the model emits anything.
initial_preview="${p%%$'\n'*}"
suffix=""
if ((${#initial_preview} > 80)); then
    initial_preview=${initial_preview:0:80}
    suffix="..."
fi
printf '\r\033[K\033[0;90m%s%s\033[0m' "$initial_preview" "$suffix" >&2

# Process output line-by-line for the 'peek' display
while IFS= read -r line; do
    preview=${line//$'\r'/}
    suffix=""
    if ((${#preview} > 80)); then
        preview=${preview:0:80}
        suffix="..."
    fi

    if [ -n "$preview" ]; then
        printf '\r\033[K\033[0;90m%s%s\033[0m' "$preview" "$suffix" >&2
    fi
    total_output+="$line"$'\n'
done < <(echo "$p" | ollama run --think=false --hidethinking --nowordwrap $MODEL 2>/dev/null)

# Clear the peek line entirely on exit.
echo -ne "\r\033[K" >&2
echo "$total_output"
