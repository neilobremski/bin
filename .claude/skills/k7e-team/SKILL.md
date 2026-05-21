# /k7e-team — Expert Review Panel

Launch 3 parallel reviewers modeled after distinguished practitioners to critically evaluate K7E code changes.

## Usage

```
/k7e-team [focus area or file path]
```

## What it does

Spawns 3 background agents, each reviewing from a different expert perspective:

1. **Karpathy** (`.claude/agents/karpathy.md`) — Knowledge architecture. Does it compile and compound? Or just accumulate?
2. **Willison** (`.claude/agents/willison.md`) — Tool design. CLI ergonomics, SQLite usage, testability, unix philosophy.
3. **Chase** (`.claude/agents/chase.md`) — Memory systems. Latency, dedup, consolidation, failure modes at scale.

Each reviewer reads the K7E source at `~/bin/apps/k7e/` and provides:
- Top 3-5 critical issues
- Concrete fix for each (code-level, not vague)
- What's done well

## When to use

- Before merging significant K7E changes
- After implementing a new feature (compile, distill, etc.)
- When unsure about architectural direction
- Periodically as a health check

## Implementation

The skill operator should spawn 3 Agent calls in parallel using the agent profiles at `.claude/agents/{karpathy,willison,chase}.md` as persona context. Each agent reads the K7E codebase and returns a focused review. Synthesize the results into a prioritized fix list with consensus markers.
