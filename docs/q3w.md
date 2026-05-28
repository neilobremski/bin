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
2. The command is validated as parseable bash (syntax check rejects garbage)
3. A second LLM call evaluates whether the command is dangerous — this
   also serves as a natural delay giving you time to read and CTRL+C
4. If flagged dangerous: prompts for Y/N confirmation
5. Use `--force` to skip the prompt (still shows a warning)
6. Use `--dry-run` / `-n` to print without executing at all

## Flags

| Flag | Description |
|------|-------------|
| `-n, --dry-run` | Print generated command without executing |
| `-f, --force` | Skip danger prompt (still warns) |

## How It Works

1. Your words become a prompt to l9m with `--type bash`
2. l9m generates a bash command (constrained output — no explanation)
3. q3w prints the command (gray, prefixed with `$`)
4. Bash syntax validation (`$SHELL -n -c`)
5. Second l9m call checks if the command is dangerous (natural delay)
6. If dangerous: prompt for confirmation (or warn with `--force`)
7. Executes via `$SHELL -c "<command>"`

## Philosophy

"Programs not speech" — the LLM interacts with the world exclusively
through structured, executable artifacts. No free-form text reaches the
terminal unless it's a valid program.
