---
name: "k7e-team"
description: "Triforce team for K7E (Python/SQLite): developer (Willison), tester (Chase), critic (Karpathy)."
---

# /k7e-team — K7E Triforce

Launch 3 parallel agents to develop, test, and critique K7E knowledge engine code.

## Usage

```
/k7e-team <task description or file path>
```

## Roles

1. **Developer** (`.claude/agents/willison.md`) — Simon Willison. Writes the implementation.
2. **Tester** (`.claude/agents/chase.md`) — Harrison Chase. Writes and runs tests.
3. **Critic** (`.claude/agents/karpathy.md`) — Andrej Karpathy. Evaluates architecture and knowledge compounding.

## Workflow

1. Developer writes the code first.
2. Tester receives the developer's output and writes tests against it.
3. Critic receives both and evaluates.

All three run in parallel when reviewing existing code. For new features, developer goes first, then tester + critic in parallel on the result.

## Implementation

Spawn 3 Agent calls using the persona files. The target codebase is `~/bin/apps/k7e/`. Each agent reads the relevant source and produces output scoped to their role.
