# az-wi-comment

Add a Markdown-formatted comment to an Azure DevOps work item.

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) with an active session: `az login`
- `jq` and `curl`

## Usage

```bash
# Inline comment
az-wi-comment 5060 "Bug confirmed — missing KPIs not shown."

# Piped from a file
cat findings.md | az-wi-comment 5060

# With explicit org/project
az-wi-comment 5060 --org https://dev.azure.com/myorg --project myproj "comment"
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--org <url>` | `https://dev.azure.com/pay-i` | Azure DevOps organization URL |
| `--project <name>` | `pay-i` | Project name |

The comment text is the final positional argument, or piped via stdin.

## How It Works

The `az boards work-item update --discussion` flag escapes Markdown (turning `**bold**` into `\*\*bold\*\*`) and stores comments as HTML. This script bypasses the CLI and calls the REST API directly using a JSON PATCH request:

```json
[
  {"op": "add", "path": "/fields/System.History", "value": "**bold** and _italic_"},
  {"op": "add", "path": "/multilineFieldsFormat/System.History", "value": "Markdown"}
]
```

The `multilineFieldsFormat/System.History` operation tells Azure DevOps to store the comment as Markdown rather than HTML. This feature was added to the REST API in 2025 but is not yet supported by the `az boards` CLI.

## Output

Outputs the full work item JSON to stdout on success. Pipe to `jq` to extract fields:

```bash
az-wi-comment 5060 "Fixed in commit abc123" | jq '{id: .id, rev: .rev}'
```

## Tips

Post a multi-line comment with a heredoc:

```bash
az-wi-comment 5060 "$(cat <<'EOF'
**Confirmed** on localhost.

- Instance with all KPIs: shows all three
- Instance with missing KPIs: only shows the one that was reported

Agree this needs an N/A indicator.
EOF
)"
```
