# Configuration

Config lives in `$K7E_HOME/config.json`. Every key has an environment-variable
override (env wins over file). Inspect effective state with `k7e status`.

```bash
k7e config <key>          # read (shows the resolved value for llm/llm_model)
k7e config <key> <value>  # write to config.json
k7e status                # what's active + recommendations
```

## Keys

| Key | Env override | Default | Meaning |
|-----|--------------|---------|---------|
| `llm` | `K7E_LLM` | `ollama` | `ollama` or `none` (disable LLM features) |
| `llm_model` | `K7E_LLM_MODEL` | *(auto-detect)* | pin the ollama generation model |
| `embeddings` | `K7E_EMBEDDINGS` | `ollama` | `ollama` or `none` |
| `embed_model` | `EMBED_MODEL` | `nomic-embed-text` | embedding model |
| `ollama_url` | `OLLAMA_URL` | `http://localhost:11434` | ollama endpoint |
| `rerank` | `K7E_RERANK` | off | LLM rerank in `search` by default |
| `decay_offset_days` | `K7E_DECAY_OFFSET` | 30 | flat (no-decay) window |
| `decay_scale_days` | `K7E_DECAY_SCALE` | 365 | decay half-life; `<=0` disables decay |
| `use_count_weight` | `K7E_USE_WEIGHT` | 0.2 | strength of use-count boost |

See [retrieval.md](retrieval.md#tuning) for what the ranking knobs do.

## LLM backend

k7e calls **ollama's HTTP API directly** for all generation (distill, recall,
rerank, compile). It does **not** shell out to LLM CLIs like `l9m`, `claude`, or
`codex`: those carry their own rolling context or agent preamble, which would
leak into k7e's prompts. Every k7e LLM call is stateless (`think: false`,
no history).

### Model resolution

When `llm_model` is unset, k7e auto-detects from installed ollama models:
qwen family preferred, largest parameter count wins, falling back to
`qwen3:0.6b`. Pin a specific model to override:

```bash
k7e config llm_model qwen3.6:27b   # or any installed model
```

### Seeing which model is in use

This is the fast answer to "what LLM is k7e actually using?":

```bash
k7e status
#   LLM: ollama (qwen3.6:27b, auto-detected) ✓

k7e config llm_model
#   qwen3.6:27b (auto-detected)      # resolved value, even when unset

k7e config llm
#   ollama (default)
```

### Disabling the LLM

```bash
k7e config llm none      # explicit opt-out: distill/recall/compile become unavailable
```

## Embeddings

Semantic search needs an embedding model pulled into ollama:

```bash
ollama pull nomic-embed-text
```

Without it (or with `embeddings none`), k7e runs FTS5-only — still effective for
keyword recall. Embeddings are computed via ollama's `/api/embed`, separate from
the generation model above.

## What needs what

k7e's store and keyword search (FTS5) are always available offline. The
LLM-powered commands **fail fast** with an actionable message rather than
degrading silently:

| Missing | Still works | Unavailable (fails fast) |
|---------|-------------|--------------------------|
| ollama entirely | store, FTS5 search, get, list, stats | semantic search; `distill`, `recall`, `compile` |
| embed model only | everything except semantic search | vector recall |
| `llm=none` | store, FTS5 search, get, list, stats | `distill`, `recall`, `compile` |

`k7e status` always reports exactly what's active and what to install.
