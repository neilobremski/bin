# The verification round — implementation spec

*2026-07-14. Follows the d5n M4 experiment (experiments/d5n-m4/) and Neil's
ruling after the bless: structural verification and pattern sweeps catch
disjoint failure classes. The runner's real-key playtest PASSED M4 while two
lines of regex would have caught the literal `\n\n` the milestone shipped
with. One issue, one implementation PR, spec first (this doc). Style follows
[COMMS-SPEC.md](COMMS-SPEC.md); every open question is resolved inline
(marked DECIDED).*

*Scope: `apps/r4t/` only. Pre-v1 scorch-the-earth — no migration code; an
org without the new setting behaves exactly as today.*

---

## 1. Background and the through-line

Three findings from the d5n run drive this:

- **Bones vs eyes.** Independent playtest verification (eyes) confirmed M4's
  navigation worked; it did not notice literal `\n\n` rendering in the text.
  A forbidden-pattern sweep (bones) would have. Neither replaces the other;
  the round needs both, and the bones are cheap enough to run on every
  human-bound escalation.
- **Self-certification.** The org described a 0.29-second smoke test as
  "perfectly validated." Agents cannot be the judge of their own
  deliverables; the judge must be machinery the org cannot see into.
- **Information asymmetry is the design.** Agents get an opaque pass/fail on
  stdout; the detail (which pattern, which file) goes to stderr and lands
  only in surfaces the human reads. Agents cannot game tests they cannot
  see, and the human can spot a broken test from the detail.

Two features deliver the round: a forbidden-pattern sweep CLI (`r4t check`)
and a doorbell gate that runs a configured check command before the org can
ring the human. The sweep is the first such command; the gate accepts any
CLI, so future checks (test suites, linters, form validators) slot in
without touching dispatch again.

## 2. Feature A — `r4t check <node>`: the forbidden-pattern sweep

### Pattern files

Patterns live OUTSIDE every repo, in the machine-global config dir:

- `~/.config/r4t/checklists/default.txt` — applies to every node
- `~/.config/r4t/checklists/<node>.txt` — per-node additions (optional)

Both are applied when present. Format: one Python regex per line, `#`
comments and blank lines ignored. Case-sensitivity is the author's business
via inline `(?i)`. DECIDED: no name/tab/severity columns — a line is a
regex, nothing else. A malformed regex is an operational error (exit 2,
message on stderr naming the file and line), not a silent skip.

The files are intentionally uncommitted and may contain private strings
(the human's name, project codewords). A sync mechanism from a GitHub
secret (pii-check style) is a follow-up, NOT in this PR. This PR documents
the file location and format only; docs use placeholder patterns, never
real private strings.

DECIDED: no pattern files at all → the sweep passes with a note on stderr
(`no checklists configured`). The gate is opt-in by configuration presence.

### Sweep target

Tracked files in the node's org workplace repo: `git ls-files` run in
`org.workplace` (resolve the node the same way other node-scoped commands
do). Each file is read as UTF-8 text; files that fail to decode are skipped
silently (binaries). Every pattern is tested against every line.

### Output contract

- **stdout** (the agent-visible surface): exactly one line —
  `check passed` or `check failed: N finding(s)`. Nothing else, ever.
- **stderr** (the human-visible surface): one line per finding —
  `<path>:<line>: <pattern>` — plus operational notes.
- **exit**: 0 pass, 1 findings, 2 operational error (unknown node,
  unreadable workplace, malformed regex).

### Seed patterns (documented for Neil to install locally, not shipped)

- `\\n` — literal backslash-n in committed text (the M4 escape bug)
- the human's name in produced docs/code
- self-certification phrases (`(?i)blessed by`, `(?i)perfectly validated`)

## 3. Feature B — the doorbell gate

### Setting

`r4t-org.json` gains one org setting alongside `comms` / `egress` /
`leader_sees_lateral`:

- `doorbell_check` (string, default absent): a shell command. Absent or
  empty → no gate; today's behavior exactly.

Parsed in `org._parse_settings` (must be a string; anything else is a
config error that degrades to absent, same pattern as the booleans).
For d5n the value will be `r4t check d5n` — the setting is verbatim; no
magic node substitution. The command runs with `cwd=org.workplace` and
`R4T_NODE=<node>` in the environment for commands that want it.

### Gate semantics (in `dispatch._park_seat`)

The gate wraps the RING only. Today `_park_seat` parks the message and
rings `Address:` when no seat session is attached. With a `doorbell_check`
configured, before ringing:

1. Run the command (shell, `cwd=org.workplace`, timeout 120s).
2. **Exit 0** → ring as today. Log `r4t: GATE <node> passed` to the node log.
3. **Nonzero exit** → do NOT ring. The message still PARKS — seat mail is
   never lost; the gate protects the human's attention, not the mailbox.
   The sender receives an error-class reply (same channel as `_tell_error`
   structured errors): `seat unreachable: <first line of check stdout>`.
   The check's stderr is appended to the node log, line-prefixed
   `r4t: GATE <node>`, so `r4t logs` shows the human the detail.
4. **Timeout or exec failure** → fail closed: no ring, park, sender gets
   `seat unreachable: check did not complete`, loud log line. DECIDED:
   a broken gate must not become a broken doorbell silently — the log
   line is the human's signal to fix the check.

DECIDED: when a seat session IS attached, no ring happens today and the
gate does not run — the human is present and reads parked mail live. The
gate governs escalation to an absent human only.

DECIDED: the gate applies to every doorbell ring (all human-bound
escalations), not just milestone/bless requests — dispatch cannot classify
intent and must not try. Cheap checks make this affordable.

## 4. Non-goals (round two and later)

- Doorbell FORMS (structured escalation with per-project presets).
- Checklist sync from a GitHub secret.
- MISSION.md machine-checkable acceptance sections — a doc convention the
  human starts writing by hand for M5; tooling that parses it comes after
  the convention proves out.

## 5. Tests and docs

- `tests/test_check.py` (new): pattern-file parsing (comments, blanks,
  malformed regex → exit 2), sweep over a tmp git repo (findings,
  binary skip, clean pass), no-checklist pass, stdout opacity (assert
  stdout carries no path/pattern text).
- `tests/test_dispatch.py`: gate pass rings; gate fail parks without ring,
  sender gets the error reply, node log carries stderr detail; timeout
  fails closed; no setting → today's behavior byte-for-byte.
- `tests/test_org.py` (or wherever settings parse is tested):
  `doorbell_check` string accepted, non-string degrades with error.
- README: short section on the verification round — the two surfaces
  (stdout/stderr), the gate setting, the checklist location. One-sentence
  user-facing style; detail stays here and in docstrings.
