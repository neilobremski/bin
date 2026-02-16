---
name: az-pr-dump
description: Dumps Azure DevOps PRs to JSON (metadata, threads, iterations, work items). Use when analyzing PR comments, reviewers, or iteration history.
allowed-tools: Bash(az-pr-dump *)
---

# az-pr-dump

Dump an Azure DevOps Pull Request to structured JSON, including metadata, iterations, comment threads, and linked work items.

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) with the DevOps extension:
  ```bash
  az extension add --name azure-devops
  ```
- Authenticated session: `az login`

## Usage

```bash
# By PR ID (defaults to pay-i org/project/repo)
az-pr-dump 5702

# By full URL
az-pr-dump https://dev.azure.com/pay-i/pay-i/_git/pay-i/pullrequest/5702

# With explicit org/project/repo
az-pr-dump 5702 --org https://dev.azure.com/myorg --project myproj --repo myrepo
```

## Output

Outputs a single JSON object to stdout with four top-level keys:

```json
{
  "pullRequest": { ... },
  "iterations": [ ... ],
  "threads": [ ... ],
  "workItems": [ ... ]
}
```

### `pullRequest`

| Field | Description |
|-------|-------------|
| `id` | PR number |
| `title` | PR title |
| `description` | PR description body |
| `status` | `active`, `completed`, `abandoned` |
| `createdBy` | Author display name |
| `creationDate` | ISO 8601 timestamp |
| `sourceRefName` | Source branch ref |
| `targetRefName` | Target branch ref |
| `mergeStatus` | `succeeded`, `conflicts`, etc. |
| `reviewers` | Array of `{ displayName, vote, isRequired }` |
| `labels` | Array of label name strings |

### `iterations`

Each iteration represents a push/update to the PR branch:

| Field | Description |
|-------|-------------|
| `id` | Iteration number |
| `description` | Iteration description (if any) |
| `createdDate` | ISO 8601 timestamp |
| `sourceRefCommit` | Commit SHA for this iteration |

### `threads`

Each thread represents a comment thread (inline or general):

| Field | Description |
|-------|-------------|
| `id` | Thread ID |
| `status` | `active`, `fixed`, `closed`, `wontFix`, or `null` |
| `isDeleted` | Whether the thread was deleted |
| `fileContext` | File path and line range (null for general comments) |
| `comments` | Array of `{ author, content, commentType, publishedDate }` |

`fileContext` includes `filePath`, `rightFileStart`, `rightFileEnd` (and `left` equivalents for diff context).

### `workItems`

Each work item linked to the PR:

| Field | Description |
|-------|-------------|
| `id` | Work item ID |
| `url` | REST API URL for the work item |

## Tips

Filter to active threads only with jq:

```bash
az-pr-dump 5702 | jq '.threads[] | select(.status == "active")'
```

Get just the file-level active comments with content:

```bash
az-pr-dump 5702 | jq '
  .threads[]
  | select(.status == "active" and .fileContext != null)
  | {
      file: .fileContext.filePath,
      line: .fileContext.rightFileStart.line,
      comments: [.comments[] | {author, content}]
    }
'
```
