# PFC (Prefrontal Cortex)

Commander in chief of the synthetic organism. Receives stimulus, thinks via LLM, responds and coordinates.

**Does nothing without stimulus.** No stimulus files = immediate exit.

## Quick Start

From this directory:
```bash
# Detect which LLM is available
bin/think --detect

# Think about something
bin/think "What should we focus on today?"

# Process a stimulus
bin/think --stimulus '{"action":"plan","content":"we need to set up email","id":"t1","from":"brain"}'

# Full JSON output (reply + signals)
bin/think --json "How are the organs doing?"

# Pipe context
echo "hippocampus has 200 memories, ear transcribed 5 files today" | bin/think "Summarize the organism status"

# Force a specific provider
bin/think --provider claude "Hello"
```

## Organ Contract

```
brain/pfc/
├── live              # Entry point (called by spark)
├── cooldown          # 0 = fire every tick (but no-ops without stimulus)
├── bin/think         # Synchronous CLI for testing
├── src/              # Python modules
├── tests/            # pytest suite
├── .stimulus/        # Incoming signals
└── .memory/          # llm-provider cache
```

## LLM Provider

Auto-detects the first working CLI: `claude` > `gemini` > `codex`. Caches the choice in `.memory/llm-provider` to avoid slow re-detection. Cache is cleared automatically if the provider fails.

## Stimulus Protocol

The PFC accepts any stimulus. It sends the content to the LLM with a system prompt that describes available skills and known organs.

```bash
stimulus send --to pfc --body '{
  "action": "plan",
  "content": "We need to improve memory consolidation",
  "id": "corr-001",
  "from": "brain"
}'
```

The LLM responds with JSON:
```json
{
  "reply": "I suggest we tune the FSRS decay thresholds...",
  "signals": [
    {"to": "hippocampus", "body": {"action": "store", "content": "PFC decision: tune FSRS", "importance": 7}}
  ]
}
```

The PFC sends `reply` back to the original sender and dispatches any `signals` to other organs.

## Tests

```bash
python3 -m pytest tests/ -v
```
