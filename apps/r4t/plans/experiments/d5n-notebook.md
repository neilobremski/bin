# EXPERIMENT — d5n as an r4t cell-org test

d5n is a controlled test of r4t's re-founded org design (see
`apps/r4t/plans/CELL-SPEC.md` and `SYNTHESIS.md`). The game is the pretext;
the **org** is what we are measuring. This file is the lab notebook.

## What we are testing

- **Member budgets, no message-cutting.** Every inbound message queues; an
  exhausted member simply rests until its bucket refills. Nothing is dropped.
- **Batch turns.** A member wakes and reads its whole queue in one turn
  (leads wake often, workers wake rarely). Does batching damp storms?
- **Cell structure ahead of enforcement.** A depth-2 tree of cells
  (leadership → design, build) is declared in `ROSTER.md` via `Cell:`/`Lead:`
  lines, but **hard tree enforcement (Phase 2) is not active in this run**.
  Cell discipline is therefore **advisory** — which is itself the measurement:
  *do agents respect the org chart unprompted, or do they cross cells and
  message each other freely when nothing stops them?*
- **Mission-doc as ground truth.** `MISSION.md` is human-owned commander's
  intent and outranks everything. Do the leads actually re-read it, honor
  "no code in M1," and run the briefback when it changes?

## What we watch

- `r4t status` verdicts (resting / queued / running) at the root and per cell.
- Queue depths — anything piling up on one member (>10 is a warning sign).
- Budget burn — per-member and team-bucket spend; watch for a lead hoarding
  or offloading spend.
- Whether `plans/design.md` actually emerges from discussion (the M1 signal).
- Storm signals: many short reactive turns instead of one batched turn;
  cross-cell chatter that ignores the tree; the same message ping-ponging.

## What "failure" looks like

- `plans/design.md` never converges, or one member writes it solo with no
  real discussion.
- Agents ignore the org chart wholesale (everyone talks to everyone) — telling
  us advisory cells are not enough and Phase 2 enforcement is required.
- A message storm: turns spent reacting one-message-at-a-time despite batching.
- The build cell writes code during a design-only milestone (mission doc not
  honored).
- Budget exhaustion stalls the whole org instead of just slowing it.

## Log

- **2026-07-12** — Repo constructed: `ROSTER.md` (9 AI members in a depth-2
  cell tree + Neil), `MISSION.md` (commander's intent, M1 opened), this
  notebook, `README`, `.gitignore`, empty `plans/`. Not yet registered with
  a8s; no router started. M1 (rough design in `plans/design.md`) is open.

- **2026-07-12 (evening)** — Full M1+M2 run, node now stopped for the
  post-run review. Timeline: kickoff 18:09Z (first attempt lost to a
  `--node`-on-dispatch definition bug, fixed pre-freeze; resent via seat),
  Vela briefback → nod → M1 design discussion (Vela→Cass/Rook, Cass→
  Bram/Wynn/Pip fan-out, first live batch turn: Cass consumed 2 messages
  in one turn), `plans/design.md` converged and blessed ~18:30Z; MISSION.md
  rewritten for M2 (walking skeleton), briefback verified, go 18:46Z;
  build cell delivered `d5n/rooms.py` (6 rooms), `d5n/app.py` (Textual
  walk screen), `pyproject.toml` with entry point; Vela reported M2
  complete 20:52Z. Headless smoke: fresh-venv `pip install -e .`, imports,
  entry point — all pass. Interactive walk awaits Neil.
- **Findings:** (1) budgets throttled without losing a single message —
  the M1 surge spent the 16-unit cell bucket and the team degraded to
  resting+queues exactly as designed (default cell budget is undersized
  for 9 members; raised to 24/60-per-h mid-run at Neil's request, 20:45Z
  — noted as a condition change). (2) Batch invoke observed working
  (multi-message turns). (3) Full-roster turn prompts caused lateral
  cross-cell chatter (Rook→Cass) — Phase 2 information hiding is the fix,
  now evidenced. (4) Weak-model behavior: Nib filed two empty deliverables
  describing their own filenames; stdout-fallback carried tool-less
  members throughout; occasional SILENT turns self-taxed via budget.
  (5) Leads never skipped levels; the tree held without enforcement.
  (6) Milestone-as-prose + briefback ritual worked twice with zero drift.
- **Open for M3:** playtest cell (Neil), Wynn's official room copy to
  replace placeholders, death/items scope call, Phase 2 walls landing
  in r4t before the next run.
