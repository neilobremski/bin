# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo shape

`~/bin/` is a personal utilities repo plus two substantive sub-projects.

- **Top level** — small single-file CLIs (`tell`, `n0b`, `speak`, `h`, `NMP.py`, etc.).
  `install.sh` adds the dir to `$PATH` and links docs/skills.
- **`apps/n0b/`** — Kitchen-sink CLI namespace (`n0b json`, `n0b az`, `n0b ai`, …).
  Docs in [`apps/n0b/docs/`](apps/n0b/docs/); index at [`apps/n0b/README.md`](apps/n0b/README.md).
- **`apps/h4l/`** — Hall chat rooms (`h4l dispatch`, slash commands, `.chatrooms/` state).
  See [`apps/h4l/README.md`](apps/h4l/README.md).
- **`apps/a8s/`** — Agent Infinity System. Filesystem-based message router
  letting independent CLI agents (Claude, Gemini, Codex, scripts) talk to each
  other via `tell`. See [`apps/a8s/README.md`](apps/a8s/README.md) for concept
  and usage, [`apps/a8s/DEVELOPMENT.md`](apps/a8s/DEVELOPMENT.md) for hard
  constraints and historical decisions.
- **`apps/k7e/`** — Knowledge accumulation engine. Flat markdown files +
  SQLite FTS5 + optional ollama embeddings. Zero non-stdlib deps for core.
  See [`apps/k7e/README.md`](apps/k7e/README.md) for usage and architecture.
- **`docs/`** — markdown for each top-level command + symlinks for skill install.
- **`venv/`** — local Python virtualenv (gitignored). Pytest is installed there.
  Run `python3 -m pytest ...` from anywhere; it picks up `venv` automatically
  via the wrapper at `bin/python3`.

## Conventions

### Shebangs

All bash scripts use `#!/usr/bin/env bash` (not `#!/bin/bash`). macOS ships
bash 3.2.57; users with Homebrew bash get a modern version this way. Don't
introduce `#!/bin/bash`.

### Polyglot bash + PowerShell scripts

Cross-platform CLIs (`a8s`, `tell`) are polyglots — the same file is valid
bash AND PowerShell. The bash side delegates to Python; the PowerShell side
finds `python3`/`python`/`py` via `Get-Command`. The pattern uses
`echo \`# <#` >/dev/null` as a no-op for bash that opens a PowerShell
multi-line comment. `tell` is a thin shim around `a8s tell`; don't add new
polyglots without reading an existing one (e.g., `~/bin/a8s`) first.

### Install hook

`install.sh` is sourced from a shell rc. It adds `~/bin/` to `$PATH`. Pass
`--skills` to also symlink `docs/*.md` and `apps/n0b/docs/*.md` into `~/.claude/skills/` (when Claude
Code is present) and `~/.cursor/skills/` for Cursor. Per-agent skill install
is `a8s install` from the agent directory (see below).

Adding a new top-level CLI: write the script, write `docs/<name>.md` with YAML
frontmatter if it should be installable as a Claude skill.

### Workflow

**Issues + feature branches off `main`. No direct commits to `main`.** Every
change goes through a PR. The user squash-merges fast. After a squash, rebase
follow-up work onto fresh `main` rather than stacking — squash hashes don't
match the original branch's commits and stacking causes conflicts.

### Pre-v1 / scorch-the-earth (a8s only)

`a8s` is explicitly pre-v1. **Do not write migration code.** When the schema
changes, the user wipes `~/.a8s/` and re-derives state via `a8s discover` +
`a8s add`. This applies to registry shape, mailbox layout, definition schema,
and on-disk pid/log paths. The contract changes only when the user declares v1.

### Commit style

- Commits prefixed `feat(a8s)` / `fix(a8s)` / `refactor(a8s)` / `test(a8s)` /
  `docs(a8s)` per Conventional Commits. The `(a8s)` scope appears for a8s
  changes; smaller top-level scripts use `feat(<script>)` etc.
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
- Don't add backwards-compat hacks. See pre-v1 above.

### SKILL.md YAML — quoted scalars only

Codex's YAML parser is strict and fails silently on unquoted descriptions
containing colons or `FILE:` lines. Always quote `name:` and `description:`
in skill frontmatter.

## Top-level scripts: `tell`

`~/bin/tell` is a **thin shim** to `a8s tell` (plus `tell.cmd` on Windows).
Implementation lives in `apps/a8s/tell.py`. It requires `TELL_OUTBOX_DIR`
(set by a8s on agent wake) and atomic-writes a JSON envelope there — no
filesystem discovery. When the registry is reachable and CWD is inside a
registered agent, recipient validation, `from` stamping, and agent logging
apply on top.

The router (`mailbox.py:_process_pending`) force-overwrites `from` based on
which agent owns the enclosing root — the filesystem is the unforgeable
identity.

Run `a8s install` from an agent root to link bundled skills into
`.claude/skills/`, `.cursor/skills/`, and `.codex/skills/` there. Use `a8s install --global` for
user-home install; `source ~/bin/install.sh --skills` for top-level doc skills.

## Common operations

```bash
# a8s tests (~640 tests)
python3 -m pytest apps/a8s/tests/

# k7e tests (~69 tests)
cd apps/k7e && tests/run

# Start fresh after a schema change (pre-v1 scorch-the-earth)
rm -rf ~/.a8s/agents/ ~/.a8s/a8s.json
a8s discover apps/a8s/tests/agents

# Tail per-agent activity
a8s logs CLAUDE GEMINI -f

# Clear local inbox without invoking
a8s drain my-agent

# Flush MQTT-queued messages (connect, trash for N seconds, exit)
a8s run my-agent --drain 5
```

## Memory note

The user has a private memory system at
`~/.claude/projects/-Users-neilo-bin/memory/` — that's separate from this
file. Personal preferences, ongoing project state, and feedback rules live
there. THIS file (`CLAUDE.md`) is the public-checked-in onboarding doc.
Don't put anything in it that would be inappropriate for a public repo.
