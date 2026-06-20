---
name: research
description: "Deep research via n0b ai research (o4-mini-deep-research). Requires OPENAI_API_KEY."
disable-model-invocation: false
allowed-tools: Bash(n0b ai research *)
argument-hint: "prompt text"
user-invocable: true
---

# Research Skill

Canonical documentation: [`apps/n0b/docs/research.md`](../../apps/n0b/docs/research.md).

```bash
n0b ai research "Your research prompt here"
```

Implementation: `apps/n0b/research.py` (stdlib only). Cache: `.files/research/<hash>.json`.
