# h4l — Hall chat rooms (v0)

Branch: `plan/a8s-chatrooms`.

Standalone app: `apps/h4l/`, shim `~/bin/h4l`. a8s wiring via
`apps/a8s/connectors/h4l/example-definition.json` only.

## Deferred

- Registry prefix routing ([#148](https://github.com/neilobremski/bin/issues/148))
- tell auto-sync ([#149](https://github.com/neilobremski/bin/issues/149))
- @mentions, namespaces, shared-repo sender spike

## Usage

```bash
# Standalone
h4l dispatch --root ~/chat-node --from ALICE --node HALL --message '/post war hi'

# a8s
a8s add chatroom ~/chat-node apps/a8s/connectors/h4l/example-definition.json
tell chatroom '/post war hi'
```

## Slash commands

`/post`, `/join`, `/leave`, `/invite`, `/list`, `/view`, `/members` — must start with `/`.

State: `<root>/.chatrooms/rooms/<slug>/`.

## v0 decisions

| Topic | Decision |
|-------|----------|
| `/list` | All rooms + all members |
| `/post` | Auto-create room; auto-join poster |
| Poster ACK | stdout + `tell` to sender |
| Errors | `tell` to sender |
| Permissions | Open / trusted |
| Slugs | `[a-z0-9_-]+`, lowercase display |
| Notify | Truncated body (~1k) + footer with live node name and room slug |
| Maintenance | `h4l clear --older-than` / `--all` via idle.invoke when configured |

## Status

- [x] Phase 1 CLI + tests
- [x] example-definition.json + README + docs/h4l.md
- [ ] Phase 2 end-to-end a8s attached_loop test (optional)
