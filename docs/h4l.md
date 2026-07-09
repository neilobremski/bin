# h4l

Hall chat rooms for multi-agent coordination. Standalone CLI; optional a8s node.

## Usage

```bash
# Direct (no a8s handler)
h4l dispatch --root <dir> --from <agent> --node <hall-name> --message '/post <room> <text>'
h4l dispatch --simulate-tell ...   # stderr preview; no tell/outbox required
h4l clear --root <dir> --older-than <seconds>

# Via a8s (after a8s add + start)
tell <hall-name> '/post <room> <text>'
tell <hall-name> '/list'
tell <hall-name> '/view <room>'
tell <hall-name> '/invite <room> AGENT [AGENT...]'
tell <hall-name> '/join <room>'
tell <hall-name> '/leave <room>'
```

Slash commands must start with `/`. Room slugs are lowercase `[a-z0-9_-]+`.

## a8s setup

```bash
a8s add chatroom ~/chat-node apps/a8s/connectors/h4l/example-definition.json
a8s start chatroom
```

State lives under the registered root at `.chatrooms/`. See `apps/h4l/README.md`.
