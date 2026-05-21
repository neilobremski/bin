---
name: "triforce"
description: "Deploy a Developer + Tester + Critic team for the current task. Picks the right team automatically."
---

# /triforce — Triforce Teams

Deploy a three-persona team: **Developer**, **Tester**, **Critic**. Each role has a distinct job and they work together to produce shipping code with tests and critical evaluation.

## Usage

```
/triforce <task description>
```

## Roles

| Role | Job | Output |
|------|-----|--------|
| **Developer** | Write the implementation | Working code |
| **Tester** | Write and run tests against the implementation | Test file(s) that pass |
| **Critic** | Evaluate both for architecture, bugs, and antipatterns | Prioritized fix list |

## Available Teams

| Team | Domain | Developer | Tester | Critic |
|------|--------|-----------|--------|--------|
| `/python-team` | Python (general) | Łukasz | Raymond | Beazley |
| `/react-team` | React/TypeScript | Sebastian | Kent | Dan |
| `/k7e-team` | K7E (Python/SQLite) | Willison | Chase | Karpathy |
| `/android-team` | Android/Kotlin | Wharton | Alcérreca | Haase |
| `/gas-team` | Google Apps Script | Crockford | Rauch | Hejlsberg |

## Workflow

**For new features:**
1. Developer writes the code (Agent, foreground — need the output)
2. Tester + Critic run in parallel on the developer's output

**For reviewing existing code:**
- All three run in parallel, each reading the current state

**For fixing a bug:**
1. Critic identifies the root cause
2. Developer fixes it
3. Tester writes a regression test

## Team Selection

Pick the team based on the codebase:
- Working in `apps/k7e/` → `/k7e-team`
- Working in `~/repos/a8s-android/` or Kotlin → `/android-team`
- Working in `~/repos/gas/` or Google Apps Script → `/gas-team`
- React/TypeScript frontend code → `/react-team`
- General Python (a8s, scripts, CLI tools) → `/python-team`
- If ambiguous, ask.

## Implementation

Read the persona files at `.claude/agents/<name>.md` for each team member. Spawn agents with those personas, scoping their task to their role. The Developer writes code, the Tester writes tests, the Critic evaluates. Synthesize a final summary with: what was built, what's tested, what the critic flagged, and what to fix.
