---
name: "python-team"
description: "Triforce team for Python: developer (Lukasz), tester (Raymond), critic (Beazley)."
---

# /python-team — Python Triforce

Launch 3 parallel agents to develop, test, and critique Python code.

## Usage

```
/python-team <task description or file path>
```

## Roles

1. **Developer** (`.claude/agents/lukasz.md`) — Łukasz Langa. Writes typed, formatted, modern Python.
2. **Tester** (`.claude/agents/raymond.md`) — Raymond Hettinger. Writes elegant, thorough tests.
3. **Critic** (`.claude/agents/beazley.md`) — David Beazley. Evaluates for over-engineering, concurrency issues, and missed simplifications.

## Workflow

1. Developer writes the code first.
2. Tester receives the developer's output and writes tests against it.
3. Critic receives both and evaluates.

All three run in parallel when reviewing existing code.

## Implementation

Spawn 3 Agent calls using the persona files. Each agent reads the relevant source and produces output scoped to their role.
