---
name: az-pr-describe
description: Sets the description of an Azure DevOps Pull Request. Use when updating or setting PR descriptions with markdown content.
allowed-tools: Bash(az-pr-describe *)
---

# az-pr-describe

Set the description of an Azure DevOps Pull Request using markdown.

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) with an active session: `az login`
- `jq` and `curl`

## Usage

```bash
# Inline description
az-pr-describe 5719 "## Summary\nBug fixes and improvements."

# Piped from a file
cat description.md | az-pr-describe 5719

# With explicit org/project/repo
az-pr-describe 5719 --org https://dev.azure.com/myorg --project myproj --repo myrepo "description"
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--org <url>` | `https://dev.azure.com/pay-i` | Azure DevOps organization URL |
| `--project <name>` | `pay-i` | Project name |
| `--repo <name>` | `pay-i` | Repository name |

The description text is the final positional argument, or piped via stdin.

## Why Not `az repos pr update`?

The `az repos pr update --description` flag has trouble with multi-line markdown — it treats each shell argument as a separate line and can mangle special characters. This script bypasses the CLI and calls the REST API directly:

```http
PATCH /_apis/git/repositories/{repo}/pullrequests/{prId}?api-version=7.1

{"description": "markdown content here"}
```

## Output

Outputs a summary JSON object on success:

```json
{
  "pullRequestId": 5719,
  "title": "Staging FE: Value Improvements",
  "status": "active",
  "description": "- **Feature A** ..."
}
```

## Tips

Set a multi-line description with a heredoc:

```bash
az-pr-describe 5719 --repo payi-frontend "$(cat <<'EOF'
- **Value Policy Editor** is now functional on the Use Cases page
- **Deployments** page shows "Allocated" column

QA PRs:
----
- !5669
- !5681
EOF
)"
```

Pipe a file as the description:

```bash
cat release-notes.md | az-pr-describe 5719 --repo payi-frontend
```
