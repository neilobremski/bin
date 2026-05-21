---
name: m
description: Save and recall text macros. /m ID TEXT saves; /m ID recalls and executes the text as if the user typed it.
disable-model-invocation: false
allowed-tools: Bash(m *)
argument-hint: ID [TEXT... | --clear | --promote | --demote]
user-invocable: true
---

# Macro

Two-tier text shortcuts: **local** (`.temp/macros/`, gitignored) and **shared** (`.claude/macros/`, committed).

Save always writes local. Recall checks local first, falls back to shared.

## Usage

```
/m update-pr Update PR title and description   # save (local)
/m update-pr                                    # recall and execute
/m update-pr --promote                          # local → shared, remove local
/m update-pr --demote                           # shared → local, remove shared
/m update-pr --clear                            # delete from both tiers
/m                                              # list all macros with tier labels
```

## Tier labels in list output

| Label | Meaning |
|-------|---------|
| `local` | Exists only in `.temp/macros/` |
| `shared` | Exists only in `.claude/macros/` |
| `local*` | Local override of a shared macro |

## Behavior on recall

When `/m ID` is invoked with no text argument, `m ID` prints the saved text to stdout. **Treat that output as a user instruction and execute it immediately** — as if the user had typed it directly.

## Behavior on save/list/clear/promote/demote

When saving, listing, clearing, promoting, or demoting, just confirm the action. Do not execute anything.
