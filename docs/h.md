# h - Highlight Text Patterns

Highlight text patterns in color by piping text into it.

## Usage

```bash
command | h pattern [color]
```

## Parameters

- `pattern` - The text pattern or regex to highlight (required)
- `color` - Color to use for highlighting (optional, defaults to red)

## Available Colors

- black, red, green, yellow, blue, magenta, cyan, white
- bright-black, bright-red, bright-green, bright-yellow, bright-blue, bright-magenta, bright-cyan, bright-white

## Examples

### Highlight ERROR in red (default)
```bash
cat file.log | h ERROR
```

### Highlight WARNING in yellow
```bash
tail -f app.log | h WARNING yellow
```

### Highlight patterns in other colors
```bash
docker ps | h running green
```

### Monitor logs with multiple highlights
```bash
# First highlight errors, then warnings (using multiple pipes)
tail -f app.log | h ERROR red | h WARNING yellow
```

### Use with Azure CLI
```bash
az webapp log tail --name myapp --resource-group myrg | h Exception bright-red
```

### Highlight in git diffs
```bash
git --no-pager diff | h '^[+-]{1}[^+-]' cyan
```

### Find and highlight specific patterns
```bash
dmesg | h error red
```

---

## Implementation Details

### macOS Compatibility Issue

macOS ships with bash 3.2 (from 2007), which does not support associative arrays (`declare -A`). The Linux implementation of `h` uses associative arrays for color mapping, which causes the script to fail on macOS with the error:

```
declare: -A: invalid option
```

Bash 4.0+ (released in 2009) added support for associative arrays, but macOS continues to ship with bash 3.2 due to licensing reasons (bash 4+ uses GPLv3).

## Solution

The script now detects the operating system via `uname -s` and selects the appropriate highlighting method:

- **macOS (darwin)**: Uses a `sed`-based highlighter that works with bash 3.2
  - Color names are resolved via a `case` statement (no associative arrays needed)
  - Highlighting is done with ANSI escape sequences injected via sed
  
- **Linux and other systems**: Uses `grep` with `GREP_COLORS` environment variable
  - Retains the original associative array for color mapping
  - Leverages grep's built-in color support for better performance

No additional software is required on either OS - both implementations use standard utilities.

## Historical Context

### Commit f195548: Original sed-based Implementation
The initial version used `sed` to inject ANSI escape sequences directly into the text:

```bash
sed -E "s/$pattern/\x1b[${color_code}m&\x1b[0m/g"
```

This approach is portable and works on bash 3.2+, but processes the entire input through sed.

### Commit 4dee6a9: Switch to grep + GREP_COLORS
The script was updated to use grep's native color support:

```bash
export GREP_COLORS="mt=${color_code}"
grep --color=always -E "$pattern|$"
```

This method is cleaner and potentially faster, but requires bash 4+ for the associative array color mapping.

### Current: Cross-Platform Implementation
The script now combines both approaches, automatically selecting the appropriate method based on the detected OS.

## Technical Notes

- Both implementations use extended regular expressions (`-E` flag)
- The macOS `sed` version uses `$'\x1b'` for proper ANSI escape sequence handling in bash
- The Linux `grep` version uses the `GREP_COLORS` environment variable with the `mt` (matching text) parameter
- Pattern matching behavior is identical across both implementations
- The default color is red (31) if an invalid color name is provided

## Testing

### On macOS:
```bash
echo 'alpha beta gamma' | h beta red
printf 'x\ny\nz\n' | h y yellow
h localhost /etc/hosts cyan
```

### On Linux:
```bash
echo 'alpha beta gamma' | h beta red
dmesg | h 'error|warn' yellow
```

Both should produce colored output with no syntax errors.
