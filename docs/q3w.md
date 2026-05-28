# q3w — NLP to Bash

Translates natural language into a bash command via l9m, shows it, then runs it.
The LLM must produce a program — it doesn't get to "speak" directly.

## Usage

```bash
# Generate and execute
q3w list all python files larger than 1MB

# Dry-run (print without executing)
q3w -n find processes using port 8080

# The command is always shown before execution:
# $ find . -name "*.py" -size +1M
```

## Safety

1. The generated command is always printed to stderr before execution
2. The command is validated as parseable bash (syntax check)
3. A brief delay after showing the command gives you time to CTRL+C
4. Use `--dry-run` / `-n` when you want to inspect without risk

## Flags

| Flag | Description |
|------|-------------|
| `-n, --dry-run` | Print generated command without executing |

## How It Works

1. Your words become a prompt to l9m with `--type bash`
2. l9m generates a bash command (constrained output — no explanation)
3. q3w prints the command (gray, prefixed with `$`)
4. Brief pause for visual inspection
5. Executes via `$SHELL -c "<command>"`

## Philosophy

"Programs not speech" — the LLM interacts with the world exclusively
through structured, executable artifacts. No free-form text reaches the
terminal unless it's a valid program.
