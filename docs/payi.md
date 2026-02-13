# payi

General-purpose Pay-i API client using per-application config profiles.

## Usage

```bash
payi <application> [verb] <path> [--key value ...] [json]
echo '{"score":0.95}' | payi <application> put /api/v1/some/path
```

## Parameters

- `application` - Name of a config profile in `~/.payi-ingest/` (required)
- `verb` - HTTP method: get, post, put, patch, delete (optional; defaults to GET without body, POST with body)
- `path` - API path starting with `/` (required)
- `--key value` - Query string parameters (optional, repeatable)
- `json` - JSON payload as last positional argument, or piped via stdin

## Configuration

Shares config profiles with `payi-ingest`. Each application gets a file in `~/.payi-ingest/`:

```bash
# ~/.payi-ingest/my-app
PAYI_API_URL="https://localhost:3011"
PAYI_API_KEY="sk-payi-app-..."
```

## Examples

### List categories

```bash
payi my-app /api/v1/categories | jq '.items[].category'
```

### List resources for a category

```bash
payi my-app /api/v1/categories/system.anthropic/resources --limit 100 | jq -r '.items[].resource'
```

### Set a KPI score

```bash
payi my-app put /api/v1/use_cases/instances/my-uc/inst-001/kpis/accuracy '{"score":0.95}'
```

### Pipe JSON body

```bash
echo '{"score":4}' | payi my-app put /api/v1/use_cases/instances/my-uc/inst-001/kpis/satisfaction
```
