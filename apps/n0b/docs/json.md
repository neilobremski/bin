---
name: n0b-json
description: "Pretty-prints and validates JSON via n0b json. Use when formatting or validating JSON output."
allowed-tools: Bash(n0b json *)
---

# n0b json

Pretty-print and validate JSON from stdin or files using Python's built-in `json.tool` module.

## Usage

```bash
command | n0b json [options]
n0b json [options] [infile [outfile]]
```

## Examples

```bash
# Pretty-print from a pipe
curl -s https://api.example.com/data | n0b json

# Pretty-print a file
n0b json data.json

# Sort keys
curl -s https://api.example.com/data | n0b json --sort-keys

# Compact output (no pretty-print)
n0b json --compact data.json

# Write to file
n0b json data.json output.json
```

## Options

All options are passed through to `python3 -m json.tool`. Common ones:

| Option | Description |
|--------|-------------|
| `--sort-keys` | Sort output by key |
| `--compact` | Compact instead of pretty-printed output |
| `--indent N` | Number of spaces for indentation (default: 4) |
| `--no-ensure-ascii` | Output non-ASCII characters as-is |
| `--json-lines` | Parse input as JSON Lines (one object per line) |
