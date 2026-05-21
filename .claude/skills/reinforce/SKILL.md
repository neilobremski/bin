---
name: reinforce
description: Set, view, or clear a reinforcement message injected before every orchestrator tool call.
disable-model-invocation: false
allowed-tools: Bash(reinforce *)
argument-hint: ["message to reinforce" | --clear]
user-invocable: true
---

# Reinforcement Message

Set a short message that gets injected into the orchestrator's context before every Bash, Edit, Write, and Agent tool call. Sub-agents never see it.

## Usage

```
/reinforce "NO COWBOY EDITS — delegate to specialized agents"
/reinforce              # show current message
/reinforce --clear      # remove it
```

## How it works

- `reinforce "msg"` writes to `.temp/reinforce.txt` (repo-local, gitignored)
- The `PreToolUse` hook reads that file and injects it as `additionalContext`
- If the file is empty or missing, no injection happens
- Sub-agents are excluded via `agent_id` detection in the hook
