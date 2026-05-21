---
name: research
description: "Deep research via OpenAI o4-mini-deep-research. Requires OPENAI_API_KEY."
disable-model-invocation: false
allowed-tools: Bash(research *)
argument-hint: "prompt text"
user-invocable: true
---

# Research Skill

This skill provides a command-line interface to OpenAI's Deep Research API (`o4-mini-deep-research`). It allows for multi-step, agentic research into complex topics with full source transparency.

## Usage

```bash
research "Your research prompt here"
```

The tool concatenates all arguments into a single prompt.

## How it Works

1. **Environment Requirement:** Requires `OPENAI_API_KEY` to be set. Fails immediately if missing.
2. **Caching:** To prevent duplicate API calls and costs, it generates a SHA-256 hash of the prompt (with all whitespace removed). 
3. **Storage:** Responses are cached in `.files/research/<hash>.json` (relative to project root).
4. **Execution:** 
   - If a hash exists, it retrieves the request ID and resumes polling.
   - If no hash exists, it submits a new request to the OpenAI Responses API.
5. **Output:** Returns the final result as a JSON object, making it compatible with `jq`.
6. **Polling:** Automatically polls every 30 seconds until the research task is `completed` or `failed`.

## Technical Details

- **Binary:** `bin/research` (Polyglot shell/python wrapper)
- **Implementation:** `skills/research/research.py`
- **Cache:** `.files/research/` (relative to project root)
- **Dependencies:** Python 3 (standard library only: `urllib`, `hashlib`, `json`).
