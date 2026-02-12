# py-json-tool

Pretty-print and validate JSON from stdin or files using Python's built-in `json.tool` module.

## Usage

```bash
command | py-json-tool [options]
py-json-tool [options] [infile [outfile]]
```

## Examples

```bash
# Pretty-print from a pipe
curl -s https://api.example.com/data | py-json-tool

# Pretty-print a file
py-json-tool data.json

# Sort keys
curl -s https://api.example.com/data | py-json-tool --sort-keys

# Compact output (no pretty-print)
py-json-tool --compact data.json

# Write to file
py-json-tool data.json output.json
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
