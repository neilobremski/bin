---
name: az-wi-comment
description: Posts Markdown comments with inline images to Azure DevOps work items. Use when adding comments or screenshots to ADO work items.
allowed-tools: Bash(az-wi-comment *)
---

# az-wi-comment

Add a Markdown-formatted comment with optional inline images to an Azure DevOps work item.

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
| `--image <path>` | | Upload image and append to comment (repeatable) |

The comment text is the final positional argument, or piped via stdin.

## Inline Images

There are two ways to include images in comments:

### 1. Markdown image syntax with local paths (auto-uploaded)

Any `![alt](path)` in the comment text where `path` is an existing local file will be automatically uploaded to Azure DevOps and the path replaced with the attachment URL:

```bash
az-wi-comment 5060 '![Screenshot of bug](./screenshots/bug.png)

The samples pane is missing KPI values.'
```

The script detects local file paths (anything that isn't `http://` or `https://`), uploads them via the [Attachments API](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/attachments/create?view=azure-devops-rest-7.1), and replaces the path in-place. URLs are left untouched.

### 2. `--image` flag (appended to end)

Use `--image` to upload an image and append it to the end of the comment. Can be repeated for multiple images:

```bash
az-wi-comment 5060 --image before.png --image after.png "Fixed the layout bug."
```

Each image is appended as `![filename](url)` after the comment text.

### How image upload works

1. The image binary is uploaded via `POST /_apis/wit/attachments?fileName=name.png` with `Content-Type: application/octet-stream`
2. Azure DevOps returns a permanent attachment URL containing a GUID
3. The URL is embedded in the comment using standard markdown image syntax: `![alt](url)`
4. The image renders inline in the work item discussion — no need to formally attach it to the work item

## How It Works

The `az boards work-item update --discussion` flag escapes Markdown (turning `**bold**` into `\*\*bold\*\*`) and stores comments as HTML. This script bypasses the CLI and calls the REST API directly using a JSON PATCH request:

```json
[
  {"op": "add", "path": "/fields/System.History", "value": "**bold** and _italic_"},
  {"op": "add", "path": "/multilineFieldsFormat/System.History", "value": "Markdown"}
]
```

The `multilineFieldsFormat/System.History` operation tells Azure DevOps to store the comment as Markdown rather than HTML. This feature was [added to the REST API in 2025](https://github.com/Azure/azure-devops-cli-extension/issues/1473) but is not yet supported by the `az boards` CLI.

## Output

Outputs the full work item JSON to stdout on success. Pipe to `jq` to extract fields:

```bash
az-wi-comment 5060 "Fixed in commit abc123" | jq '{id: .id, rev: .rev}'
```

## Tips

Post a multi-line comment with a heredoc and inline screenshot:

```bash
az-wi-comment 5060 "$(cat <<'EOF'
**Confirmed** on localhost.

- Instance with all KPIs: shows all three
- Instance with missing KPIs: only shows the one that was reported

![Samples pane](./screenshots/missing-kpis.png)

Agree this needs an N/A indicator.
EOF
)"
```

## References

- [Attachments - Create REST API](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/attachments/create?view=azure-devops-rest-7.1) — upload images
- [Markdown Support Arrives for Work Items](https://devblogs.microsoft.com/devops/markdown-support-arrives-for-work-items/) — multilineFieldsFormat
- [CLI Markdown support request (GitHub #1473)](https://github.com/Azure/azure-devops-cli-extension/issues/1473) — REST API workaround
