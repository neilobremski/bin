#!/bin/bash
# h - highlight text patterns in color
# Usage: command | h pattern [color]
# Example: cat file.log | h ERROR red

# Color codes
declare -A colors=(
  [black]=30
  [red]=31
  [green]=32
  [yellow]=33
  [blue]=34
  [magenta]=35
  [cyan]=36
  [white]=37
  [bright-black]=90
  [bright-red]=91
  [bright-green]=92
  [bright-yellow]=93
  [bright-blue]=94
  [bright-magenta]=95
  [bright-cyan]=96
  [bright-white]=97
)

pattern="${1:-}"
color="${2:-red}"

if [[ -z "$pattern" ]]; then
  echo "Usage: command | h pattern [color]" >&2
  echo "Available colors: ${!colors[*]}" >&2
  exit 1
fi

color_code="${colors[$color]:-31}"

# Highlight the pattern with the specified color
sed -E "s/$pattern/\x1b[${color_code}m&\x1b[0m/g"
