#!/usr/bin/env bash
# h - highlight text patterns in color
# Usage: command | h pattern [color]
# Example: cat file.log | h ERROR red

# Detect OS
uname_s=$(uname -s 2>/dev/null | tr '[:upper:]' '[:lower:]')
if [[ "$uname_s" == darwin* ]]; then
  on_darwin=true
else
  on_darwin=false
fi

pattern="${1:-}"
color="${2:-red}"

if [[ -z "$pattern" ]]; then
  echo "Usage: command | h pattern [color]" >&2
  echo "Available colors: black red green yellow blue magenta cyan white bright-black bright-red bright-green bright-yellow bright-blue bright-magenta bright-cyan bright-white" >&2
  exit 1
fi

# Function to get color code (works without associative arrays)
get_color_code() {
  case "$1" in
    black) echo "30" ;;
    red) echo "31" ;;
    green) echo "32" ;;
    yellow) echo "33" ;;
    blue) echo "34" ;;
    magenta) echo "35" ;;
    cyan) echo "36" ;;
    white) echo "37" ;;
    bright-black) echo "90" ;;
    bright-red) echo "91" ;;
    bright-green) echo "92" ;;
    bright-yellow) echo "93" ;;
    bright-blue) echo "94" ;;
    bright-magenta) echo "95" ;;
    bright-cyan) echo "96" ;;
    bright-white) echo "97" ;;
    *) echo "31" ;;  # default to red
  esac
}

if [[ "$on_darwin" == true ]]; then
  # macOS path: use sed-based highlighting (bash 3.2 compatible)
  color_code=$(get_color_code "$color")
  # Use $'\x1b' for proper escape sequence in bash
  esc=$'\x1b'
  sed -E "s/$pattern/${esc}[${color_code}m&${esc}[0m/g"
else
  # Linux path: use grep with GREP_COLORS (requires bash 4+ for associative arrays)
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
  
  color_code="${colors[$color]:-31}"
  
  # Set GREP_COLORS to use the specified color
  # Format: mt=color_code where mt is the matched text color
  export GREP_COLORS="mt=${color_code}"
  
  # Use grep to highlight the pattern (matches line if pattern exists, shows all lines)
  grep --color=always -E "$pattern|$"
fi
