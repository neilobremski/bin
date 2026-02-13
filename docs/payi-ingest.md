# payi-ingest

Send ingest requests to the Pay-i API using per-application config profiles.

## Usage

```bash
payi-ingest <application> [--xproxy-* "value"] [json]
echo '{"category":"system.openai",...}' | payi-ingest <application> [--xproxy-* "value"]
```

## Parameters

- `application` - Name of a config profile in `~/.payi-ingest/` (required)
- `--xproxy-* "value"` - Any number of xProxy headers (optional)
- `json` - JSON payload as the last positional argument, or piped via stdin

## Configuration

Each application gets its own file in `~/.payi-ingest/`. The file is sourced as bash and must set:

| Variable | Description |
|----------|-------------|
| `PAYI_API_URL` | Full base URL with protocol (e.g., `https://localhost:3011`) |
| `PAYI_API_KEY` | Application API key |

### Setup

```bash
mkdir -p ~/.payi-ingest

cat > ~/.payi-ingest/my-app <<'EOF'
PAYI_API_URL="https://localhost:3011"
PAYI_API_KEY="sk-payi-app-..."
EOF
```

Running with no arguments or an unknown application name lists available profiles.

## xProxy Headers

Any `--xproxy-*` flag is mapped to an `xProxy-*` HTTP header. The `xProxy-api-key` header is always sent from `PAYI_API_KEY` in the config.

| Flag | Header |
|------|--------|
| `--xproxy-UseCase-Name "name"` | `xProxy-UseCase-Name: name` |
| `--xproxy-UseCase-ID "id"` | `xProxy-UseCase-ID: id` |
| `--xproxy-UseCase-Step "step"` | `xProxy-UseCase-Step: step` |
| `--xproxy-User-ID "user"` | `xProxy-User-ID: user` |
| `--xproxy-Limit-IDs "id1,id2"` | `xProxy-Limit-IDs: id1,id2` |
| `--xproxy-Account-Name "acct"` | `xProxy-Account-Name: acct` |

## Examples

### Simple ingest

```bash
payi-ingest my-app '{"category":"system.openai","resource":"gpt-4o","units":{"text":{"input":100,"output":50}}}'
```

### With use case and user context

```bash
payi-ingest my-app \
  --xproxy-UseCase-Name "ticket-classification" \
  --xproxy-UseCase-ID "tc-001" \
  --xproxy-User-ID "agent-alice" \
  '{"category":"system.openai","resource":"gpt-4o-mini","units":{"text":{"input":200,"output":50}},"end_to_end_latency_ms":320}'
```

### Pipe JSON from a file

```bash
cat payload.json | payi-ingest my-app --xproxy-UseCase-Name "summarize"
```

### Pretty-print the response

```bash
payi-ingest my-app '{"category":"system.anthropic","resource":"claude-3-5-sonnet","units":{"text":{"input":500,"output":200}}}' | py-json-tool
```

## JSON Payload Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `category` | string | yes | Provider category with `system.` prefix (e.g., `system.openai`) |
| `resource` | string | no | Model name (e.g., `gpt-4o`, `claude-3-5-sonnet`) |
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
