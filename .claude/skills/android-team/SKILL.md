---
name: "android-team"
description: "Triforce team for Android/Kotlin: developer (Wharton), tester (Alcérreca), critic (Haase)."
---

# /android-team — Android Triforce

Launch 3 parallel agents to develop, test, and critique Android/Kotlin code.

## Usage

```
/android-team <task description or file path>
```

## Roles

1. **Developer** (`.claude/agents/wharton.md`) — Jake Wharton. Writes the implementation.
2. **Tester** (`.claude/agents/alcereca.md`) — Jose Alcérreca. Writes and runs tests.
3. **Critic** (`.claude/agents/haase.md`) — Chet Haase. Evaluates architecture, performance, correctness.

## Workflow

1. Developer writes the code first.
2. Tester receives the developer's output and writes tests against it.
3. Critic receives both and evaluates.

All three run in parallel when reviewing existing code. For new features, developer goes first, then tester + critic in parallel on the result.

## Implementation

Spawn 3 Agent calls using the persona files. The target codebase is `~/repos/a8s-android/`. Each agent reads the relevant source and produces output scoped to their role.
