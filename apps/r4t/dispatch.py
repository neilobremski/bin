"""Dispatch — handle one delivered a8s message for a team node.

Stateless per invocation: load state, govern, run one harness turn, exit.
Replies happen through the agent's own `tell` calls made during its run
(intercepted by the tell shim — see tellproxy.py); r4t only tells the
sender on errors and governance blocks.

Requeueing note: a8s trashes the inbox message BEFORE spawning the wake
subprocess and only logs its exit code (daemon.wake_once), so exiting
nonzero does NOT redeliver. Messages blocked on concurrency or the team
throttle are therefore parked in the team's local pending/ dir and drained
at the start of every dispatch and on every idle/clear pass.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import state
import tasks
from harness import HarnessConfig, HarnessError, Tier, load_harness_config
from notify import TellFn
from roster import Member, Roster, RosterError, load_roster

PROMPT_BODY_MAX = 4000
HISTORY_BODY_MAX = 2000
R4T_PY = Path(__file__).resolve().parent / "r4t.py"


@dataclass
class DispatchContext:
    root: Path
    node: str
    roster_path: Path
    config_path: Path
    tell_fn: TellFn
    tell_mode: str = "real"  # real | simulate | drop (what the tell shim does)


def split_recipient(to: str) -> tuple[str, str]:
    """`s1l:phil` -> (`s1l`, `phil`); bare `s1l` -> (`s1l`, `""`).
    The sub-address is everything after the FIRST colon, verbatim."""
    to = (to or "").strip()
    if ":" in to:
        node, sub = to.split(":", 1)
        return node.strip(), sub.strip()
    return to, ""


def _tell_error(ctx: DispatchContext, recipient: str, text: str) -> None:
    body = f"[r4t {ctx.node}] {text}"
    state.append_log(ctx.node, f"## {state.utc_now()} error -> {recipient}\n\n{body}")
    ctx.tell_fn(recipient, body)


def _load_roster(ctx: DispatchContext, sender: str) -> Roster | None:
    try:
        return load_roster(ctx.roster_path)
    except RosterError as e:
        _tell_error(ctx, sender, f"cannot dispatch: {e}")
        return None


def _load_config(ctx: DispatchContext, sender: str) -> HarnessConfig | None:
    try:
        return load_harness_config(ctx.config_path)
    except HarnessError as e:
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
            reach = f"tell {m.address}" if m.address else "(no a8s address)"
            lines.append(f"  - {m.name} (Human, {reach}) — {m.role}".rstrip(" —"))
        elif not m.errors:
            lines.append(
                f"  - {m.name} (tell {ctx.node}:{m.name.lower()}) — {m.role}".rstrip(" —")
            )
    return lines


def build_prompt(
    ctx: DispatchContext,
    roster: Roster,
    member: Member,
    sender: str,
    body: str,
    header: str,
) -> str:
    history = state.read_history(ctx.node, member.name)
    if len(body) > PROMPT_BODY_MAX:
        body = body[:PROMPT_BODY_MAX] + "\n[... message truncated by r4t ...]"
    teammates = _teammate_lines(ctx, roster, member)
    parts = [
        f"You are {member.name}, a member of the {ctx.node} team, working in "
        f"the team repo (your current directory).",
        "",
        "## Who you are (from the team roster)",
        member.persona or f"### {member.name}",
        "",
        "## Your conversation so far (messages you received and sent)",
        history.strip() or "(no prior messages — this is your first recorded turn)",
        "",
        "## Incoming message",
        f"From: {sender}",
        "",
        body or "(empty message)",
        "",
        "## How to communicate",
        f"- Reply to the sender with the `tell` shell command: tell {sender} \"<message>\"",
        f"- Message a teammate: tell {ctx.node}:<name> \"<message>\". Teammates:",
        *(teammates or ["  - (none)"]),
        "- Group discussion belongs in the chatroom: tell chatroom '#<room> <message>'",
        "- r4t stamps the task header on tells automatically. If you send a "
        "message any other way, start it with this exact header line, copied "
        "verbatim:",
        f"  {header}",
        "- Do not send acknowledgment-only messages. If you have nothing "
        "substantive to add, send nothing — silence is fine.",
        "- `tell` is a shell command: invoke it via your shell tool; never just "
        "print it as text.",
    ]
    return "\n".join(parts)


def run_harness(
    tier: Tier,
    prompt: str,
    cwd: Path,
    *,
    env: dict | None = None,
    variant: int = 0,
) -> tuple[int, str, float, bool]:
    """Run the tier's argv (pool variant `variant`) with {prompt} substituted
    as a single argv element — never a shell. Returns (exit_code, output,
    duration_seconds, timed_out)."""
    argv = tier.argv(prompt, variant)
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
        output, _ = proc.communicate(timeout=tier.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
        output, _ = proc.communicate()
    duration = time.monotonic() - start
    return proc.returncode, output or "", duration, timed_out


def _throttle_block(ctx: DispatchContext, config: HarnessConfig) -> str | None:
    """Team-wide gates, checked before any tier logic. Returns a reason
    string when the message must be parked."""
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


def _run_turn(
    ctx: DispatchContext,
    roster: Roster,
    member: Member,
    tier: Tier,
    sender: str,
    body: str,
    task_id: str,
    hop: int,
    run_fn,
) -> None:
    """The turn itself — governance already passed, agent lock held."""
    header = tasks.format_header(task_id, hop + 1)
    prompt = build_prompt(ctx, roster, member, sender, body, header)

    variant = state.take_rotation(ctx.node, tier.name, tier.pool_size)
    real_tell = shutil.which("tell") or ""
    turn_file = state.write_turn(
        ctx.node,
        member.name,
        {
            "task": task_id,
            "hop": hop,
            "sender": sender,
            "body": body[:HISTORY_BODY_MAX],
            "tier": tier.name,
            "sends_remaining": tier.max_sends_per_turn,
            "mode": ctx.tell_mode,
            "real_tell": real_tell,
            "started": state.utc_now(),
        },
    )
    shim = state.write_tell_shim(
        ctx.node, member.name, sys.executable, R4T_PY, turn_file
    )
    env = dict(os.environ)
    env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"

    now = state.utc_now()
    entry_body = body if len(body) <= HISTORY_BODY_MAX else body[:HISTORY_BODY_MAX] + " [...]"
    state.append_history(
        ctx.node, member.name, f"## {now} from {sender}\n\n{entry_body}"
    )
    state.update_meta(ctx.node, member.name, last_inbound_at=now)
    state.stamp_last_turn_start(ctx.node)
    state.append_log(
        ctx.node,
        f"## {state.utc_now()} dispatch {sender} -> {member.name} "
        f"(task {task_id} hop {hop}, tier {tier.name}"
        + (f" variant {variant}" if tier.pool_size > 1 else "")
        + f")\n\n### Prompt\n\n{prompt}",
    )

    exit_code, output, duration, timed_out = run_fn(
        tier, prompt, ctx.root, env=env, variant=variant
    )

    outcome = f"exit {exit_code} in {duration:.1f}s"
    if timed_out:
        outcome += f" (killed at timeout {tier.timeout_seconds:g}s)"
    state.append_log(
        ctx.node,
        f"### Output ({member.name}, {outcome})\n\n{output.strip() or '(no output)'}",
    )
    state.record_velocity(
        ctx.node,
        agent=member.name.lower(),
        tier=tier.name,
        task=task_id,
        hop=hop,
        duration_seconds=duration,
        exit_code=exit_code,
    )
    completed = state.utc_now()
    state.update_meta(
        ctx.node,
        member.name,
        last_completed_at=completed,
        last_turn={
            "task": task_id,
            "hop": hop,
            "sender": sender,
            "exit": exit_code,
            "timed_out": timed_out,
            "completed_at": completed,
        },
    )
    state.clear_turn(ctx.node, member.name)
    if exit_code == 127:
        _tell_error(
            ctx,
            sender,
            f"{member.name}'s harness (tier {tier.name}) failed to start: "
            f"{output.strip()}",
        )


def handle_message(
    ctx: DispatchContext,
    sender: str,
    to: str,
    message: str,
    *,
    run_fn=run_harness,
) -> int:
    _, sub = split_recipient(to)

    roster = _load_roster(ctx, sender)
    if roster is None:
        return 0

    if sub:
        member = roster.find(sub)
        if member is None:
            names = ", ".join(_dispatchable_names(roster)) or "(none)"
            _tell_error(
                ctx,
                sender,
                f"no team member named {sub!r}. Dispatchable members: {names}. "
                f"Address them as {ctx.node}:<name>.",
            )
            return 0
    else:
        member = roster.leader()
        if member is None:
            names = ", ".join(_dispatchable_names(roster)) or "(none)"
            _tell_error(
                ctx,
                sender,
                "no leader is marked in the roster, so bare messages to "
                f"{ctx.node} have no recipient. Address a member directly: "
                f"{ctx.node}:<name> (members: {names}).",
            )
            return 0

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
        return 0

    if member.errors:
        _tell_error(
            ctx,
            sender,
            f"{member.name} is disabled by a roster problem: {member.error}. "
            f"Fix {ctx.roster_path.name} and resend.",
        )
        return 0

    config = _load_config(ctx, sender)
    if config is None:
        return 0
    tier, err, _pinned = config.tier_for(member)
    if tier is None:
        _tell_error(ctx, sender, f"{member.name} cannot run: {err}")
        return 0

    state.refresh_active(ctx.node, member.name, config.active_ttl_rotations)

    task_id, hop, body = tasks.parse_header(message)
    if task_id is None:
        task_id = tasks.new_task_id()
        hop = 0
    task = tasks.ensure_task(ctx.node, task_id, sender)

    envelope = {"from": sender, "to": to, "task": task_id, "hop": hop, "body": body}

    if hop >= tier.hop_limit:
        state.append_log(
            ctx.node,
            f"## {state.utc_now()} chain cut: {sender} -> {member.name} "
            f"(task {task_id} hop {hop} >= hop_limit {tier.hop_limit} of "
            f"tier {tier.name})",
        )
        if not task.get("cut_notified"):
            task["cut_notified"] = True
            tasks.save_task(ctx.node, task)
            ctx.tell_fn(
                task.get("creator", sender),
                f"[r4t {ctx.node}] task {task_id}: message chain cut at hop "
                f"{hop} (tier {tier.name} hop_limit {tier.hop_limit}). The last "
                f"message was {sender} -> {member.name} and was not dispatched.",
            )
        return 0

    throttle_reason = _throttle_block(ctx, config)
    if throttle_reason:
        state.park_pending(ctx.node, envelope)
        state.append_log(
            ctx.node,
            f"## {state.utc_now()} parked ({throttle_reason}): {sender} -> "
            f"{member.name} (task {task_id} hop {hop})",
        )
        return 0

    lock = state.AgentLock(ctx.node, member.name)
    if not lock.acquire(tier.name):
        state.park_pending(ctx.node, envelope)
        state.append_log(
            ctx.node,
            f"## {state.utc_now()} parked (agent busy): {sender} -> "
            f"{member.name} (task {task_id} hop {hop})",
        )
        return 0

    try:
        if state.count_tier_locks(ctx.node, tier.name) > tier.concurrency:
            lock.release()
            state.park_pending(ctx.node, envelope)
            state.append_log(
                ctx.node,
                f"## {state.utc_now()} parked (tier {tier.name} at concurrency "
                f"{tier.concurrency}): {sender} -> {member.name} (task {task_id})",
            )
            return 0

        task = tasks.ensure_task(ctx.node, task_id, sender)
        if not tasks.charge_turn(task, tier.max_turns_per_task):
            lock.release()
            task["status"] = tasks.STATUS_PARKED
            task["parked_tier_max"] = tier.max_turns_per_task
            tasks.park_message(ctx.node, task_id, envelope)
            notify_creator = not task.get("park_notified")
            if notify_creator:
                task["park_notified"] = True
            tasks.save_task(ctx.node, task)
            state.append_log(
                ctx.node,
                f"## {state.utc_now()} parked (turn budget exhausted): {sender} "
                f"-> {member.name} (task {task_id}, used "
                f"{task.get('used', 0.0):.2f}/{task.get('budget', 1.0):.2f})",
            )
            if notify_creator:
                ctx.tell_fn(
                    task.get("creator", sender),
                    f"[r4t {ctx.node}] task {task_id} has exhausted its turn "
                    f"budget; further messages are parked. Approve more turns "
                    f"with: r4t task approve {task_id} --turns N --node {ctx.node}",
                )
            return 0
        tasks.save_task(ctx.node, task)

        _run_turn(ctx, roster, member, tier, sender, body, task_id, hop, run_fn)
        return 0
    finally:
        lock.release()


# ---------- parked-message drain ----------

def _redispatch(ctx: DispatchContext, envelope: dict, *, run_fn=run_harness) -> None:
    body = envelope.get("body", "")
    task_id = envelope.get("task")
    hop = int(envelope.get("hop", 0) or 0)
    message = f"{tasks.format_header(task_id, hop)} {body}" if task_id else body
    handle_message(
        ctx,
        envelope.get("from", "unknown"),
        envelope.get("to", ctx.node),
        message,
        run_fn=run_fn,
    )


def drain(ctx: DispatchContext, *, run_fn=run_harness) -> int:
    """Dispatch parked messages: the concurrency/throttle pending/ queue plus
    the parked/ dir of every open (approved) task. Each file is consumed
    before redispatch so a re-park creates a fresh entry instead of looping."""
    import json

    dispatched = 0
    for path in state.list_pending(ctx.node):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        if isinstance(envelope, dict):
            _redispatch(ctx, envelope, run_fn=run_fn)
            dispatched += 1

    for task in tasks.list_tasks(ctx.node):
        if task.get("status") != tasks.STATUS_OPEN:
            continue
        for path in tasks.parked_messages(ctx.node, task["id"]):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            path.unlink(missing_ok=True)
            if isinstance(envelope, dict):
                _redispatch(ctx, envelope, run_fn=run_fn)
                dispatched += 1
    return dispatched


def run_clear(ctx: DispatchContext, older_than: float, *, run_fn=run_harness) -> dict:
    pruned = state.prune_stale_locks(ctx.node)
    expired = tasks.expire_tasks(ctx.node, older_than)
    drained = drain(ctx, run_fn=run_fn)
    return {"locks_pruned": pruned, "tasks_expired": expired, "drained": drained}


# ---------- idle-driven active list (crash recovery) ----------

def _collect_evidence(
    node: str, agent_key: str, active_entry: dict
) -> tuple[list[str], dict | None]:
    """Evidence of unfinished business for one active agent. Returns
    (human-readable lines for the nudge, identity source dict or None).
    The identity source carries task/hop/sender to re-dispatch under."""
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

    suffix = f":{agent_key}"
    parked_new = 0
    for task in tasks.list_tasks(node):
        for path in tasks.parked_messages(node, task["id"]):
            try:
                import json

                envelope = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not str(envelope.get("to", "")).lower().endswith(suffix):
                continue
            if str(envelope.get("queued_at", "")) > last_nudge:
                parked_new += 1
    if parked_new:
        lines.append(
            f"{parked_new} message(s) addressed to you are parked awaiting "
            "turn-budget approval; the task creator has been asked to approve."
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
    """One idle pass: for every agent on the active list, re-wake it with a
    nudge if it shows unfinished business (crashed/timed-out turn, stalled
    inbound, freshly parked messages); decrement ttl; drop at 0. A nudged
    agent's turn goes through the normal handle_message governance, which
    also re-refreshes its active entry."""
    try:
        roster = load_roster(ctx.roster_path)
    except RosterError as e:
        state.append_log(ctx.node, f"## {state.utc_now()} idle skipped: {e}")
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
        state.clear_turn(ctx.node, agent_key)  # consumed as evidence above
        state.append_log(
            ctx.node,
            f"## {state.utc_now()} idle nudge -> {agent_key} "
            f"(task {task_id} hop {hop}): "
            + "; ".join(evidence),
        )
        message = f"{tasks.format_header(task_id, hop)} {_nudge_body(evidence)}"
        handle_message(ctx, sender, f"{ctx.node}:{agent_key}", message, run_fn=run_fn)
        state.mark_nudged(ctx.node, agent_key)
        nudged.append(agent_key)

    return {"watched": len(active) + len(dropped), "nudged": nudged, "dropped": dropped}
