"""Dispatch — enqueue delivered mail, then drain member queues as batch turns.

Every inbound message to a member ENQUEUES, unconditionally, into that
member's durable queue (state.enqueue). No gate ever drops or dead-letters a
deliverable message; dead letters are for undeliverable mail only (unknown
recipient, disabled member, no rig). A separate drain loop picks a runnable
member with a non-empty queue and runs ONE turn that drains the WHOLE queue:
the prompt renders every queued message at once, so an agent that sees
"teammates discussed X, then the lead overrode with Y" pivots in one reading
instead of burning a turn per message.

Runnability is governed autonomously — no human gates. A member runs when its
own spend bucket and the shared team bucket both hold at least 1 unit (a turn
costs 1 of each), its failure breaker is closed, and the team throttle
(max_concurrent, cadence) admits another start. An empty bucket means the
member is *resting*: its queue holds and it runs again when the bucket
refills. Nothing is lost.

The agent replies with the unmodified `tell`. Dispatch points the harness
subprocess's $TELL_OUTBOX_DIR at a per-turn staging dir and releases the
staged envelopes afterwards: attribution (only this turn wrote there), the
thread/hop header stamped mechanically, per-turn send quota, then either the
node's real outbox (external) or straight onto the recipient member's queue
(intra-team). A reply is attributed to the thread of the message it answers.

Requeueing note: a8s trashes the inbox message BEFORE spawning the wake
subprocess and only logs its exit code (daemon.wake_once), so exiting nonzero
does NOT redeliver. That is fine — the message is already durably queued
before any turn runs.
"""
from __future__ import annotations

import errno
import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import state
import tasks
from rig import RigConfig, RigError, Rig, load_rig_config
from notify import TellFn
from roster import Member, Roster, RosterError, load_roster

PROMPT_BODY_MAX = 4000
HISTORY_BODY_MAX = 2000
DRAIN_MAX_PASSES = 20

RAN = "ran"
DEFERRED = "deferred"
RESTING = "resting"
DEAD = "dead-letter"
QUEUED = "queued"
SKIPPED = "skipped"
BREAKER = "breaker"


@dataclass
class DispatchContext:
    root: Path
    node: str
    roster_path: Path
    config_path: Path
    tell_fn: TellFn


def split_recipient(to: str) -> tuple[str, str]:
    """`acme:phil` -> (`acme`, `phil`); bare `acme` -> (`acme`, `""`).
    The sub-address is everything after the FIRST colon, verbatim."""
    to = (to or "").strip()
    if ":" in to:
        node, sub = to.split(":", 1)
        return node.strip(), sub.strip()
    return to, ""


def _is_internal(node: str, to: str) -> bool:
    t = (to or "").strip().lower()
    return t == node.lower() or t.startswith(node.lower() + ":")


def _display_name(node: str, addr: str) -> str:
    prefix = node.lower() + ":"
    a = (addr or "").strip()
    return a[len(prefix):] if a.lower().startswith(prefix) else a


def _canonical_recipient(node: str, roster: Roster, to: str) -> str:
    """Agents address the walled garden by bare first name; the wire uses
    `node:name`. Bare roster names — humans included — canonicalize to
    internal form; anything else (external addresses, unknown names) passes
    through untouched. Human members are internal on purpose: their mail parks
    in the node's seat mailbox, and `Address:` is only a doorbell copy when no
    seat session is attached."""
    t = to.strip()
    if ":" in t:
        prefix, _, sub = t.partition(":")
        if prefix.strip().lower() != node.lower():
            return t
        name = sub
    else:
        name = t
    member = roster.find(name)
    if member is None:
        return t
    return f"{node}:{member.name.lower()}"


def _same_recipient(node: str, roster: Roster, a: str, b: str) -> bool:
    return (
        _canonical_recipient(node, roster, a).strip().lower()
        == _canonical_recipient(node, roster, b).strip().lower()
    )


def _human_member(node: str, roster: Roster, addr: str) -> Member | None:
    """The roster human that `addr` (canonical or bare) names, if any."""
    t = (addr or "").strip()
    if ":" in t:
        prefix, _, sub = t.partition(":")
        if prefix.strip().lower() != node.lower():
            return None
        t = sub
    member = roster.find(t)
    return member if member is not None and member.is_human else None


def _tell_error(ctx: DispatchContext, recipient: str, text: str) -> None:
    body = f"[r4t {ctx.node}] {text}"
    state.append_log(ctx.node, f"r4t: ERROR -> {recipient}: {text}")
    ctx.tell_fn(recipient, body)


def _park_seat(ctx: DispatchContext, member: Member, sender: str, message: str) -> None:
    """Deliver a message to a roster human: park it in the node's seat
    mailbox, and ring the `Address:` doorbell (a forwarded copy over a8s)
    only when no seat session is attached to read it live."""
    state.park_seat_message(ctx.node, member.name, sender, message)
    bell = ""
    if member.address and not state.seat_attached(ctx.node, member.name):
        _, _, _, bell_body = tasks.parse_header(message)
        ctx.tell_fn(member.address, bell_body)
        bell = f", doorbell -> {member.address}"
    state.append_log(
        ctx.node,
        f"r4t: SEAT {member.name.lower()} <- {sender} (parked{bell})",
    )


def _load_roster(ctx: DispatchContext, sender: str) -> Roster | None:
    try:
        return load_roster(ctx.roster_path)
    except RosterError as e:
        _tell_error(ctx, sender, f"cannot dispatch: {e}")
        return None


def _load_config(ctx: DispatchContext, sender: str) -> RigConfig | None:
    try:
        return load_rig_config(ctx.config_path)
    except RigError as e:
        _tell_error(ctx, sender, f"cannot dispatch: {e}")
        return None


def _dispatchable_names(roster: Roster) -> list[str]:
    return [m.name for m in roster.members if not m.is_human and not m.errors]


def _teammate_lines(ctx: DispatchContext, roster: Roster, member: Member) -> list[str]:
    lines: list[str] = []
    for m in roster.members:
        if m.name.lower() == member.name.lower():
            continue
        if m.is_human:
            lines.append(
                f"    - {m.name} (Human, tell {m.name.lower()}) — {m.role}".rstrip(" —")
            )
        elif not m.errors:
            lines.append(
                f"    - {m.name} (tell {m.name.lower()}) — {m.role}".rstrip(" —")
            )
    return lines


def build_prompt(
    ctx: DispatchContext,
    roster: Roster,
    member: Member,
    batch: list[dict],
) -> str:
    history = state.read_history(ctx.node, member.name)
    teammates = _teammate_lines(ctx, roster, member)
    message_lines: list[str] = []
    for env in batch:
        sender = _display_name(ctx.node, str(env.get("from", "?")))
        thread = str(env.get("task", "")) or "?"
        repeats = int(env.get("repeats", 1) or 1)
        body = str(env.get("body", "")).strip() or "(empty message)"
        if len(body) > PROMPT_BODY_MAX:
            body = body[:PROMPT_BODY_MAX] + "\n[... message truncated by r4t ...]"
        header = f"From: {sender} (thread {thread})"
        if repeats > 1:
            header += f" (sent {repeats} times)"
        message_lines.append(header)
        message_lines.append("")
        message_lines.append(body)
        message_lines.append("")
    parts = [
        f"You are {member.name}, a member of the {ctx.node} team, working in "
        f"the team repo at {ctx.root.resolve()} (your current directory). "
        "Write files here with relative paths only.",
        "",
        "## Who you are (from the team roster)",
        member.persona or f"### {member.name}",
        "",
        "## Your conversation so far (messages you received and sent)",
        history.strip() or "(no prior messages — this is your first recorded turn)",
        "",
        "## Messages since your last turn",
        *(message_lines or ["(none)"]),
        "## How to work",
        "- This is one turn: you were woken with every message above at once. "
        "Read them together and act on the current state, not each message in "
        "sequence. Your process ends when you finish; you are woken again when "
        "more messages arrive.",
        "- Never wait for a reply inside a turn. If you need work from "
        "teammates, message them and END your turn without answering the "
        "original request; when their replies wake you later, answer the "
        "person who asked once you have enough.",
        "- Send messages with the `tell` shell command (run it via your shell "
        "tool — printing it as text sends nothing):",
        "    - reply to whoever asked: tell <name> \"<message>\"",
        "    - a teammate: tell <name> \"<message>\". Teammates:",
        *(teammates or ["    - (none)"]),
        "- Speak to teammates directly and one at a time — do not post to "
        "chat rooms or broadcast channels.",
        "- Do not send acknowledgment-only messages. If you have nothing "
        "substantive to add, send nothing — silence is fine.",
    ]
    return "\n".join(parts)


def run_harness(
    rig: Rig,
    prompt: str,
    cwd: Path,
    *,
    env: dict | None = None,
    variant: int = 0,
) -> tuple[int, str, float, bool]:
    """Run the rig's argv (pool variant `variant`) with {prompt} substituted
    as a single argv element — never a shell. Returns (exit_code, output,
    duration_seconds, timed_out)."""
    argv = rig.argv(prompt, variant)
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            start_new_session=True,
        )
    except OSError as e:
        return 127, f"failed to spawn harness {argv[0]!r}: {e}", 0.0, False
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=rig.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
        output, _ = proc.communicate()
    duration = time.monotonic() - start
    return proc.returncode, output or "", duration, timed_out


def _throttle_block(ctx: DispatchContext, config: RigConfig) -> str | None:
    throttle = config.throttle
    if throttle.max_concurrent > 0:
        live = len(state.live_locks(ctx.node))
        if live >= throttle.max_concurrent:
            return (
                f"team throttle: {live} live turn(s) >= max_concurrent "
                f"{throttle.max_concurrent}"
            )
    if throttle.min_seconds_between_turn_starts > 0:
        last = state.read_last_turn_start(ctx.node)
        if last is not None:
            elapsed = time.time() - last
            if elapsed < throttle.min_seconds_between_turn_starts:
                return (
                    f"team throttle: last turn started {elapsed:.0f}s ago < "
                    f"min_seconds_between_turn_starts "
                    f"{throttle.min_seconds_between_turn_starts:g}"
                )
    return None


# ---------- ingress (enqueue only; never runs a turn) ----------

def _ingest(
    ctx: DispatchContext,
    sender: str,
    to: str,
    message: str,
    *,
    trusted_header: bool,
    roster: Roster | None = None,
    config: RigConfig | None = None,
) -> str:
    """Resolve the recipient and enqueue. Humans park in the seat; undeliverable
    mail dead-letters with an audit record; a deliverable message to an AI
    member enqueues unconditionally (duplicate-collapsed) and returns QUEUED."""
    _, sub = split_recipient(to)

    if roster is None:
        roster = _load_roster(ctx, sender)
    if roster is None:
        return SKIPPED

    if sub:
        member = roster.find(sub)
        if member is None:
            names = ", ".join(_dispatchable_names(roster)) or "(none)"
            _tell_error(
                ctx, sender,
                f"no team member named {sub!r}. Dispatchable members: {names}.",
            )
            state.record_dead_letter(
                ctx.node, reason="unknown-recipient", sender=sender, to=to,
                task="", content=message,
            )
            return DEAD
    else:
        member = roster.leader()
        if member is None:
            names = ", ".join(_dispatchable_names(roster)) or "(none)"
            _tell_error(
                ctx, sender,
                "no leader is marked in the roster, so bare messages to "
                f"{ctx.node} have no recipient. Address a member directly "
                f"(members: {names}).",
            )
            state.record_dead_letter(
                ctx.node, reason="no-leader", sender=sender, to=to,
                task="", content=message,
            )
            return DEAD

    if member.is_human:
        _park_seat(ctx, member, sender, message)
        return SKIPPED

    if member.errors:
        _tell_error(
            ctx, sender,
            f"{member.name} is disabled by a roster problem: {member.error}. "
            f"Fix {ctx.roster_path.name} and resend.",
        )
        state.record_dead_letter(
            ctx.node, reason="member-disabled", sender=sender, to=to,
            task="", content=message,
        )
        return DEAD

    if config is None:
        config = _load_config(ctx, sender)
    if config is None:
        return SKIPPED
    rig, err, _pinned = config.rig_for(member)
    if rig is None:
        _tell_error(ctx, sender, f"{member.name} cannot run: {err}")
        state.record_dead_letter(
            ctx.node, reason="no-rig", sender=sender, to=to,
            task="", content=message,
        )
        return DEAD

    if trusted_header:
        thread_id, hop, auto, body = tasks.parse_header(message)
    else:
        # Ingress protocol: headers never legitimately arrive from outside the
        # garden (egress strips them), so an external header is either noise or
        # a forgery aimed at an existing thread — treat the whole message as
        # content and open a fresh thread.
        thread_id, hop, auto, body = None, 0, False, (message or "").strip()
    if thread_id is None:
        thread_id = tasks.new_task_id()
        hop = 0
    tasks.ensure_task(ctx.node, thread_id, sender)

    state.enqueue(
        ctx.node,
        member.name,
        {
            "from": sender,
            "to": _canonical_recipient(ctx.node, roster, to),
            "task": thread_id,
            "hop": hop,
            "auto": auto,
            "body": body,
        },
    )
    state.update_meta(ctx.node, member.name, last_inbound_at=state.utc_now())
    state.append_log(
        ctx.node,
        f"r4t: QUEUED {sender} -> {member.name.lower()} thread={thread_id} "
        f"hop={hop} (depth {state.queue_depth(ctx.node, member.name)})",
    )
    return QUEUED


# ---------- staging release ----------

def _real_outbox(ctx: DispatchContext) -> Path:
    raw = os.environ.get("TELL_OUTBOX_DIR", "").strip()
    if raw:
        return Path(raw)
    return ctx.root / ".outbox"


def _release_one(
    ctx: DispatchContext,
    outbox: Path,
    staging: Path,
    envelope: dict,
    sender_addr: str,
    thread_id: str,
    next_hop: int,
    body: str,
    roster: Roster,
    config: RigConfig,
) -> None:
    to = str(envelope.get("to", "")).strip()
    if _is_internal(ctx.node, to):
        header = tasks.format_header(thread_id, next_hop, auto=True)
        _ingest(
            ctx, sender_addr, to, f"{header} {body}",
            trusted_header=True, roster=roster, config=config,
        )
        bundle = staging / str(envelope.get("id", ""))
        if bundle.is_dir():
            shutil.rmtree(bundle, ignore_errors=True)
            state.append_log(
                ctx.node,
                f"r4t: WARN attachments dropped on intra-team route "
                f"{sender_addr} -> {to}",
            )
        state.append_log(
            ctx.node,
            f"r4t: RELEASED-internal {sender_addr} -> {to} thread={thread_id} "
            f"hop={next_hop}",
        )
        return
    # Egress protocol: the r4t header never leaves the garden. Other a8s nodes
    # must not need to know whether a name is one agent, a human, a device, or
    # a whole roster — class marking survives as envelope metadata only.
    envelope["content"] = body
    envelope["x_r4t_class"] = "auto"
    envelope["from"] = sender_addr
    outbox.mkdir(parents=True, exist_ok=True)
    msg_id = str(envelope.get("id", "")) or tasks.new_task_id()
    envelope["id"] = msg_id
    bundle = staging / msg_id
    if bundle.is_dir():
        destination = outbox / msg_id
        if destination.exists():
            shutil.rmtree(bundle, ignore_errors=True)
        else:
            try:
                os.replace(bundle, destination)
            except OSError as e:
                if e.errno != errno.EXDEV:
                    raise
                temporary = outbox / f".{msg_id}.{tasks.new_task_id()}.tmp"
                try:
                    shutil.copytree(bundle, temporary)
                    if not destination.exists():
                        os.replace(temporary, destination)
                finally:
                    shutil.rmtree(temporary, ignore_errors=True)
                shutil.rmtree(bundle, ignore_errors=True)
    state.atomic_write_json(outbox / f"{msg_id}.json", envelope)
    state.append_log(
        ctx.node,
        f"r4t: RELEASED {sender_addr} -> {to} thread={thread_id} hop={next_hop}",
    )


def release_staging(
    ctx: DispatchContext,
    config: RigConfig,
    roster: Roster,
    member: Member,
    rig: Rig,
    batch: list[dict],
) -> dict:
    """Process the turn's staged envelopes in send order: per-turn send quota,
    thread attribution, outbound history, then release (real outbox or the
    recipient member's queue). A reply is attributed to the thread of the
    message it answers — the newest queued message from that recipient in this
    batch; a message to someone the batch did not include rides the batch's
    newest thread. A substantive reply to a thread's originator closes it.
    Returns {"released": n, "violations": n}."""
    staging = state.staging_dir(ctx.node, member.name)
    sender_addr = f"{ctx.node}:{member.name.lower()}"
    outbox = _real_outbox(ctx)

    consumed: dict[str, tuple[str, int]] = {}
    newest: tuple[str, int] | None = None
    for env in batch:
        key = _display_name(ctx.node, str(env.get("from", ""))).strip().lower()
        pair = (str(env.get("task", "")), int(env.get("hop", 0) or 0))
        consumed[key] = pair
        newest = pair
    if newest is None:
        newest = (tasks.new_task_id(), 0)

    released = 0
    violations = 0
    for i, path in enumerate(state.staged_envelopes(ctx.node, member.name)):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        if not isinstance(envelope, dict):
            path.unlink(missing_ok=True)
            continue
        to = str(envelope.get("to", "")).strip()
        _, _, _, body = tasks.parse_header(str(envelope.get("content", "")))
        if not to or not body.strip():
            path.unlink(missing_ok=True)
            continue
        to = _canonical_recipient(ctx.node, roster, to)
        envelope["to"] = to
        if i >= rig.max_sends_per_turn:
            path.unlink(missing_ok=True)
            violations += 1
            state.record_dead_letter(
                ctx.node, reason="quota", sender=sender_addr, to=to,
                task=newest[0], content=body,
            )
            state.append_log(
                ctx.node,
                f"r4t: QUOTA {sender_addr} -> {to} "
                f"(> max_sends_per_turn {rig.max_sends_per_turn})",
            )
            continue

        key = _display_name(ctx.node, to).strip().lower()
        thread_id, in_hop = consumed.get(key, newest)
        next_hop = in_hop + 1

        state.append_history(
            ctx.node,
            member.name,
            f"## {state.utc_now()} to {_display_name(ctx.node, to)}\n\n"
            + (body if len(body) <= HISTORY_BODY_MAX else body[:HISTORY_BODY_MAX] + " [...]"),
        )
        _release_one(
            ctx, outbox, staging, envelope, sender_addr, thread_id, next_hop,
            body, roster, config,
        )
        path.unlink(missing_ok=True)
        released += 1

        task = tasks.load_task(ctx.node, thread_id)
        if (
            task is not None
            and task.get("status") != tasks.STATUS_CLOSED
            and _same_recipient(ctx.node, roster, to, str(task.get("creator", "")))
        ):
            tasks.close_task(ctx.node, thread_id)
            state.append_log(
                ctx.node,
                f"r4t: ANSWERED thread={thread_id} {sender_addr} -> {to} "
                "(originator answered, thread closed)",
            )
    shutil.rmtree(staging, ignore_errors=True)
    return {"released": released, "violations": violations}


# ---------- the turn ----------

STDOUT_REPLY_MIN_CHARS = 80

_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?)")
_HARNESS_NOISE_RE = re.compile(
    r"^(?:"
    r">\s+\S+\s+·\s"          # opencode banner: "> build · qwen3.6:latest"
    r"|[→✱✳✻●⏺✓✔✖|]\s"  # tool-trace glyphs
    r"|Shell cwd was reset\b"
    r")"
)


def clean_transcript(output: str) -> str:
    """Reduce a harness transcript to what the model actually said: strip
    ANSI escapes, then drop harness chrome — the rig banner, tool-trace lines,
    cwd-reset notices. Heuristic by design."""
    text = _ANSI_RE.sub("", output)
    kept = [
        line for line in text.splitlines()
        if not _HARNESS_NOISE_RE.match(line.strip())
    ]
    return "\n".join(kept).strip()


def _run_turn(
    ctx: DispatchContext,
    config: RigConfig,
    roster: Roster,
    member: Member,
    rig: Rig,
    run_fn,
) -> None:
    batch = state.claim_queue(ctx.node, member.name)
    if not batch:
        return
    newest_thread = str(batch[-1].get("task", "")) or "?"
    newest_hop = int(batch[-1].get("hop", 0) or 0)
    newest_sender = str(batch[-1].get("from", "")) or f"{ctx.node}"

    variant = state.take_rotation(ctx.node, rig.name, rig.pool_size)
    staging = state.prepare_staging(ctx.node, member.name)
    state.write_turn(
        ctx.node,
        member.name,
        {
            "batch": len(batch),
            "threads": sorted({str(b.get("task", "")) for b in batch}),
            "newest_sender": newest_sender,
            "rig": rig.name,
            "started": state.utc_now(),
        },
    )
    prompt = build_prompt(ctx, roster, member, batch)

    env = dict(os.environ)
    env["TELL_OUTBOX_DIR"] = str(staging)
    state.append_log(
        ctx.node,
        f"## {state.utc_now()} dispatch {len(batch)} message(s) -> {member.name} "
        f"(threads {', '.join(sorted({str(b.get('task', '')) for b in batch}))}, "
        f"rig {rig.name}"
        + (f" variant {variant}" if rig.pool_size > 1 else "")
        + f")\n\n### Prompt\n\n{prompt}",
    )

    exit_code, output, duration, timed_out = run_fn(
        rig, prompt, ctx.root, env=env, variant=variant
    )

    outcome = f"exit {exit_code} in {duration:.1f}s"
    if timed_out:
        outcome += f" (killed at timeout {rig.timeout_seconds:g}s)"
    state.append_log(
        ctx.node,
        f"### Output ({member.name}, {outcome})\n\n{output.strip() or '(no output)'}",
    )

    failed = timed_out or exit_code != 0
    if failed:
        # A failed turn releases nothing and returns its whole batch to the
        # queue: the messages are never lost, and the breaker accumulates
        # against repeated failures until it trips and the queue simply holds.
        shutil.rmtree(staging, ignore_errors=True)
        for env_msg in batch:
            state.enqueue(ctx.node, member.name, env_msg)
        state.append_log(
            ctx.node,
            f"r4t: RETRY {member.name.lower()} turn failed ({outcome}); "
            f"{len(batch)} message(s) returned to the queue",
        )
    else:
        for env_msg in batch:
            entry_body = str(env_msg.get("body", ""))
            if len(entry_body) > HISTORY_BODY_MAX:
                entry_body = entry_body[:HISTORY_BODY_MAX] + " [...]"
            state.append_history(
                ctx.node,
                member.name,
                f"## {state.utc_now()} from "
                f"{_display_name(ctx.node, str(env_msg.get('from', '?')))}\n\n{entry_body}",
            )
        if not state.staged_envelopes(ctx.node, member.name):
            # The classic weak-rig shape: the model answers on stdout instead
            # of running `tell`. `tell` always wins — a turn that staged
            # anything keeps its stdout as transcript — but a clean turn that
            # released nothing gets its cleaned stdout staged as ONE reply to
            # the newest message's sender, riding the normal release gates.
            reply = clean_transcript(output)
            if len(reply) > STDOUT_REPLY_MIN_CHARS:
                msg_id = tasks.new_task_id()
                state.atomic_write_json(
                    state.staging_dir(ctx.node, member.name) / f"{msg_id}.json",
                    {"id": msg_id, "to": newest_sender, "content": reply, "files": []},
                )
                state.append_log(
                    ctx.node,
                    f"r4t: STDOUT-REPLY {member.name.lower()} (rig {rig.name}) "
                    f"released nothing; {len(reply)} bytes of cleaned stdout "
                    f"staged as a reply to {newest_sender}",
                )
            elif len(output.strip()) > STDOUT_REPLY_MIN_CHARS:
                state.append_log(
                    ctx.node,
                    f"r4t: SILENT {member.name.lower()} (rig {rig.name}) exit 0 "
                    f"with {len(output.strip())} bytes of stdout but nothing "
                    "worth relaying survived transcript cleaning",
                )
        release_staging(ctx, config, roster, member, rig, batch)

    state.record_velocity(
        ctx.node,
        agent=member.name.lower(),
        rig=rig.name,
        task=newest_thread,
        hop=newest_hop,
        duration_seconds=duration,
        exit_code=exit_code,
    )
    completed = state.utc_now()
    failures = int(
        state.read_meta(ctx.node, member.name).get("consecutive_failures", 0) or 0
    )
    failures = failures + 1 if failed else 0
    meta_fields = {
        "last_completed_at": completed,
        "consecutive_failures": failures,
        "last_turn": {
            "threads": sorted({str(b.get("task", "")) for b in batch}),
            "messages": len(batch),
            "exit": exit_code,
            "timed_out": timed_out,
            "completed_at": completed,
        },
    }
    if failed:
        meta_fields["last_failure_at"] = completed
    state.update_meta(ctx.node, member.name, **meta_fields)
    state.clear_turn(ctx.node, member.name)
    if failed and failures == config.breaker_cap:
        state.append_log(
            ctx.node,
            f"r4t: BREAKER {member.name.lower()} tripped ({failures} consecutive "
            f"failed turns, rig {rig.name}) — turns pause; one probe per "
            f"{config.breaker_cooldown_seconds:g}s until a turn succeeds",
        )
    if exit_code == 127:
        _tell_error(
            ctx, newest_sender,
            f"{member.name}'s harness (rig {rig.name}) failed to start: "
            f"{output.strip()}",
        )


def _runnable(
    ctx: DispatchContext, config: RigConfig, member: Member, rig: Rig
) -> tuple[bool, str]:
    """Can this member start a turn right now? Returns (runnable, reason).
    The queue and everything else is untouched either way."""
    blocked, failures = state.breaker_open(
        ctx.node, member.name, config.breaker_cap, config.breaker_cooldown_seconds
    )
    if blocked:
        return False, (
            f"breaker open ({failures} consecutive failed turns)"
        )
    m = state.budget_level(ctx.node, member.name, rig.budget_max, rig.budget_earn_per_hour)
    t = state.budget_level(
        ctx.node, state.TEAM_BUDGET_KEY,
        config.team_budget_max, config.team_budget_earn_per_hour,
    )
    if m < 1.0:
        wait = state.budget_seconds_until(
            ctx.node, member.name, rig.budget_max, rig.budget_earn_per_hour
        )
        return False, f"resting (member budget {m:.1f}, ready in ~{wait / 60:.0f} min)"
    if t < 1.0:
        wait = state.budget_seconds_until(
            ctx.node, state.TEAM_BUDGET_KEY,
            config.team_budget_max, config.team_budget_earn_per_hour,
        )
        return False, f"resting (team budget {t:.1f}, ready in ~{wait / 60:.0f} min)"
    return True, ""


def _run_member_turn(
    ctx: DispatchContext,
    config: RigConfig,
    roster: Roster,
    member: Member,
    rig: Rig,
    run_fn,
) -> str:
    if state.queue_depth(ctx.node, member.name) == 0:
        return SKIPPED
    runnable, reason = _runnable(ctx, config, member, rig)
    if not runnable:
        state.append_log(
            ctx.node,
            f"r4t: {'BREAKER' if reason.startswith('breaker') else 'RESTING'} "
            f"{member.name.lower()} — {reason} "
            f"({state.queue_depth(ctx.node, member.name)} queued)",
        )
        return BREAKER if reason.startswith("breaker") else RESTING

    lock = state.AgentLock(ctx.node, member.name)
    admission = state.admission_lock(ctx.node)
    if not admission.acquire():
        return DEFERRED
    acquired = False
    try:
        throttle_reason = _throttle_block(ctx, config)
        if throttle_reason is None:
            acquired = lock.acquire(rig.name)
            if acquired and state.count_rig_locks(ctx.node, rig.name) > rig.concurrency:
                lock.release()
                acquired = False
        if acquired:
            # Re-read budgets under the admission lock so simultaneous
            # admissions cannot both spend the last unit.
            runnable, reason = _runnable(ctx, config, member, rig)
            if not runnable:
                lock.release()
                acquired = False
            else:
                state.budget_charge(
                    ctx.node, member.name, rig.budget_max, rig.budget_earn_per_hour
                )
                state.budget_charge(
                    ctx.node, state.TEAM_BUDGET_KEY,
                    config.team_budget_max, config.team_budget_earn_per_hour,
                )
                state.stamp_last_turn_start(ctx.node)
    finally:
        admission.release()

    if not acquired:
        if throttle_reason:
            state.append_log(
                ctx.node,
                f"r4t: DEFERRED ({throttle_reason}) {member.name.lower()} "
                f"({state.queue_depth(ctx.node, member.name)} queued)",
            )
        return RESTING if not runnable else DEFERRED

    try:
        _run_turn(ctx, config, roster, member, rig, run_fn)
        return RAN
    finally:
        lock.release()


# ---------- dispatch entry points ----------

def handle_message(
    ctx: DispatchContext,
    sender: str,
    to: str,
    message: str,
    *,
    run_fn=run_harness,
    drain_after: bool = True,
) -> int:
    _ingest(
        ctx, sender, to, message,
        trusted_header=_is_internal(ctx.node, sender),
    )
    if drain_after:
        drain_until_quiet(ctx, run_fn=run_fn)
    return 0


def resting_note(ctx: DispatchContext, to: str) -> str | None:
    """A one-line note for the seat when a deliberate send lands on a resting
    member — the human is never blocked from sending, but should know the turn
    is waiting on the bucket. None when the recipient will run normally."""
    _, sub = split_recipient(to)
    try:
        roster = load_roster(ctx.roster_path)
        config = load_rig_config(ctx.config_path)
    except (RosterError, RigError):
        return None
    member = roster.find(sub) if sub else roster.leader()
    if member is None or member.is_human or member.errors:
        return None
    rig, _err, _pinned = config.rig_for(member)
    if rig is None:
        return None
    depth = state.queue_depth(ctx.node, member.name)
    if depth == 0:
        return None
    runnable, reason = _runnable(ctx, config, member, rig)
    if runnable:
        return None
    return f"queued — {member.name} is {reason}"


# ---------- queue drain ----------

def drain(ctx: DispatchContext, *, run_fn=run_harness) -> int:
    """One pass over every member with a non-empty queue: run a batch turn for
    each runnable one. Returns the number of turns that RAN. The agent lock is
    the only claim — two concurrent drainers race on it and exactly one runs a
    given member; the loser's message stays safely queued."""
    try:
        roster = load_roster(ctx.roster_path)
        config = load_rig_config(ctx.config_path)
    except (RosterError, RigError):
        return 0
    ran = 0
    for name in state.members_with_queue(ctx.node):
        member = roster.find(name)
        if member is None or member.is_human or member.errors:
            continue
        rig, _err, _pinned = config.rig_for(member)
        if rig is None:
            continue
        if _run_member_turn(ctx, config, roster, member, rig, run_fn) == RAN:
            ran += 1
    return ran


def _cadence_wait(ctx: DispatchContext) -> float:
    """Seconds until the cadence throttle admits another turn start (0 when the
    window is already open or the config is unreadable)."""
    try:
        config = load_rig_config(ctx.config_path)
    except RigError:
        return 0.0
    interval = config.throttle.min_seconds_between_turn_starts
    if interval <= 0:
        return 0.0
    last = state.read_last_turn_start(ctx.node)
    if last is None:
        return 0.0
    return max(0.0, interval - (time.time() - last))


def drain_until_quiet(ctx: DispatchContext, *, run_fn=run_harness) -> int:
    """Drain repeatedly until a pass runs nothing — a released intra-team
    message enqueues the next member and can enable another turn in the same
    invocation. A pass that runs nothing while queued work remains and no turn
    is live means either the cadence window is the only thing in the way (sleep
    it out and retry) or every queued member is resting/broken (return; the
    queue holds until the bucket refills or the breaker closes)."""
    total = 0
    for _ in range(DRAIN_MAX_PASSES):
        ran = drain(ctx, run_fn=run_fn)
        total += ran
        if ran:
            continue
        if not state.members_with_queue(ctx.node) or state.live_locks(ctx.node):
            break
        wait = _cadence_wait(ctx)
        if wait <= 0:
            break
        time.sleep(wait + 0.05)
    return total


def run_clear(ctx: DispatchContext, older_than: float, *, run_fn=run_harness) -> dict:
    pruned = state.prune_stale_locks(ctx.node)
    expired = tasks.expire_tasks(ctx.node, older_than)
    drained = drain_until_quiet(ctx, run_fn=run_fn)
    return {"locks_pruned": pruned, "tasks_expired": expired, "drained": drained}


# ---------- quiet-thread sweep (the termination backstop) ----------

def _quiet_task_sweep(
    ctx: DispatchContext, config: RigConfig, roster: Roster
) -> list[str]:
    """A thread can go quiet with its originator never having heard back — a
    member's turn succeeds while staging no reply, or a chain stalls. When an
    open thread with an unanswered originator sees no ledger activity for
    `quiet_task_seconds`, wake the leader with a nudge to report current state
    (NOT to force-finish the work). Returns the threads nudged."""
    if config.quiet_task_seconds <= 0:
        return []
    if state.live_locks(ctx.node):
        return []
    leader = roster.leader()
    if leader is None or leader.errors:
        return []
    cutoff = time.time() - config.quiet_task_seconds
    nudged: list[str] = []
    for task in tasks.list_tasks(ctx.node):
        if task.get("status") != tasks.STATUS_OPEN or task.get("answered"):
            continue
        if tasks.last_activity(task) > cutoff:
            continue
        thread_id = str(task["id"])
        creator = str(task.get("creator", "?"))
        header = tasks.format_header(thread_id, 0, auto=True)
        body = (
            f"Thread {thread_id} has gone quiet and {creator} has not heard "
            "back. Reply to them with where things stand — what is done and "
            "what remains. You do not have to finish the work, just report "
            "current state."
        )
        _ingest(
            ctx, f"r4t:{ctx.node}", f"{ctx.node}:{leader.name.lower()}",
            f"{header} {body}", trusted_header=True, roster=roster, config=config,
        )
        tasks.save_task(ctx.node, task)  # bump updated_at; won't re-fire until quiet again
        state.append_log(
            ctx.node,
            f"r4t: QUIET thread={thread_id} quiet >{config.quiet_task_seconds:g}s "
            f"— nudged leader {leader.name.lower()} to update {creator}",
        )
        nudged.append(thread_id)
    return nudged


def run_idle(ctx: DispatchContext, *, run_fn=run_harness) -> dict:
    """One idle pass: nudge the leader about quiet unanswered threads, then
    drain every runnable member's queue. Crash recovery needs no special path
    — a turn that never completed left its messages in the queue (they were
    claimed only at a turn that ran), so the next runnable turn picks them up."""
    try:
        roster = load_roster(ctx.roster_path)
        config = load_rig_config(ctx.config_path)
    except (RosterError, RigError) as e:
        state.append_log(ctx.node, f"r4t: IDLE-SKIPPED {e}")
        return {"quiet_nudged": [], "drained": 0, "error": str(e)}
    nudged = _quiet_task_sweep(ctx, config, roster)
    drained = drain_until_quiet(ctx, run_fn=run_fn)
    return {"quiet_nudged": nudged, "drained": drained}
