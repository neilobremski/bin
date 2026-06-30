# l9m — Local LLM Interface

Lightweight CLI for local LLM interaction via ollama. Auto-detects the best
installed model, streams responses, handles all the plumbing.

## Usage

```bash
# Simple prompt (positional)
l9m "what is 2+2"

# Explicit prompt flag
l9m -p "explain this error"

# Pipe input
echo "some code" | l9m -p "explain this"

# Stdin as full prompt
cat notes.md | l9m

# Structured output
l9m -p "list AWS regions" --type list

# Silent (no stderr)
l9m -s -p "one word answer"

# Show resolved model
l9m --model

# Render markdown via glow (auto-detects light/dark terminal)
l9m --glow auto -p "explain quicksort in markdown"

# Explicit glow theme
l9m --glow dracula -p "explain quicksort in markdown"
L9M_GLOW=light l9m --chat

# Context from file
l9m -p "summarize" -c document.md
```

## Model Resolution

Order of precedence:
1. `MODEL` env var — explicit override
2. `~/.cache/l9m.env` — cached default from last detection
3. Best installed qwen model from `ollama list` (version-sorted, largest wins)
4. Fallback: pull `qwen3:0.6b` (smallest, works everywhere)

```bash
# Override for one call
MODEL=qwen3:0.6b l9m -p "fast answer"

# Clear cache to re-detect
rm ~/.cache/l9m.env
```

## Flags

| Flag | Description |
|------|-------------|
| `-p, --prompt` | Prompt text |
| `-t, --type` | Response type: `bash`, `bool`, `list` |
| `-i, --instruction` | Instruction framing for the prompt |
| `-c, --context` | Read context from file |
| `-e, --echo` | Echo assembled prompt before generation |
| `-s, --silent` | Suppress stderr output |
| `--glow <theme>` | Render markdown via glow (`auto`, `dark`, `light`, `dracula`, …) |
| `--model` | Print resolved model name and exit |

`--glow auto` queries the terminal background (OSC 11) and picks `dark` or
`light`. Override with any glow theme name, or set `L9M_GLOW=<theme>`.

## Integration

Other tools use l9m as their LLM backend:
- **q3w** — NLP to bash commands
- **k7e** — knowledge distillation (auto-detected when on PATH)
