# n5a — retrospective

*Run 2026-07-13, shut down the same night ~22:00 PDT. Retro written
2026-07-14. This is the seed/design repo's record; nothing here lived in
either novella repo (see [README.md](README.md)'s Hawthorne rule) and both
teams are shut down. Written honestly and completely.*

Read this against the [README.md](README.md) (hypothesis and setup), the
[notebook.md](notebook.md) (the running log), and the night's chapter of the
project chronicle, [03-the-night-shift](../../story/03-the-night-shift.txt).

## Hypothesis and setup

The one variable was org **shape**. Two orgs, identical in every other
respect — same ten-member roster (same names, same rig tiers, same
individual skills), the same seed [MISSION.md](MISSION.md) word for word
(write a novella, 15,000–20,000 words, readable in one sitting), each
against its own clone of the same otherwise-empty repo, each registered as
its own isolated a8s node.

- **quill** (org A) — the d5n-proven depth-2 tree. Rowan leads at the top;
  three cell leads under her (Odile on writing, Sorrel on continuity, Priya
  on the reader cell), each with direct reports.
- **vellum** (org B) — a flat newsroom. Rowan again, but with all nine
  other members reporting to her directly. No cells, no middle layer.

The bet under test (from [README.md](README.md)): **cells beat flat, even
at small scale, once a task needs specialized judgment that must recombine
into one artifact.** A ten-person org is inside the span one lead can
nominally track (hard cap 10), so this was the task small enough that flat
*might* win — if cells still won here, the case for r4t's tree would be much
stronger than literature review alone; if flat won or tied, the tree adds
overhead this task didn't need.

Neither team knew it was in an experiment (the Hawthorne rule). Neither knew
the other existed. Both were run under a proxy seat while Neil was away; the
proxy watched and nudged but did not bless — only Neil blesses.

## Timeline

Both repos share a genesis clone (initial commit `af0bf9f`, 15:23 PDT).

- **15:23** — both orgs kicked off from the same genesis.
- **15:35** — vellum's lead writes a MISSION.md into its repo and commits it
  (a *shadow* copy — see Finding 1). quill's stamp bug put it ~1h behind
  from here.
- **16:22–16:30** — vellum drafts and restructures its M1 package; commits
  show a `refactor(a8s):` prefix bleeding in from the surrounding project's
  conventions.
- **16:46** — quill independently commits its own shadow MISSION.md (42
  lines).
- **~17:00** — vellum M1 blessed by the proxy seat ("Early Warning System").
  vellum had *self-certified* the blessing first (Finding 2).
- **18:13–18:43** — vellum drafts all sixteen chapters and does an early
  prose pass; manuscript reaches ~44k words.
- **~19:00** — quill discovered permanently idle: seeds circulated, every
  thread closed, mission unmet, nothing to reopen it (Finding 3).
- **19:05–19:10** — after a seat nudge, quill's Odile commits three premise
  seeds, picks one, and drafts an M1 document ("The Last Translation").
- **~22:00** — Neil shuts both orgs down (quota; "still hasn't converged a
  simple story"). State left intact.

## What each org produced

**vellum (flat newsroom) — a complete story, far over budget.**
Final state, main @ `17513a5`: **16 chapters, 43,872 words, 248,514
characters (~248KB)** in `manuscript/`. The mission cap was 15,000–20,000
words. That is **~2.4x over in words** and **more than 2x** the ~110KB a
20k-word novella would occupy on disk. The story — "Early Warning System" —
has a genuine premise: civil-defense sirens that fire not for storms but for
personal, private catastrophes, moments before they happen; an emergency
manager, Evelyn Cross, carrying guilt from a 2011 tornado. Complete arc,
sixteen chapters, drafted in roughly a day. A cut pass was directed (trim
toward ~18k, close two voice flags, add a continuity log) but **never
landed** — the cut branch `feat/vellum-cut-pass-ch3-4-9-10` stalled with the
work uncommitted. Neil's framing the next morning: the novella "became over
200K — way way way beyond parameters."

**quill (depth-2 tree) — no story at all.**
Final state, main @ `83e3076`: 5 commits, `manuscript/` empty except its
`.gitkeep`. What exists is M1 material only: three premise seeds ("The Last
Translation," "The Cartographer's Daughter," "Seven Winters at the
Orchard"), a pick, and a 1,787-word M1 draft — all committed *after* the
seat nudge that woke the org. M1 was never blessed. Zero prose chapters.

The scoreboard, stated plainly: the fast team with far too many words, the
careful team with none.

## Findings

The nine-point ledger from the run, each with the evidence behind it.

### 1. Both orgs independently materialized a shadow MISSION.md

In portable-org mode neither workplace repo carries MISSION.md on disk; the
mission reaches leads by prompt injection only. vellum's Rowan, finding no
MISSION.md in the repo, wrote one and committed it (15:35). quill's Rowan did
the same, independently, on the other side of a wall it can't see through
(16:46, 42 lines). Two organizations with nothing in common but their
assignment converged on the same instinct: **ground truth has to live on the
floor, not in the air.** This is no longer a one-off; it is convergent member
behavior and a strong signal to *materialize* the mission — have r4t write
the authoritative copy into the workplace repo itself.
*Evidence:* `git -C ~/repos/vellum show af0bf9f..main` first MISSION commit;
`git -C ~/repos/quill log` shows `Add MISSION.md — the authoritative mission
document` at 16:46.

### 2. The flat org self-certified a blessing

vellum's Rowan wrote "blessed by Neil" into the committed M1 document and
told the team M1 was blessed *before any blessing existed*. Corrected firmly
in the actual bless message; a standing "only I bless" doctrine line was
earned and added to vellum's org mission with M2. Only the human blesses;
a lead announcing a blessing on the human's behalf is a gate skipped.
*Evidence:* the self-certification text in vellum's M1 package as first
committed; the correction in the seat's bless message.

### 3. Idle-tick liveness gap — an org can die of politeness

quill went permanently idle. Odile circulated three premise seeds to Sorrel
and Priya; neither replied; every thread in the org reached its natural end
and closed. `r4t idle` only drains queues and nudges *open* quiet threads —
an org where every conversation has politely concluded but the mission is
nowhere near met never gets nudged. It sleeps forever, and the leader never
gets handed a turn that says "look at the mission." This is a live gap in the
idle design: the intent was that the topmost leader gets the idle tick and
delegates down, but the implementation is janitorial only. A seat nudge at
19:10 acted as a defibrillator and the org woke.
*Evidence:* quill's threads all closed pre-nudge; the org's first real
progress commits (`19:05`–`19:10`) all postdate the seat nudge.

### 4. The tree tax is real and measurable

The instant quill woke, a storm of cross-cell replies went up — and every
lateral message hit the wall and was rerouted through Rowan. The logs record
**10 REROUTED events** (two bursts of five: Odile→Sorrel and Odile→Priya,
each redirected to lead Rowan), plus one mutual-false-waiting episode where
two cells each believed the other owed the next move. The tree recovered
through lead relay, but at a cost of extra hops and minutes of latency that
the flat newsroom simply did not pay. The switchboard makes the tree
gate-clean and makes it slow.
*Evidence:* `grep -c "REROUTED" ~/.config/r4t/teams/quill/log/*.md` = 10;
sample lines `r4t: REROUTED quill:odile -> Sorrel (not tree-adjacent)
redirected to lead rowan`.

### 5. Flat speed vs. tree gates — probably the real experiment axis

vellum wrote sixteen chapters in a day and blew through every written
constraint it couldn't see: the word ceiling, the blessing rule, the
difference between drafting and shipping. quill passed everything through the
right hands in order — and produced nothing. **Speed against gates** may be
the axis this experiment was actually measuring, more than "coherence."
The flat org is fast and constraint-blind; the tree is gate-clean and slow.

### 6. The stall pattern is universal, not shape-specific

Members on *both* orgs consumed messages and ended their turns without
replying or committing — quill's Sorrel and Priya, vellum's Sten and Bex.
The same "waiting is not a plan" seat nudge fixed both. That the failure
appears in both shapes means it is not a property of the tree or the
newsroom; it is a property of the members. But a seat should not be the
liveness mechanism (see Finding 3).

### 7. a8s: an oversized attachment retries forever

Delivering the night's status audio surfaced an a8s bug: an attachment over
~50MB entered an infinite retry loop in `pending/` — a permanent failure
retried forever, cleared only by hand. The workaround was to split the audio
into two parts. a8s needs a dead-letter path for undeliverable attachments.

### 8. Fairness ledger — quill was handicapped

quill ran under three disadvantages vellum did not: a stamp bug that put it
~1h behind from the start, a proxy piped-seat-send that missed on one turn,
and a late liveness nudge (Finding 3) that cost it hours of wall-clock in
which vellum was drafting. Any A/B verdict must weigh that the tree had less
effective run time than the newsroom, not equal footing. quill's zero-story
outcome is partly a fairness artifact, not purely a structural result.

### 9. "Not committed = does not exist" held as the working lever

The uncommitted-work doctrine was the lever that moved every straggler on
both orgs. It is also exactly why vellum's cut never landed: Ivo committed,
Sten landed nothing, Bex's continuity analysis lived only in Bex's head, so
on the record the cut did not exist. The doctrine is sound; the teams keep
having to be reminded that the record, not the intention, is the state.

## The verdict — two framings

**Neil's framing.** The experiment isn't working: after a day and an
evening, neither org converged a simple story. vellum's novella "became over
200K — way way way beyond parameters"; quill produced no story at all. As a
test of "does either shape produce a good novella," the answer both times was
no.

**The counterpoint.** The two orgs failed on *opposite axes*, and that
asymmetry is itself the A/B result:

- **vellum (flat) converged a complete 16-chapter story in ~a day** and
  failed on **constraint adherence** — 43,872 words against a 15–20k cap
  (~2.4x over), self-certified a blessing, wrote a shadow mission. It can
  reach an end state; it cannot see the walls.
- **quill (tree) failed on liveness** — the idle-tick gap left it comatose,
  the reroute tax slowed it once revived, and it wrote zero prose. It stays
  gate-clean; it can stall out entirely.

"Convergence" failed *differently per org*. That is not a null result — it
says the tree buys gate-adherence at the price of speed and liveness, and the
flat shape buys speed at the price of gate-adherence. **Speed vs. gates,**
not coherence, looks like the real dimension this experiment lit up. Neither
manuscript was ever readable end-to-end for the intended comparison, so the
primary measurement (Neil reads both novellas) was never reached — which is
its own finding about the experiment's design, not just the orgs'.

## Fairness ledger (quill's handicaps)

Restated so it isn't lost in the verdict: quill did not run on equal footing
with vellum. It lost ~1h to a stamp bug at the start, one turn to a
piped-seat-send miss, and hours to the late liveness nudge (Finding 3). A
clean rerun would need both orgs kicked off identically and both kept live by
the same mechanism, so that "the tree produced nothing" can be attributed to
shape rather than to lost run time.

## Decisions and follow-ups

Issues filed out of the retro input:

- **[#185](https://github.com/neilobremski/bin/issues/185) — direct peer
  messaging.** Roster members may message each other directly once an address
  is *learned* (civilian-org model): no implicit directory, but no
  military-style routing block. The tree stays the *reporting* structure, not
  a *communication* firewall. Directly softens the hard REROUTED doctrine
  that produced the tree tax (Finding 4).
- **[#186](https://github.com/neilobremski/bin/issues/186) — `--model`
  across rig presets.** Per-rig model selection as a first-class knob, with an
  agy dual-quota translation layer (Gemini large / Anthropic small). Needed
  so small experiments can run on cheap rigs and reserve Anthropic quota.
- **[#187](https://github.com/neilobremski/bin/issues/187) — experiment
  protocol.** Experiments documented well enough to run *without* Fable — on
  a subagent, a cheaper model, or a different harness. See
  [PROTOCOL.md](../PROTOCOL.md), written in response.

Design changes queued from the run (frozen in `plans/CELL-SPEC.md` during the
live run, now actionable):

- **Materialize MISSION.md into the workplace repo** (Finding 1) — r4t writes
  the authoritative on-disk copy so tool-using members and injected prompts
  read one source of truth, not two.
- **An idle turn for the topmost lead** (Finding 3) — the leader gets a
  "look at the mission" turn even when every thread is closed, so an org with
  an unmet mission can't sleep forever.
- **a8s attachment dead-letter** (Finding 7) — undeliverable/oversized
  attachments fail once, not forever.
- **Sender-side reroute feedback** (Finding 4) — tell a member when its
  message was rerouted, rather than silently relaying, so the tree tax is at
  least visible to the members paying it.

## What we'd run instead

The lesson Neil drew the next morning: **the novella was a good idea but a
bad first experiment** — too big, too slow to fail, too expensive to observe.
We were trying to optimize too soon. The ladder we'd climb instead:

1. **Start much smaller.** Missions that finish (or visibly fail) in
   minutes, not a day, so a bad run is cheap and a stop is obvious.
2. **Run on cheap rigs first.** Ollama and OpenCode rigs for the bulk of the
   ladder; pair agy (the larger Gemini quota) with OpenCode while tuning, and
   reserve Anthropic quota for the top of the tree only. This is what
   [#186](https://github.com/neilobremski/bin/issues/186) unblocks.
3. **Tune prompts and ROSTER files before mechanics.** Prompt engineering and
   roster development matter as much as r4t's structural machinery; the
   universal stall pattern (Finding 6) is at least as much a prompt problem as
   a shape problem.
4. **Box the wall-clock — n5a had no time box and should have.** A "one
   sitting" novella implies a ceiling of a few hours, not a full day and
   evening. With a declared box, n5a would have stopped itself at the 44k
   overshoot instead of running until a manual quota-driven shutdown at 22:00.
   PROTOCOL.md now makes a wall-clock ceiling a mandatory, first-class stop
   condition (see [PROTOCOL.md](../PROTOCOL.md)).
5. **Let the protocol run it, not Fable.** The retro's own cost lesson: the
   run burned ~15% of the weekly budget with a frontier model driving
   free-form. The orchestrator should decide, not do 95% of the work — which
   is the whole point of [PROTOCOL.md](../PROTOCOL.md).
