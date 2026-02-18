---
name: payi-ingest
description: Sends ingest/telemetry requests to the Pay-i API with xProxy headers. Use when posting usage data or telemetry to Pay-i.
allowed-tools: Bash(payi-ingest *)
---

# payi-ingest

Send ingest requests to the Pay-i API. Thin wrapper around `payi` targeting `post /api/v1/ingest`.

## Usage

```bash
payi-ingest <application> [--header value] [-param value] [json]
echo '{"category":"system.openai",...}' | payi-ingest <application>
```

Running with no arguments shows usage and lists available applications (via `payi`).

## Parameters

- `application` - Name of a config profile in `./.payi-ingest/` or `~/.payi-ingest/` (required)
- `--key value` - HTTP header (e.g., `--xProxy-UseCase-Name "name"`) — passed through to `payi`
- `-key value` - Query string parameter — passed through to `payi`
- `json` - JSON payload as the last positional argument, or piped via stdin

See `payi` for configuration setup and the full argument reference.

## Examples

### Simple ingest

```bash
payi-ingest my-app '{"category":"system.openai","resource":"gpt-4o","units":{"text":{"input":100,"output":50}}}'
```

### With use case and user context

```bash
payi-ingest my-app \
  --xProxy-UseCase-Name "ticket-classification" \
  --xProxy-UseCase-ID "tc-001" \
  --xProxy-User-ID "agent-alice" \
  '{"category":"system.openai","resource":"gpt-4o-mini","units":{"text":{"input":200,"output":50}},"end_to_end_latency_ms":320}'
```

### Pipe JSON from a file

```bash
cat payload.json | payi-ingest my-app --xProxy-UseCase-Name "summarize"
```

## JSON Payload Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `category` | string | yes | Provider category with `system.` prefix (e.g., `system.openai`) |
| `resource` | string | no | Model name (e.g., `gpt-4o`, `claude-sonnet-4-5-20250929`) |
| `units` | object | yes | Token counts, keyed by type (e.g., `{"text": {"input": N, "output": N}}`) |
| `event_timestamp` | string | no | ISO 8601 timestamp (defaults to now; future up to 5 min) |
| `end_to_end_latency_ms` | int | no | Total request latency in milliseconds |
| `time_to_first_token_ms` | int | no | Time to first token in milliseconds |
| `http_status_code` | int | no | HTTP status code of the upstream request |
| `properties` | object | no | Arbitrary key-value metadata |
| `use_case_properties` | object | no | Metadata persisted across requests in the same use case instance |

### Unit types

- `text` — standard text tokens
- `text_cache_read` — cached tokens
- `vision` — image tokens
