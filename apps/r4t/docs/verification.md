# The verification round

An agent cannot be the judge of its own deliverable, so the judge is
machinery it cannot see into. Design history and the incident that drove it:
[../plans/VERIFY-SPEC.md](../plans/VERIFY-SPEC.md).

## `r4t check`

`r4t check <node>` sweeps the tracked files in the node's workplace for
forbidden patterns and prints exactly `check passed` or
`check failed: N finding(s)` — nothing else.

The findings (which file, which line, which pattern) go to stderr, the
surface only the human reads; the agent gets an opaque verdict it cannot
game.

## Checklists

Patterns are one Python regex per line in
`~/.config/r4t/checklists/default.txt` (every node) and
`~/.config/r4t/checklists/<node>.txt` (per-node additions); `#` comments and
blank lines are ignored, and no checklist at all is a pass.

These files live outside every repo, uncommitted, because they may carry
private strings like a codename (e.g. `secret-codename`) or a name.

## Gating the doorbell

Set `doorbell_check` in `r4t-org.json` (see [org.md](org.md#org-settings)) to
run any command — the sweep or a test suite — before the org may ring an
absent human, and a failing check parks the message without ringing rather
than losing it.
