---
name: payi
description: General-purpose Pay-i API client with per-app config profiles. Use when querying or interacting with Pay-i APIs (categories, resources, KPIs, use cases).
allowed-tools: Bash(payi *)
---

# payi

General-purpose Pay-i API client using per-application config profiles.

## Usage

```bash
payi                                           # list configured applications
payi <application> [verb] <path> [options] [json]
echo '{"score":0.95}' | payi <application> put /api/v1/some/path
```

## Parameters

- `application` - Name of a config profile in `~/.payi-ingest/` (required)
- `verb` - HTTP method: get, post, put, patch, delete (optional; defaults to GET without body, POST with body)
- `path` - API path starting with `/` (required)
- `--key value` - HTTP header (`key: value`) — repeatable
- `-key value` - Query string parameter (`?key=value`) — repeatable
- `json` - JSON payload as last positional argument, or piped via stdin

## Configuration

Each application gets a file in `~/.payi-ingest/`. If the first line starts with `#`, it is shown as a description when listing apps.

```bash
# ~/.payi-ingest/my-app
# Production Pay-i instance
PAYI_API_URL="https://localhost:3011"
PAYI_API_KEY="sk-payi-app-..."
```

Running with no arguments lists available applications:

```
$ payi
Available applications:
  my-app         # Production Pay-i instance
  staging        # Staging environment
  local
```

## Common API calls

### Ingest

```bash
# single event ingest
payi APP post /api/v1/ingest '{"category":"system.openai","resource":"gpt-4o","units":{"text":{"input":100,"output":50}}}'
# with use case header
payi APP post /api/v1/ingest --xProxy-UseCase-Name my-uc '{"category":"system.anthropic","resource":"claude-sonnet-4-5-20250929","units":{"text":{"input":200}}}'
# bulk ingest
payi APP post /api/v1/ingest/bulk '[{...},{...}]'
# pipe from stdin
echo "$json" | payi APP post /api/v1/ingest
```

### Categories & resources

```bash
payi APP /api/v1/categories | jq '.items[].category'               # list all categories
payi APP /api/v1/categories/system.openai/resources -limit 100     # resources in a category
payi APP /api/v1/categories/system.openai/resources/gpt-4o         # resource version list
payi APP /api/v1/categories/system.openai/resources/gpt-4o/RES_ID  # resource version detail
# create custom resource
payi APP post /api/v1/categories/org.custom/resources/my-model '{...}'
# delete
payi APP delete /api/v1/categories/org.custom                               # category + all resources
payi APP delete /api/v1/categories/org.custom/resources/my-model             # all versions of resource
payi APP delete /api/v1/categories/org.custom/resources/my-model/RES_ID      # specific version
```

### Provisioned resources

```bash
payi APP post /api/v1/categories/system.openai/provisioned/my-deployment '{"resource":"gpt-4o",...}'
payi APP put /api/v1/categories/system.openai/provisioned/my-deployment '{...}'
payi APP delete /api/v1/categories/system.openai/provisioned/my-deployment
payi APP delete /api/v1/categories/system.openai/provisioned/my-deployment/RES_ID
```

### Limits

```bash
payi APP /api/v1/limits | jq '.items[]'          # list all limits
payi APP /api/v1/limits/LIMIT_ID                 # limit details
payi APP post /api/v1/limits '{"scope":"application","limit_type":"Block","threshold":10.00,"max":50.00}'
payi APP put /api/v1/limits/LIMIT_ID '{"threshold":20.00}'
payi APP delete /api/v1/limits/LIMIT_ID
payi APP put /api/v1/limits/LIMIT_ID/properties '{"key":"value"}'
payi APP post /api/v1/limits/LIMIT_ID/reset      # reset spend tracking
```

### Use case definitions

```bash
payi APP /api/v1/use_cases/definitions | jq '.items[].use_case_name' # list all
payi APP /api/v1/use_cases/definitions/my-uc                         # details
payi APP post /api/v1/use_cases/definitions '{"use_case_name":"my-uc"}'
payi APP put /api/v1/use_cases/definitions/my-uc '{...}'
payi APP delete /api/v1/use_cases/definitions/my-uc
payi APP post /api/v1/use_cases/definitions/my-uc/increment_version
# default limit config for new instances
payi APP post /api/v1/use_cases/definitions/my-uc/limit_config '{...}'
payi APP delete /api/v1/use_cases/definitions/my-uc/limit_config
```

### Use case instances

```bash
payi APP post /api/v1/use_cases/instances/my-uc '{"use_case_id":"inst-001"}'
payi APP /api/v1/use_cases/instances/my-uc/inst-001                  # instance details
payi APP put /api/v1/use_cases/instances/my-uc/inst-001/properties '{"user":"alice"}'
payi APP /api/v1/use_cases/instances/my-uc/inst-001/value            # value score
payi APP delete /api/v1/use_cases/instances/my-uc/inst-001
```

### KPIs

```bash
# definitions (on use case type)
payi APP /api/v1/use_cases/definitions/my-uc/kpis                    # list KPIs
payi APP /api/v1/use_cases/definitions/my-uc/kpis/accuracy           # KPI detail
payi APP post /api/v1/use_cases/definitions/my-uc/kpis '{"kpi_name":"accuracy","goal":0.95}'
payi APP put /api/v1/use_cases/definitions/my-uc/kpis/accuracy '{"goal":0.99}'
payi APP delete /api/v1/use_cases/definitions/my-uc/kpis/accuracy
# scores (on use case instance)
payi APP put /api/v1/use_cases/instances/my-uc/inst-001/kpis/accuracy '{"score":0.97}'
payi APP /api/v1/use_cases/instances/my-uc/inst-001/kpis             # all scores
```

### Requests

```bash
payi APP /api/v1/requests/REQ_ID/result                              # request result
payi APP put /api/v1/requests/REQ_ID/properties '{"key":"value"}'
# by provider response ID
payi APP /api/v1/requests/provider/system.openai/PROVIDER_ID/result
payi APP put /api/v1/requests/provider/system.openai/PROVIDER_ID/properties '{"key":"value"}'
```

### Discounts

```bash
payi APP /api/v1/discounts | jq '.items[]'       # list all
payi APP /api/v1/discounts/DISC_ID               # detail
payi APP post /api/v1/discounts '{"category":"system.openai","discount_percent":10}'
payi APP put /api/v1/discounts/DISC_ID '{"discount_percent":15}'
payi APP delete /api/v1/discounts/DISC_ID
```

### Reservations

```bash
payi APP /api/v1/reservations                                        # list all
payi APP /api/v1/reservations/RES_ID                                 # instances
payi APP /api/v1/reservations/RES_ID/INST_ID                         # instance detail
payi APP /api/v1/reservations/RES_ID/resources                       # deployed resources
payi APP post /api/v1/reservations '{...}'
payi APP put /api/v1/reservations/RES_ID '{...}'
payi APP put /api/v1/reservations/RES_ID/INST_ID '{...}'
payi APP delete /api/v1/reservations/RES_ID
payi APP delete /api/v1/reservations/RES_ID/INST_ID
```

### Common headers (via --)

```bash
--xProxy-UseCase-Name "name"       # associate request with use case
--xProxy-UseCase-ID "id"           # use case instance ID
--xProxy-UseCase-Step "step"       # step within use case
--xProxy-User-ID "user"            # end user attribution
--xProxy-Limit-IDs "id1,id2"      # specific limits to check
--xProxy-Account-Name "acct"       # account attribution
```
