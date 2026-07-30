# AGENTS.md

This file provides guidance when working with code in this repository.

## Repo shape

`~/bin/` is a personal utilities repo plus a handful of sub-projects under
`apps/`. The Ark suite (a8s, r4t, k7e) lives at
[github.com/witw-llc/ar3](https://github.com/witw-llc/ar3).

- **Top level** — small single-file CLIs (`n0b`, `h`, `l9m`, `NMP.py`, etc.).
  `install.sh` adds the dir to `$PATH` and links docs/skills.
- **`apps/n0b/`** — Kitchen-sink CLI namespace (`n0b json`, `n0b az`, `n0b ai`, …).
  Docs in [`apps/n0b/docs/`](apps/n0b/docs/); index at [`apps/n0b/README.md`](apps/n0b/README.md).
- **`apps/h4l/`** — Hall chat rooms (`h4l dispatch`, slash commands, `.chatrooms/` state).
  See [`apps/h4l/README.md`](apps/h4l/README.md).
- **`apps/l9m/`, `apps/q3w/`** — local-LLM prompt CLI and its natural-language
  shell-command sibling.
- **`docs/`** — markdown for each top-level command + symlinks for skill install.
- **`requirements/`** — consolidated pip deps for the shared repo venv (see
  `requirements/README.md`). Per-app `requirements.txt` files point here.
- **`.venv/`** — shared local Python virtualenv at the repo root (gitignored).
  `lib/venv_util.py` bootstraps it on first use; `n0b ai` and other apps share
  it so PyTorch and friends install once. Run `python3 -m pytest ...` with
  `.venv/bin/python3` after `pip install -r requirements/dev.txt`, or use the
  legacy `venv/` until migrated.

## Conventions

### Shebangs

All bash scripts use `#!/usr/bin/env bash` (not `#!/bin/bash`). macOS ships
bash 3.2.57; users with Homebrew bash get a modern version this way. Don't
introduce `#!/bin/bash`.

### Polyglot bash + PowerShell scripts

Cross-platform CLIs (`n0b`, `h4l`, `b3t`, `l9m`, `q3w`) are polyglots — the same
file is valid bash AND PowerShell. The bash side delegates to Python; the
PowerShell side finds `python3`/`python`/`py` via `Get-Command`. The pattern uses
`echo \`# <#` >/dev/null` as a no-op for bash that opens a PowerShell
multi-line comment. Don't add new polyglots without reading an existing one
(e.g., `~/bin/n0b`) first.

Windows can't run the extensionless polyglot from `PATH`, so the important
top-level commands also ship a sibling `.ps1` (PowerShell prefers it over the
extensionless file) and a `.cmd` for `cmd.exe`. Both are thin: resolve the
repo dir, find python, exec the entry-point `.py`, propagate the exit code.

### Install hook

`install.sh` is sourced from a shell rc. It adds `~/bin/` to `$PATH`. Pass
`--skills` to also symlink `docs/*.md` and `apps/n0b/docs/*.md` into `~/.claude/skills/` (when Claude
Code is present) and `~/.cursor/skills/` for Cursor. That mechanism installs the
user's own tool docs.

Adding a new top-level CLI: write the script, write `docs/<name>.md` with YAML
frontmatter if it should be installable as a Claude skill.

### Workflow

**Issues + feature branches off `main`. No direct commits to `main`.** Every
change goes through a PR. The user squash-merges fast. After a squash, rebase
follow-up work onto fresh `main` rather than stacking — squash hashes don't
match the original branch's commits and stacking causes conflicts.

### Commit style

- Conventional Commits with the app or script as the scope — `feat(n0b)`,
  `fix(h4l)`, `refactor(l9m)`, `test(q3w)`, `docs(<script>)`.
- Body explains the *why* and the design decision, not just the mechanical
  *what*.
- Co-author trailer for AI-assisted work:
  `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`

### Code style

- Default to no comments. Names should explain what; comments are only for
  *why* something non-obvious is done.
- Avoid emojis in source unless asked.
- Don't add abstractions that aren't being used today. Three similar lines is
  fine; abstract on the fourth.
- Don't add error handling for cases that can't happen. Trust internal
  guarantees; validate at boundaries (CLI input, external APIs, filesystem).

### SKILL.md YAML — quoted scalars only

Harness YAML parsers differ: copilot rejects unquoted descriptions
containing colons outright (skill dropped), and other parsers have their
own strictness. Always quote `name:` and `description:` in skill
frontmatter.

## Common operations

```bash
# PII unit tests
python3 -m pytest tests/test_pii.py

# h4l tests
python3 -m pytest apps/h4l/tests/

# l9m + q3w tests (skip the ones needing a live model)
python3 -m pytest apps/l9m/tests/ apps/q3w/tests/ -m "not llm"

# n0b tests
python3 -m pytest apps/n0b/tests/
```

## Memory note

The user has a private memory system at
`~/.claude/projects/-Users-neilo-bin/memory/` — that's separate from this
file. Personal preferences, ongoing project state, and feedback rules live
there. THIS file (`AGENTS.md`) is the public-checked-in onboarding doc.
Don't put anything in it that would be inappropriate for a public repo.
