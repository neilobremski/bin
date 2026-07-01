# Distillation

Turning raw experience (notes, transcripts, command output, images) into
durable, deduplicated knowledge entries.

```bash
k7e distill notes.md
k7e distill ./transcripts/      # a whole directory
k7e distill notes.md --dry-run  # show candidates, store nothing
```

**Distillation requires an LLM (ollama).** The CLI fails fast with an
actionable message when ollama is unavailable — there is no offline
pattern-matching fallback. Set `llm=none` to explicitly opt out (extraction
returns nothing).

## Pipeline

```
raw file ─┬─ text  ─ LLM extraction (ollama, chunked)
          │              │
          │         dedup across chunks
          │              │
          └─ media ─ ollama vision (images only)
                         │
                         ▼
                  diff vs existing store ─► store genuine deltas
```

### Text extraction

1. **LLM extraction** — the text is chunked (~3000 chars, 200 overlap) and each
   chunk is sent to the model with a strict "extract only genuinely novel
   knowledge" prompt (max 3 items/chunk). Output is parsed as a JSON array of
   `{title, content, tags}`.
2. **Dedup** — candidates are deduplicated across chunks before storage.

### Media extraction

- **Images** — base64-encoded and sent to a vision-capable ollama model for
  description; the binary is stored as a content-addressed asset.
- **Audio / video** — *not supported via ollama.* Transcribe with a dedicated
  tool first, then distill the resulting text. (This capability previously
  relied on cloud LLM CLIs, which were removed — see
  [configuration.md](configuration.md#llm-backend).)

## Delta detection

Before storing, candidates are diffed against the existing store so distillation
is idempotent-ish: re-running over the same input doesn't pile up duplicates.
Genuinely new or changed knowledge becomes new entries.

## Related write operations

- `k7e consolidate [--dry-run]` — find and merge duplicate nodes by title
  similarity (uses `supersede` under the hood).
- `k7e compile <tag> [--dry-run]` — synthesize the active entries for a tag into
  a single `compiled` reference page (LLM).

See [cli.md](cli.md) for full command/flag reference.
