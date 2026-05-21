---
name: "gas-team"
description: "Triforce team for Google Apps Script: developer (Crockford), tester (Rauch), critic (Hejlsberg)."
---

# /gas-team — GAS Triforce

Launch 3 parallel agents to develop, test, and critique Google Apps Script code.

## Usage

```
/gas-team <task description or file path>
```

## Roles

1. **Developer** (`.claude/agents/crockford.md`) — Douglas Crockford. Writes minimal, correct JS using only the good parts.
2. **Tester** (`.claude/agents/rauch.md`) — Guillermo Rauch. Tests messaging contracts, HTTP integration, edge cases.
3. **Critic** (`.claude/agents/hejlsberg.md`) — Anders Hejlsberg. Evaluates API surface, type safety, longevity, and GAS-specific pitfalls.

## Workflow

1. Developer writes the code first.
2. Tester receives the developer's output and writes tests against it.
3. Critic receives both and evaluates.

All three run in parallel when reviewing existing code.

## Implementation

Spawn 3 Agent calls using the persona files. The target codebase is `~/repos/gas/`. Each agent reads the relevant source and produces output scoped to their role. All agents understand GAS constraints (no modules, no persistent state, 6-min timeout, UrlFetchApp for HTTP, PropertiesService for config).
