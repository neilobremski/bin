# n5a — org shape as the only variable

n5a (numeronym of "novella," after d5n's "dungeon") is an A/B test of r4t org
*structure*, not r4t org *content*. Two orgs receive the identical seed
mission — write a novella — and the identical roster of people (same names,
same rig tiers, same individual skills) but are wired into different shapes.
Each runs against its own clone of the same, otherwise-empty repo. Neil
judges by reading both manuscripts. See `apps/r4t/plans/CELL-SPEC.md` for the
cell doctrine this is testing and `apps/r4t/plans/research/ORG-LESSONS.md`
for the literature it's checking against reality.

A novella was chosen over another game because verification is fast and
cheap and a human is the right judge: continuity, a single authorial voice,
characters who stay themselves, a plot that resolves. You don't need
instrumentation to notice a book falls apart — you read it.

## Hypothesis

**Cells beat flat, even at small scale, once a task needs specialized
judgment that must recombine into one artifact.** ORG-LESSONS' cell-size and
depth research (Hackman ≈4.6, Amazon two-pizza, Microsoft span-of-control
defect data) is r4t's core structural bet; its "we looked, rejected" section
dismisses flat, no-manager structures for r4t generally on the strength of
that literature, without an r4t-native test. n5a runs that rejected
alternative for real, on a task small enough that flat *might* win: a
10-person org is well inside the span a single lead can nominally track
(hard cap 10), so if cells still win here, the case for r4t's tree is much
stronger than literature review alone; if flat wins or ties, the tree adds
overhead this task didn't need.

Org A (`org-a/`) is the d5n-proven hierarchy: a top lead delegating to three
cell leads (writing, continuity, reader), each running 2-4 direct reports —
depth 2, matching ORG-LESSONS' default. Org B (`org-b/`) is a flat newsroom:
the same top lead with all nine other members as direct reports, no cells,
no middle layer. Same ten AI seats, same rig mix (4 specialist / 5 simple /
1 dumb) in both — the only variable is who reports to whom.

## Setup

- `apps/r4t/experiments/n5a/MISSION.md` — the seed mission, identical copy
  into both orgs.
- `apps/r4t/experiments/n5a/org-a/ROSTER.md`, `org-b/ROSTER.md` — the two
  structures, seeded from the same MISSION.md.
- Each org gets its own clone of the same novella repo (empty except for
  MISSION.md/ROSTER.md at genesis) so the two runs never share state,
  commits, or context. Two clones, not one shared repo, is what makes "diff
  the manuscripts" a clean comparison.
- Both orgs run **in parallel**, via the portable MISSION/ROSTER feature
  (org directories that point at a repo rather than living inside it) —
  the mechanism landing in issue #180. The org-dir → repo pointer config
  itself is #180's surface, not this experiment's:

  ```
  # PLACEHOLDER — org-dir config (repo pointer), mechanics land in #180.
  # org-a/<config file>: repo: <path to org A's clone>
  # org-b/<config file>: repo: <path to org B's clone>
  ```

- Register each org as its own a8s node; nothing about the two shares a
  process, a queue, or a budget bucket.

## What is measured

**The manuscript is the primary measurement.** Neil reads both completed
novellas and judges:
- **Continuity** — do facts, timelines, and objects hold across chapters.
- **Voice** — does the whole book read as one author, not a seam of hands.
- **Character consistency** — do people stay themselves scene to scene.
- **Plot resolution** — does the ending pay off what came before, nothing
  dangling that wasn't meant to be.

**Cost is the secondary measurement**, pulled from `r4t logs` and `r4t
status` for each org over the run: turns consumed per org and per member,
wall-clock time to each milestone, and budget exhaustions (how often a
member or the team bucket went to resting, and for how long). A structure
that produces a better book at meaningfully higher cost is a different
finding than one that produces a better book for free.

## Hawthorne rule

**Participants must not know they are in an experiment.** MISSION.md is
identical in content and tone to a real seed mission — it says nothing
about A/B, other orgs, or being compared. Neither ROSTER.md mentions the
other org, describes itself as "flat" or "hierarchical" as an experimental
condition, or hints that the org chart is a variable under test. Each org's
files read exactly as d5n's did to d5n's own roster: a real project brief
for a real team, full stop. Anything that leaks the meta-frame would let a
lead perform for the wrong audience.

## Blessing gates

Mirrors d5n's M1/M2/M3 rhythm, run identically and independently in both
orgs:

- **M1 — premise, cast, outline.** Reached through real discussion inside
  each org, written down, and blessed by Neil before either org writes a
  single chapter. Two separate blessings — the orgs may converge on
  different stories; that's expected and fine.
- **M2 — first act, voice locked.** A partial draft (roughly the first
  third) exists, in each org's own idiom, proving the voice holds before the
  whole book is committed to it. Blessed or sent back for a voice reset.
- **M3 — complete manuscript.** Full novella, chaptered, in each repo.
  Blessed once Neil has read it against the four manuscript criteria above.

Only after both M3s land does the comparison happen — reading, then the
cost pull from `r4t logs`. See `notebook.md` for the running log of the
actual run.
