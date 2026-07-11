"""Dispatch — handle one delivered a8s message for a team node.

Stateless per invocation: load state, govern, run one harness turn, exit.
The agent replies with the unmodified `tell` — dispatch points the harness
subprocess's $TELL_OUTBOX_DIR at a per-turn staging dir and releases the
staged envelopes afterwards: attribution (only this turn wrote there), the
task/hop header stamped mechanically, send quota, class marking, pair
suppression, then move into the node's real outbox (external) or the
pending queue (intra-team, which a8s would drop as a self-send).

Everything governs autonomously — no human gates. Suppressed, cut, and
excess messages dead-letter with an audit record; budget exhaustion closes
the task through one forced-synthesis leader turn. Mechanisms and prior
art: docs/governance.md.

Requeueing note: a8s trashes the inbox message BEFORE spawning the wake
subprocess and only logs its exit code (daemon.wake_once), so exiting
nonzero does NOT redeliver. Deferred messages (concurrency/throttle) park
in the team's pending/ dir and drain on later dispatch and idle passes.
"""
from __future__ import annotations

import errno
import json
import os
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
DEAD = "dead-letter"
SYNTHESIS = "synthesis"
SKIPPED = "skipped"


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
    `node:name`. Bare roster names canonicalize to internal form, humans
    (bare or prefixed) resolve to their real a8s address, and anything
    else — `chatroom`, external addresses, unknown names — passes through
    untouched."""
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
    if member.is_human and member.address:
        return member.address
    return f"{node}:{member.name.lower()}"


def _tell_error(ctx: DispatchContext, recipient: str, text: str) -> None:
    body = f"[r4t {ctx.node}] {text}"
    state.append_log(ctx.node, f"r4t: ERROR -> {recipient}: {text}")
    ctx.tell_fn(recipient, body)


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
            reach = f"tell {m.name.lower()}" if m.address else "(unreachable)"
            lines.append(f"    - {m.name} (Human, {reach}) — {m.role}".rstrip(" —"))
        elif not m.errors:
            lines.append(
                f"    - {m.name} (tell {m.name.lower()}) — {m.role}".rstrip(" —")
            )
    return lines


def build_prompt(
    ctx: DispatchContext,
    roster: Roster,
    member: Member,
    sender: str,
    body: str,
) -> str:
    history = state.read_history(ctx.node, member.name)
    if len(body) > PROMPT_BODY_MAX:
        body = body[:PROMPT_BODY_MAX] + "\n[... message truncated by r4t ...]"
    teammates = _teammate_lines(ctx, roster, member)
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
        "## Incoming message",
        f"From: {_display_name(ctx.node, sender)}",
        "",
        body or "(empty message)",
        "",
        "## How to work",
        "- This is one turn: you were woken for the message above, and your "
        "process ends when you finish. You will be woken again when replies "
        "arrive.",
        "- Never wait for a reply inside a turn. If you need work from "
        "teammates, message them and END your turn without answering the "
        "original request; when their replies wake you later, answer the "
        "person who asked once you have enough.",
        "- Send messages with the `tell` shell command (run it via your shell "
        "tool — printing it as text sends nothing):",
        f"    - reply to the sender: tell {_display_name(ctx.node, sender)} \"<message>\"",
        "    - a teammate: tell <name> \"<message>\". Teammates:",
        *(teammates or ["    - (none)"]),
        "    - group discussion: tell chatroom '#<room> <message>'",
        "- Never use `tell --sync` with teammates — it blocks your turn "
        "waiting for a reply that arrives by waking you instead.",
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
    stamped_content: str,
    sender_addr: str,
    task_id: str,
    next_hop: int,
    body: str,
    synthesis_response: bool,
) -> None:
    to = str(envelope.get("to", "")).strip()
    if _is_internal(ctx.node, to):
        state.park_pending(
            ctx.node,
            {
                "from": sender_addr,
                "to": to,
                "task": task_id,
                "hop": next_hop,
                "auto": True,
                "body": body,
                "synthesis_response": synthesis_response,
            },
        )
        bundle = staging / str(envelope.get("id", ""))
        if bundle.is_dir():
            shutil.rmtree(bundle, ignore_errors=True)
            state.append_log(
                ctx.node,
                f"r4t: WARN attachments dropped on intra-team route {sender_addr} -> {to}",
            )
        state.append_log(
            ctx.node,
            f"r4t: RELEASED-internal {sender_addr} -> {to} task={task_id} hop={next_hop}",
        )
        return
    envelope["content"] = stamped_content
    envelope["x_r4t_class"] = "auto"
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
        f"r4t: RELEASED {sender_addr} -> {to} task={task_id} hop={next_hop}",
    )


def release_staging(
    ctx: DispatchContext,
    config: RigConfig,
    roster: Roster,
    member: Member,
    rig: Rig,
    task_id: str,
    hop: int,
    *,
    bulk_source: str | None = None,
    synthesis_response: bool = False,
) -> dict:
    """Process the turn's staged envelopes in send order: quota, pair
    suppression, bulk once-per-window, header stamp + auto class mark,
    outbound history, then release (real outbox or intra-team pending).
    Returns {"released": n, "violations": n}."""
    staging = state.staging_dir(ctx.node, member.name)
    sender_addr = f"{ctx.node}:{member.name.lower()}"
    next_hop = hop + 1
    outbox = _real_outbox(ctx)
    released = 0
    violations = 0
    synthesis_available = synthesis_response
    synthesis_creator = str((tasks.load_task(ctx.node, task_id) or {}).get("creator", ""))
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
                ctx.node,
                reason="quota",
                sender=sender_addr,
                to=to,
                task=task_id,
                content=body,
            )
            state.append_log(
                ctx.node,
                f"r4t: QUOTA {sender_addr} -> {to} task={task_id} "
                f"(> max_sends_per_turn {rig.max_sends_per_turn})",
            )
            continue
        repeated, count = state.suppression_check(
            ctx.node,
            tasks.pair_key(sender_addr, to, body),
            config.suppression_window_seconds,
        )
        if repeated:
            path.unlink(missing_ok=True)
            violations += 1
            state.record_dead_letter(
                ctx.node,
                reason="pair-repeat",
                sender=sender_addr,
                to=to,
                task=task_id,
                content=body,
                count=count,
            )
            state.append_log(
                ctx.node,
                f"r4t: SUPPRESSED {sender_addr} -> {to} task={task_id} repeat={count}",
            )
            continue
        if bulk_source and to.lower() == bulk_source:
            repeated, count = state.suppression_check(
                ctx.node,
                tasks.pair_key(sender_addr, to, "", kind="bulk"),
                config.suppression_window_seconds,
            )
            if repeated:
                path.unlink(missing_ok=True)
                violations += 1
                state.record_dead_letter(
                    ctx.node,
                    reason="bulk-window",
                    sender=sender_addr,
                    to=to,
                    task=task_id,
                    content=body,
                    count=count,
                )
                state.append_log(
                    ctx.node,
                    f"r4t: BULK-WINDOW {sender_addr} -> {to} task={task_id} repeat={count}",
                )
                continue
        stamped = f"{tasks.format_header(task_id, next_hop, auto=True)} {body}"
        state.append_history(
            ctx.node,
            member.name,
            f"## {state.utc_now()} to {_display_name(ctx.node, to)}\n\n"
            + (body if len(body) <= HISTORY_BODY_MAX else body[:HISTORY_BODY_MAX] + " [...]"),
        )
        final_response = (
            synthesis_available
            and _is_internal(ctx.node, to)
            and to.lower() == synthesis_creator.lower()
        )
        _release_one(
            ctx, outbox, staging, envelope, stamped, sender_addr, task_id, next_hop,
            body, final_response,
        )
        path.unlink(missing_ok=True)
        synthesis_available = synthesis_available and not final_response
        released += 1
    shutil.rmtree(staging, ignore_errors=True)
    return {"released": released, "violations": violations}


# ---------- the turn ----------

def _run_turn(
    ctx: DispatchContext,
    config: RigConfig,
    roster: Roster,
    member: Member,
    rig: Rig,
    sender: str,
    body: str,
    task_id: str,
    hop: int,
    run_fn,
    *,
    bulk_source: str | None = None,
    synthesis_response: bool = False,
) -> None:
    prompt = build_prompt(ctx, roster, member, sender, body)
    variant = state.take_rotation(ctx.node, rig.name, rig.pool_size)
    staging = state.prepare_staging(ctx.node, member.name)
    state.write_turn(
        ctx.node,
        member.name,
        {
            "task": task_id,
            "hop": hop,
            "sender": sender,
            "body": body[:HISTORY_BODY_MAX],
            "rig": rig.name,
            "started": state.utc_now(),
        },
    )
    env = dict(os.environ)
    env["TELL_OUTBOX_DIR"] = str(staging)

    now = state.utc_now()
    entry_body = body if len(body) <= HISTORY_BODY_MAX else body[:HISTORY_BODY_MAX] + " [...]"
    state.append_history(ctx.node, member.name, f"## {now} from {_display_name(ctx.node, sender)}\n\n{entry_body}")
    state.update_meta(ctx.node, member.name, last_inbound_at=now)
    state.append_log(
        ctx.node,
        f"## {state.utc_now()} dispatch {sender} -> {member.name} "
        f"(task {task_id} hop {hop}, rig {rig.name}"
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

    release = release_staging(
        ctx, config, roster, member, rig, task_id, hop, bulk_source=bulk_source,
        synthesis_response=synthesis_response,
    )
    if release["violations"]:
        level = state.bucket_drain(
            ctx.node, member.name, float(release["violations"]), config.bucket_max
        )
        state.append_log(
            ctx.node,
            f"r4t: BUCKET {member.name.lower()} -{release['violations']} "
            f"-> {level:.1f}/{config.bucket_max:g}",
        )
    else:
        state.bucket_earn(ctx.node, member.name, config.bucket_earn_ratio, config.bucket_max)

    state.record_velocity(
        ctx.node,
        agent=member.name.lower(),
        rig=rig.name,
        task=task_id,
        hop=hop,
        duration_seconds=duration,
        exit_code=exit_code,
    )
    completed = state.utc_now()
    failed = timed_out or exit_code != 0
    failures = int(
        state.read_meta(ctx.node, member.name).get("consecutive_failures", 0) or 0
    )
    failures = failures + 1 if failed else 0
    meta_fields = {
        "last_completed_at": completed,
        "consecutive_failures": failures,
        "last_turn": {
            "task": task_id,
            "hop": hop,
            "sender": sender,
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
            ctx,
            sender,
            f"{member.name}'s harness (rig {rig.name}) failed to start: "
            f"{output.strip()}",
        )


# ---------- forced synthesis ----------

def _forced_synthesis(
    ctx: DispatchContext,
    config: RigConfig,
    roster: Roster,
    task: dict,
    run_fn,
    *,
    why: str,
) -> str:
    """Close the task through one leader turn: "respond to the originator
    with what you have." Replaces every parking/approval path — tasks always
    terminate in an answer (docs/governance.md §4)."""
    task_id = str(task["id"])
    ledger_lock = state.task_lock(ctx.node, task_id)
    if not ledger_lock.acquire():
        state.park_pending(ctx.node, {"synthesis": True, "task": task_id, "why": why})
        return DEFERRED
    try:
        task = tasks.load_task(ctx.node, task_id)
        if task is None:
            return SKIPPED
        if (
            task.get("status") == tasks.STATUS_OPEN
            and not task.get("synthesis_state")
        ):
            task["status"] = tasks.STATUS_CLOSED
            task["synthesis_state"] = "pending"
            tasks.save_task(ctx.node, task)
        if task.get("synthesis_state") != "pending":
            return SKIPPED
    finally:
        ledger_lock.release()

    leader = roster.leader()
    if leader is None or leader.errors:
        state.append_log(
            ctx.node,
            f"r4t: SYNTHESIS-SKIPPED task={task_id} ({why}; no usable leader)",
        )
        return SKIPPED
    rig, err, _pinned = config.rig_for(leader)
    if rig is None:
        state.append_log(
            ctx.node, f"r4t: SYNTHESIS-SKIPPED task={task_id} ({why}; {err})"
        )
        return SKIPPED
    lock = state.AgentLock(ctx.node, leader.name)
    if not lock.acquire(rig.name):
        state.park_pending(ctx.node, {"synthesis": True, "task": task_id, "why": why})
        return DEFERRED
    try:
        ledger_lock = state.task_lock(ctx.node, task_id)
        if not ledger_lock.acquire():
            state.park_pending(ctx.node, {"synthesis": True, "task": task_id, "why": why})
            return DEFERRED
        try:
            task = tasks.load_task(ctx.node, task_id)
            if task is None or task.get("synthesis_state") != "pending":
                return SKIPPED
            task["synthesis_state"] = "running"
            task["synthesized"] = True
            tasks.save_task(ctx.node, task)
        finally:
            ledger_lock.release()
        creator = str(task.get("creator", "")) or "unknown"
        body = (
            f"Task {task['id']} is closed: {why}. Respond NOW to {creator} "
            "with the best answer you can assemble from the conversation so "
            "far — summarize what was accomplished, what remains, and any "
            "results. Do not delegate further work."
        )
        state.append_log(
            ctx.node, f"r4t: SYNTHESIS task={task['id']} leader={leader.name.lower()} ({why})"
        )
        _run_turn(
            ctx, config, roster, leader, rig, creator, body,
            task["id"], int(task.get("turns", 0)), run_fn,
            synthesis_response=True,
        )
        ledger_lock = state.task_lock(ctx.node, task_id)
        if ledger_lock.acquire():
            try:
                completed = tasks.load_task(ctx.node, task_id)
                if completed is not None and completed.get("synthesis_state") == "running":
                    completed["synthesis_state"] = "done"
                    tasks.save_task(ctx.node, completed)
            finally:
                ledger_lock.release()
        return SYNTHESIS
    finally:
        lock.release()


# ---------- dispatch ----------

def _handle(
    ctx: DispatchContext,
    sender: str,
    to: str,
    message: str,
    *,
    run_fn=run_harness,
    synthesis_response: bool = False,
) -> str:
    _, sub = split_recipient(to)

    roster = _load_roster(ctx, sender)
    if roster is None:
        return SKIPPED

    if sub:
        member = roster.find(sub)
        if member is None:
            names = ", ".join(_dispatchable_names(roster)) or "(none)"
            _tell_error(
                ctx,
                sender,
                f"no team member named {sub!r}. Dispatchable members: {names}.",
            )
            return SKIPPED
    else:
        member = roster.leader()
        if member is None:
            names = ", ".join(_dispatchable_names(roster)) or "(none)"
            _tell_error(
                ctx,
                sender,
                "no leader is marked in the roster, so bare messages to "
                f"{ctx.node} have no recipient. Address a member directly "
                f"(members: {names}).",
            )
            return SKIPPED

    if member.is_human:
        reach = (
            f"reach them directly: tell {member.address} \"...\""
            if member.address
            else "they have no a8s address in the roster"
        )
        _tell_error(
            ctx,
            sender,
            f"{member.name} is Human — r4t never dispatches humans; {reach}.",
        )
        return SKIPPED

    if member.errors:
        _tell_error(
            ctx,
            sender,
            f"{member.name} is disabled by a roster problem: {member.error}. "
            f"Fix {ctx.roster_path.name} and resend.",
        )
        return SKIPPED

    config = _load_config(ctx, sender)
    if config is None:
        return SKIPPED
    rig, err, _pinned = config.rig_for(member)
    if rig is None:
        _tell_error(ctx, sender, f"{member.name} cannot run: {err}")
        return SKIPPED

    state.refresh_active(ctx.node, member.name, config.active_ttl_rotations)

    task_id, hop, auto, body = tasks.parse_header(message)
    had_header = task_id is not None
    if task_id is None:
        task_id = tasks.new_task_id()
        hop = 0

    envelope = {
        "from": sender, "to": to, "task": task_id, "hop": hop,
        "auto": auto or not had_header, "body": body,
        "synthesis_response": synthesis_response,
    }
    ledger_lock = state.task_lock(ctx.node, task_id)
    if not ledger_lock.acquire():
        state.park_pending(ctx.node, envelope)
        return DEFERRED
    deliberate_reset = False
    cut_recipient = ""
    try:
        task = tasks.ensure_task(ctx.node, task_id, sender)
        if had_header and not auto:
            deliberate_reset = tasks.reset_budget(task)
        accepted_synthesis = (
            synthesis_response
            and task.get("status") == tasks.STATUS_CLOSED
            and bool(task.get("synthesized"))
            and task.get("synthesis_state") in ("running", "done")
            and _is_internal(ctx.node, sender)
            and _is_internal(ctx.node, to)
            and to.strip().lower()
            == str(task.get("creator", "")).strip().lower()
        )
        early_outcome = ""
        if task.get("status") == tasks.STATUS_CLOSED and not accepted_synthesis:
            early_outcome = "closed"
        elif not accepted_synthesis and hop >= rig.hop_limit:
            early_outcome = "hop-cut"
            if not task.get("cut_notified"):
                task["cut_notified"] = True
                cut_recipient = str(task.get("creator", sender))
        tasks.save_task(ctx.node, task)
    finally:
        ledger_lock.release()

    if had_header and not auto:
        state.clear_failures(ctx.node, member.name)
    if deliberate_reset:
        state.append_log(
            ctx.node,
            f"r4t: DELIBERATE task={task_id} budget and breaker reset "
            f"(non-auto header from {sender})",
        )
    if early_outcome == "closed":
        state.record_dead_letter(
            ctx.node, reason="task-closed", sender=sender, to=to,
            task=task_id, content=body,
        )
        state.append_log(
            ctx.node, f"r4t: CLOSED task={task_id} {sender} -> {member.name.lower()}"
        )
        return DEAD
    if early_outcome == "hop-cut":
        state.record_dead_letter(
            ctx.node, reason="hop-cut", sender=sender, to=to,
            task=task_id, content=body,
        )
        state.append_log(
            ctx.node,
            f"r4t: CUT task={task_id} hop={hop} {sender} -> {member.name.lower()} "
            f"(rig {rig.name} hop_limit {rig.hop_limit})",
        )
        if cut_recipient:
            ctx.tell_fn(
                cut_recipient,
                f"[r4t {ctx.node}] task {task_id}: message chain cut at hop "
                f"{hop} (rig {rig.name} hop_limit {rig.hop_limit}). The last "
                f"message was {sender} -> {member.name} and was not dispatched.",
            )
        return DEAD

    bulk_source = sender.lower() if sender.lower() in config.rebroadcast_senders else None

    # Intra-team traffic was already suppression-checked at release (same
    # store, same key) — re-checking here would suppress every delivery.
    if (auto and not _is_internal(ctx.node, sender)) or bulk_source:
        repeated, count = state.suppression_check(
            ctx.node,
            tasks.pair_key(sender, to, body),
            config.suppression_window_seconds,
        )
        if repeated:
            state.record_dead_letter(
                ctx.node, reason="pair-repeat", sender=sender, to=to,
                task=task_id, content=body, count=count,
            )
            state.append_log(
                ctx.node,
                f"r4t: SUPPRESSED inbound {sender} -> {to} task={task_id} repeat={count}",
            )
            return DEAD

    breaker_blocked, failures = state.breaker_open(
        ctx.node, member.name, config.breaker_cap, config.breaker_cooldown_seconds
    )
    if breaker_blocked:
        now = state.utc_now()
        entry_body = body if len(body) <= HISTORY_BODY_MAX else body[:HISTORY_BODY_MAX] + " [...]"
        state.append_history(ctx.node, member.name, f"## {now} from {_display_name(ctx.node, sender)}\n\n{entry_body}")
        state.update_meta(ctx.node, member.name, last_inbound_at=now, last_completed_at=now)
        state.record_dead_letter(
            ctx.node, reason="breaker-open", sender=sender, to=to,
            task=task_id, content=body,
        )
        state.append_log(
            ctx.node,
            f"r4t: BREAKER {member.name.lower()} open ({failures} consecutive "
            f"failed turns, rig {rig.name}) — message from {sender} recorded "
            "to history only; closing the task through forced synthesis",
        )
        # The chain through this member is dead until a probe succeeds, so
        # the task terminates in an answer now instead of dangling (same
        # rule as budget exhaustion). A deliberate human message reopens
        # both the task budget and the breaker.
        task = tasks.ensure_task(ctx.node, task_id, sender)
        return _forced_synthesis(
            ctx, config, roster, task, run_fn,
            why=f"{member.name.lower()}'s failure breaker is open "
            f"({failures} consecutive failed turns)",
        )

    level = state.bucket_level(ctx.node, member.name, config.bucket_max)
    if state.bucket_muted(level, config.bucket_max):
        now = state.utc_now()
        entry_body = body if len(body) <= HISTORY_BODY_MAX else body[:HISTORY_BODY_MAX] + " [...]"
        state.append_history(ctx.node, member.name, f"## {now} from {_display_name(ctx.node, sender)}\n\n{entry_body}")
        state.update_meta(ctx.node, member.name, last_inbound_at=now, last_completed_at=now)
        state.bucket_earn(ctx.node, member.name, config.bucket_earn_ratio, config.bucket_max)
        state.record_dead_letter(
            ctx.node, reason="bucket-muted", sender=sender, to=to,
            task=task_id, content=body,
        )
        state.append_log(
            ctx.node,
            f"r4t: MUTED {member.name.lower()} (bucket {level:.1f}/{config.bucket_max:g}) "
            f"— message from {sender} recorded to history only",
        )
        return DEAD

    lock = state.AgentLock(ctx.node, member.name)
    admission = state.admission_lock(ctx.node)
    if not admission.acquire():
        state.park_pending(ctx.node, envelope)
        return DEFERRED
    task = None
    task_outcome = ""
    try:
        throttle_reason = _throttle_block(ctx, config)
        acquired = throttle_reason is None and lock.acquire(rig.name)
        rig_blocked = acquired and state.count_rig_locks(
            ctx.node, rig.name
        ) > rig.concurrency
        if rig_blocked:
            lock.release()
            acquired = False
        if acquired:
            ledger_lock = state.task_lock(ctx.node, task_id)
            if not ledger_lock.acquire():
                lock.release()
                acquired = False
                task_outcome = "task-busy"
            else:
                try:
                    task = tasks.ensure_task(ctx.node, task_id, sender)
                    accepted_synthesis = (
                        synthesis_response
                        and task.get("status") == tasks.STATUS_CLOSED
                        and bool(task.get("synthesized"))
                        and task.get("synthesis_state") in ("running", "done")
                        and _is_internal(ctx.node, sender)
                        and _is_internal(ctx.node, to)
                        and to.strip().lower()
                        == str(task.get("creator", "")).strip().lower()
                    )
                    if task.get("status") == tasks.STATUS_CLOSED and not accepted_synthesis:
                        task_outcome = "closed"
                    elif accepted_synthesis or tasks.charge_turn(
                        task, rig.max_turns_per_task
                    ):
                        task_outcome = "admitted"
                    else:
                        task_outcome = "exhausted"
                        task["status"] = tasks.STATUS_CLOSED
                        task["synthesis_state"] = "pending"
                    tasks.save_task(ctx.node, task)
                finally:
                    ledger_lock.release()
                if task_outcome == "admitted":
                    state.stamp_last_turn_start(ctx.node)
                else:
                    lock.release()
                    acquired = False
    finally:
        admission.release()

    if task_outcome == "closed":
        state.record_dead_letter(
            ctx.node, reason="task-closed", sender=sender, to=to,
            task=task_id, content=body,
        )
        state.append_log(
            ctx.node, f"r4t: CLOSED task={task_id} {sender} -> {member.name.lower()}"
        )
        return DEAD

    if task_outcome == "exhausted":
        state.record_dead_letter(
            ctx.node, reason="budget-exhausted", sender=sender, to=to,
            task=task_id, content=body,
        )
        state.append_log(
            ctx.node,
            f"r4t: BUDGET task={task_id} exhausted (used "
            f"{task.get('used', 0.0):.2f}/{task.get('budget', 1.0):.2f}) — "
            "closing through forced synthesis",
        )
        return _forced_synthesis(
            ctx, config, roster, task, run_fn, why="turn budget exhausted"
        )

    if throttle_reason:
        state.park_pending(ctx.node, envelope)
        state.append_log(
            ctx.node,
            f"r4t: DEFERRED ({throttle_reason}) {sender} -> {member.name.lower()} "
            f"task={task_id} hop={hop}",
        )
        return DEFERRED

    if rig_blocked:
        state.park_pending(ctx.node, envelope)
        state.append_log(
            ctx.node,
            f"r4t: DEFERRED (rig {rig.name} at concurrency {rig.concurrency}) "
            f"{sender} -> {member.name.lower()} task={task_id}",
        )
        return DEFERRED

    if task_outcome == "task-busy":
        state.park_pending(ctx.node, envelope)
        state.append_log(
            ctx.node,
            f"r4t: DEFERRED (task ledger busy) {sender} -> {member.name.lower()} "
            f"task={task_id} hop={hop}",
        )
        return DEFERRED

    if not acquired:
        state.park_pending(ctx.node, envelope)
        state.append_log(
            ctx.node,
            f"r4t: DEFERRED (agent busy) {sender} -> {member.name.lower()} "
            f"task={task_id} hop={hop}",
        )
        return DEFERRED

    try:
        _run_turn(
            ctx, config, roster, member, rig, sender, body, task_id, hop,
            run_fn, bulk_source=bulk_source,
        )
        return RAN
    finally:
        lock.release()


def handle_message(
    ctx: DispatchContext,
    sender: str,
    to: str,
    message: str,
    *,
    run_fn=run_harness,
) -> int:
    _handle(ctx, sender, to, message, run_fn=run_fn)
    return 0


# ---------- deferred-message drain ----------

def _redispatch(ctx: DispatchContext, envelope: dict, *, run_fn=run_harness) -> str:
    if envelope.get("synthesis"):
        task = tasks.load_task(ctx.node, str(envelope.get("task", "")))
        if task is None:
            return SKIPPED
        roster = _load_roster(ctx, task.get("creator", "unknown"))
        config = _load_config(ctx, task.get("creator", "unknown"))
        if roster is None or config is None:
            return SKIPPED
        return _forced_synthesis(
            ctx, config, roster, task, run_fn,
            why=str(envelope.get("why", "budget exhausted")),
        )
    body = envelope.get("body", "")
    task_id = envelope.get("task")
    hop = int(envelope.get("hop", 0) or 0)
    if task_id:
        header = tasks.format_header(
            str(task_id), hop, auto=bool(envelope.get("auto", False))
        )
        message = f"{header} {body}"
    else:
        message = body
    return _handle(
        ctx,
        envelope.get("from", "unknown"),
        envelope.get("to", ctx.node),
        message,
        run_fn=run_fn,
        synthesis_response=bool(envelope.get("synthesis_response", False)),
    )


def drain(ctx: DispatchContext, *, run_fn=run_harness) -> int:
    """One pass over the pending queue. Each file is consumed before
    redispatch so a re-defer creates a fresh entry instead of looping.
    Returns the number of turns that actually RAN."""
    ran = 0
    for path in state.list_pending(ctx.node):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        if isinstance(envelope, dict):
            if _redispatch(ctx, envelope, run_fn=run_fn) in (RAN, SYNTHESIS):
                ran += 1
    return ran


def drain_until_quiet(ctx: DispatchContext, *, run_fn=run_harness) -> int:
    """Drain repeatedly until a pass runs nothing — a released intra-team
    message can enable the next turn in the same invocation (the cadence
    throttle still spaces the starts; blocked messages simply re-defer)."""
    total = 0
    for _ in range(DRAIN_MAX_PASSES):
        ran = drain(ctx, run_fn=run_fn)
        total += ran
        if ran == 0:
            break
    return total


def run_clear(ctx: DispatchContext, older_than: float, *, run_fn=run_harness) -> dict:
    pruned = state.prune_stale_locks(ctx.node)
    expired = tasks.expire_tasks(ctx.node, older_than)
    drained = drain_until_quiet(ctx, run_fn=run_fn)
    return {"locks_pruned": pruned, "tasks_expired": expired, "drained": drained}


# ---------- idle-driven active list (governed crash recovery) ----------

def _collect_evidence(
    node: str, agent_key: str, active_entry: dict
) -> tuple[list[str], dict | None]:
    """Evidence of unfinished business for one active agent. Returns
    (human-readable lines for the nudge, identity dict carrying task/hop/
    sender to re-dispatch under)."""
    lines: list[str] = []
    identity: dict | None = None
    last_nudge = str(active_entry.get("last_nudge_at", ""))
    locked = any(l.get("agent") == agent_key for l in state.live_locks(node))

    turn = state.read_turn(node, agent_key)
    if turn is not None and not locked:
        lines.append(
            f'You received this message from {turn.get("sender", "?")} but your '
            f'turn did not complete: "{turn.get("body", "")}"'
        )
        identity = turn

    meta = state.read_meta(node, agent_key)
    last_turn = meta.get("last_turn") or {}
    completed = str(last_turn.get("completed_at", ""))
    failed = bool(last_turn.get("timed_out")) or int(last_turn.get("exit", 0) or 0) != 0
    if completed and completed > last_nudge and failed:
        how = (
            "timed out"
            if last_turn.get("timed_out")
            else f"exited {last_turn.get('exit')}"
        )
        lines.append(
            f"Your last turn (task {last_turn.get('task', '?')}, message from "
            f"{last_turn.get('sender', '?')}) {how} before finishing."
        )
        if identity is None:
            identity = last_turn

    if (
        not locked
        and turn is None
        and str(meta.get("last_inbound_at", "")) > str(meta.get("last_completed_at", ""))
    ):
        lines.append(
            "You received messages newer than your last completed turn — "
            "check your conversation history above."
        )

    return lines, identity


def _nudge_body(lines: list[str]) -> str:
    bullet = "\n".join(f"- {line}" for line in lines)
    return (
        "[r4t idle recovery] Your previous turn appears not to have "
        f"completed. Outstanding:\n{bullet}\n"
        "Pick up where you left off and reply to the people waiting on you."
    )


def run_idle(ctx: DispatchContext, *, run_fn=run_harness) -> dict:
    """One idle pass: re-wake active agents that show unfinished business
    (crashed/timed-out turn, stalled inbound); decrement ttl; drop at 0.
    Recovery is itself governed: at most `nudge_cap` nudges per agent per
    task, then the leader closes the task with what exists."""
    try:
        roster = load_roster(ctx.roster_path)
        config = load_rig_config(ctx.config_path)
    except (RosterError, RigError) as e:
        state.append_log(ctx.node, f"r4t: IDLE-SKIPPED {e}")
        return {"watched": 0, "nudged": [], "dropped": [], "error": str(e)}

    active = state.load_active(ctx.node)
    to_nudge: dict[str, tuple[list[str], dict | None]] = {}
    dropped: list[str] = []
    for agent_key, entry in list(active.items()):
        if not isinstance(entry, dict):
            del active[agent_key]
            continue
        member = roster.find(agent_key)
        if member is None or member.is_human or member.errors:
            del active[agent_key]
            dropped.append(agent_key)
            continue
        evidence, identity = _collect_evidence(ctx.node, agent_key, entry)
        if evidence:
            to_nudge[agent_key] = (evidence, identity)
        entry["ttl"] = int(entry.get("ttl", 0)) - 1
        if entry["ttl"] <= 0:
            del active[agent_key]
            dropped.append(agent_key)
    state.save_active(ctx.node, active)

    nudged: list[str] = []
    for agent_key, (evidence, identity) in to_nudge.items():
        identity = identity or {}
        task_id = str(identity.get("task", "")) or tasks.new_task_id()
        hop = int(identity.get("hop", 0) or 0)
        sender = str(identity.get("sender", "")) or "r4t"
        evidence_key = tasks.pair_key(
            agent_key,
            task_id,
            json.dumps({"evidence": evidence, "identity": identity}, sort_keys=True),
            kind="nudge",
        )
        ledger_lock = state.task_lock(ctx.node, task_id)
        if not ledger_lock.acquire():
            continue
        action = ""
        count = 0
        try:
            task = tasks.ensure_task(ctx.node, task_id, sender)
            nudges = task.get("nudges") or {}
            inflight = task.get("nudge_inflight") or {}
            count = int(nudges.get(agent_key, 0))
            if inflight.get(agent_key) == evidence_key:
                action = "duplicate"
            elif task.get("status") == tasks.STATUS_CLOSED:
                action = "closed"
            elif count >= config.nudge_cap:
                task["status"] = tasks.STATUS_CLOSED
                task["synthesis_state"] = "pending"
                action = "synthesis"
            else:
                nudges[agent_key] = count + 1
                inflight[agent_key] = evidence_key
                task["nudges"] = nudges
                task["nudge_inflight"] = inflight
                action = "nudge"
            tasks.save_task(ctx.node, task)
        finally:
            ledger_lock.release()

        if action == "synthesis":
            state.append_log(
                ctx.node,
                f"r4t: NUDGE-CAP task={task_id} agent={agent_key} "
                f"({count} >= {config.nudge_cap}) — closing through forced synthesis",
            )
            _forced_synthesis(
                ctx, config, roster, task, run_fn,
                why=f"recovery nudge cap reached for {agent_key}",
            )
            continue
        if action != "nudge":
            continue
        state.clear_turn(ctx.node, agent_key)
        state.append_log(
            ctx.node,
            f"r4t: NUDGE {agent_key} task={task_id} hop={hop} "
            f"({count + 1}/{config.nudge_cap}): " + "; ".join(evidence),
        )
        message = f"{tasks.format_header(task_id, hop, auto=True)} {_nudge_body(evidence)}"
        # Stamped BEFORE the nudge turn runs: a nudged turn that fails
        # completes after this stamp, so its failure stays visible as
        # evidence next pass and the nudge cap can actually be reached.
        state.mark_nudged(ctx.node, agent_key)
        try:
            _handle(ctx, sender, f"{ctx.node}:{agent_key}", message, run_fn=run_fn)
        finally:
            ledger_lock = state.task_lock(ctx.node, task_id)
            if ledger_lock.acquire():
                try:
                    current = tasks.load_task(ctx.node, task_id)
                    if current is not None:
                        inflight = current.get("nudge_inflight") or {}
                        if inflight.get(agent_key) == evidence_key:
                            del inflight[agent_key]
                            current["nudge_inflight"] = inflight
                            tasks.save_task(ctx.node, current)
                finally:
                    ledger_lock.release()
        nudged.append(agent_key)

    return {"watched": len(active) + len(dropped), "nudged": nudged, "dropped": dropped}
