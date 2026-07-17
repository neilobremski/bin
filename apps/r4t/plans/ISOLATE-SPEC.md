# The isolation phase — implementation spec

*2026-07-16 ruling: isolation config moved rig → org (per-project decision; rigs span the system); border unchanged. Implemented in the PR that carries this note.*

*2026-07-15. Follows the verification round (VERIFY-SPEC.md) and Neil's
ruling that morning: run-as-user and run-via-container become first-class
r4t features, buildable in parallel with the verification work. One issue,
one implementation PR, spec first (this doc). Every open question is
resolved inline (marked DECIDED).*

*Scope: `apps/r4t/` only. Pre-v1 scorch-the-earth — no migration code; a
rig without the new keys behaves byte-for-byte as today.*

*Ratified by Neil 2026-07-16: workplace access is probe-only (r4t never
fixes operator repo perms), presets stay untouched under isolation
(posture lives in docs), both variants ship in one PR.*

---

## 1. Background and the through-line

Two findings drive this:

- **Harness sandbox flags are not a security boundary.** The d5n M4 run
  proved it: one CLI's sandbox is model-discretionary (the harness *hints*
  the model to retry denied operations with a bypass parameter, and
  auto-approval honors it), and the same sandbox silently broke message
  staging for a whole org. Rationalizing per-harness flags is a losing
  game; the capability map that came out of it (docs/harness-agy.md)
  exists because nobody could say from memory what the flag actually did.
- **The OS already has a boundary that works.** A private production
  deployment has run an agent for weeks behind a plain Unix user boundary:
  the agent user has no sudo and cannot read the router user's home; the
  only channel between them is a setgid shared directory the agent writes
  message envelopes into. Verified live by inspection (2026-07-15): no
  ACLs, no namespaces, no container — `sudo -u`, `chmod 2770`, and POSIX
  ownership do all the work.

The posture this feature ships: **the security boundary is the operating
system — a Unix user or a container — and inside that boundary the agent
runs fully permissive.** Harness sandbox flags stop being load-bearing.

## 2. Where the knobs live

DECIDED: per-rig keys in `rigs.json`, named `run_as` and `container`.

- Rigs are already the member-capability layer and already live outside
  the repo in machine-global config — and an OS username or local image
  tag is a machine-local fact. Rosters stay portable; the same roster runs
  isolated on one machine and bare on another.
- `container` names the concept, not the implementation (the
  protocol-not-library rule). Docker is today's backend; the key does not
  say so.
- DECIDED: `run_as` and `container` are mutually exclusive. Both set is a
  config error and the rig fails closed (member does not run), same
  posture as a rig missing from config.

## 3. Feature A — `run_as: "<username>"`

### Prerequisites (operator-provisioned, documented, never automated)

- The target user exists (`useradd -m -s /bin/bash <user>`), is NOT in
  the sudo group, and has no sudoers entry of its own.
- The router user can `sudo -u <user>` without a password. The docs show
  the scoped shape (a `Cmnd_Alias` limited to the wake command) as the
  recommended grant; a blanket NOPASSWD grant also works.
- The org workplace is writable by the agent user (shared group +
  `g+ws`, or any arrangement the operator prefers).

DECIDED: r4t verifies prerequisites and fails closed with action-first
errors carrying the exact fix command — it never provisions users,
sudoers entries, or workplace permissions itself. Verification at invoke
time: `sudo -n -u <user> true` (grant probe) and a write probe in the
workplace as the agent user. A failed probe fails the turn like any other
turn failure (breaker counts it; the message stays queued, never lost).

### Invoke wrapping

When `run_as` is set, the rig's resolved argv is wrapped:

```
sudo -u <user> bash --login -c \
  'export TELL_OUTBOX_DIR="$1"; cd "$2"; shift 2; exec "$@"' \
  _ <staging-dir> <workplace> <argv...>
```

- `bash --login` so the agent user's own profile/PATH resolves its own
  CLI installs (per-user tool installs are the norm).
- Env must ride as positional arguments baked through the `-c` string:
  sudoers `env_reset` (the default) strips the caller's environment, so
  exported vars from dispatch never survive the boundary. DECIDED: the
  `exec "$@"` form, not a quoted command string — argv passes through
  untouched and quoting bugs are structurally impossible.
- cwd is the org workplace (r4t members work in the shared repo; this
  deliberately differs from the production precedent, whose agent works
  in its own home).
- The existing rig wall-clock timeout applies to the whole `sudo`
  wrapper unchanged.

### Shared directories

The message channel is the same pattern the production deployment uses:

- Any per-member directory the agent must WRITE (the staging dir that
  `TELL_OUTBOX_DIR` points at) is owned `<router>:<agent-group>`, mode
  `2770` (setgid).
- Any directory the agent must only READ (delivered files/attachments)
  is group-readable to the agent's group, no write bit.
- DECIDED: r4t re-asserts ownership and mode on these dirs before every
  turn, not just at setup — the boundary is procedural, and re-assertion
  is what made the precedent robust in practice. This is the one place
  r4t DOES fix permissions itself: these dirs are r4t's own state, unlike
  the operator's workplace.

Turn transcripts (`agents/<member>/turns/`) already live under the
router-owned team dir and are written by dispatch from the subprocess
pipe — the agent user cannot reach, alter, or erase the audit trail. No
change needed; stated here as a required property.

## 4. Feature B — `container: "<image>"`

- Invoke becomes `docker run --rm` with:
  - the workplace bind-mounted read-write at the same absolute path, and
    used as the container workdir;
  - the member staging dir bind-mounted read-write at the same path,
    `TELL_OUTBOX_DIR` injected via `-e` (no env_reset inside a container
    — env passes directly);
  - the delivered-files dir bind-mounted read-only;
  - the a8s client bind-mounted read-only with a PATH shim so `tell`
    resolves inside the container.
- DECIDED: the image contract is minimal — the harness CLI must be on
  the image's PATH. r4t never builds, pulls, or inspects images; a
  missing image is a turn failure with the docker error on the human
  surface.
- DECIDED: a per-rig `container_args` list is appended verbatim to
  `docker run` (the option-passthrough principle). Credentials for cloud
  CLIs ride this way as operator-chosen read-only mounts. Interactive
  and browser-based auth flows are an explicit non-goal of the container
  variant — if a harness cannot auth headlessly from mounted files, it
  does not belong in a container rig.
- Timeout: the container gets a deterministic name
  (`r4t-<node>-<member>-<ts>`); on rig-timeout expiry r4t kills the
  container by name, then reaps. `--rm` handles the normal path.
- Network stays on (agents talk to model APIs). Operators who want it
  off say so in `container_args`.

## 5. Shared semantics

- Fail closed everywhere; every isolation failure is an ordinary failed
  turn — breaker counts it, messages stay queued, `r4t status` shows the
  member unhealthy with the real error and a `(try: ...)` hint.
- `r4t status` rig rows show the isolation mode (`user:<name>` /
  `container:<image>`) so the operator sees the boundary at a glance.
- Harness presets are untouched: isolation composes with any preset.
  Docs state the intended posture — behind `run_as`/`container`, choose
  the preset's most-permissive flags; the OS is the boundary.

## 6. Non-goals (round two and later)

- Auto-provisioning (users, sudoers, workplace group bits) — documented
  prerequisites only.
- A configurable forbidden-paths sweep of the agent user's home (the
  precedent self-heals rogue router-state paths before every wake;
  generalizing that is its own feature).
- GUI/desktop OAuth flows for either variant.
- Supervising the router daemon itself (systemd unit / reboot survival)
  — real, but orthogonal to per-turn isolation.
- Remote-machine execution; per-member overrides of a rig's isolation.

## 7. Tests and docs

- `tests/test_isolate.py` (new): argv wrapping shape for both variants
  (assert exact wrapper argv, no real sudo/docker — fake binaries on
  PATH); mutual-exclusion config error fails closed; failed sudo probe →
  turn fails, message still queued, breaker incremented; shared-dir
  assertion sets owner-group/mode on tmp dirs and re-asserts after
  tampering; container timeout kills by name (fake docker records the
  kill).
- `r4t status` row rendering for isolated rigs.
- Docs: one user-facing sentence in the README; `docs/isolation.md`
  operator page with the full prerequisite incantations (useradd,
  scoped sudoers example, workplace group setup, container auth-mount
  example). Detail stays there and in docstrings.
