---
name: "react-team"
description: "Triforce team for React/TypeScript: developer (Sebastian), tester (Kent), critic (Dan)."
---

# /react-team — React Triforce

Launch 3 parallel agents to develop, test, and critique React/TypeScript code.

## Usage

```
/react-team <task description or file path>
```

## Roles

1. **Developer** (`.claude/agents/sebastian.md`) — Sebastian Markbåge. Writes composed, platform-aligned React.
2. **Tester** (`.claude/agents/kent.md`) — Kent C. Dodds. Writes behavior-focused tests with Testing Library.
3. **Critic** (`.claude/agents/dan.md`) — Dan Abramov. Evaluates for over-engineering, wrong mental models, and premature abstractions.

## Workflow

1. Developer writes the code first.
2. Tester receives the developer's output and writes tests against it.
3. Critic receives both and evaluates.

All three run in parallel when reviewing existing code.

## Implementation

Spawn 3 Agent calls using the persona files. Each agent reads the relevant source and produces output scoped to their role.
