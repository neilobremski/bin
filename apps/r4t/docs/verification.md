# The verification round

An agent cannot be the judge of its own deliverable, so the judge is
machinery it cannot see into. Design history and the incident that drove it:
[../plans/history/VERIFY-SPEC.md](../plans/history/VERIFY-SPEC.md).

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

## The post-hoc judge

`r4t check` and the doorbell gate act on a live run; the judge is the third
leg — it grades a finished run. `r4t judge <node> --rig <rig>` reads a
completed run's recorded transcripts and scores them against the MAST
multi-agent failure taxonomy ("Why Do Multi-Agent LLM Systems Fail?",
arXiv:2503.13657), plus one r4t extension mode for mutual-wait deadlock, a
failure MAST has no single mode for.

It is post-hoc and out-of-band by design: a graded org changes behavior, and
an agent that could read its own grade would learn to game it. Reports land
under the team dir's `judge/` — a surface no roster agent ever reads — never
inside the workplace repo. Pass `--json` instead of the sectioned panel to
derive an experiment-ledger column.
