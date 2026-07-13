"""r4t chat — the roster human's seat at the team, in one window.

Interleaves the seat mailbox (messages parked for the human by dispatch),
r4t turn/event lines from the team's daily log, and an input line that
speaks as the human. There is no a8s coupling: intra-team mail rides the
node's own pending queue, sends invoke dispatch directly, and a presence
file tells dispatch to skip the `Address:` doorbell while a session is
attached. The walled garden works with zero routers; a8s only matters for
traffic crossing the wall.

`r4t seat` exposes the same mailbox and voice as discrete commands — that
is the surface for orchestrators impersonating the human; chat is the
human-facing view over it. This module is the line UI plus the shared
seat machinery (SeatFeed, sending, target resolution); chat_tui.py is the
Textual front end over the same pieces.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import state
import tasks as taskmod
from dispatch import DispatchContext, drain_until_quiet, handle_message
from roster import Member, Roster, RosterError, load_roster

POLL_SECONDS = 0.5


class SeatError(Exception):
    pass


def load_seat(ctx: DispatchContext) -> tuple[Roster, Member]:
    try:
        roster = load_roster(ctx.roster_path)
    except RosterError as e:
        raise SeatError(str(e)) from e
    human = next((m for m in roster.members if m.is_human), None)
    if human is None:
        raise SeatError(
            "no human member in the roster — add one to ROSTER.md (Status: Human)"
        )
    return roster, human


def sender_label(roster: Roster, sender: str) -> str:
    """Decorate an envelope sender with its rig slug — `d5n:vela (specialist)`
    — so the human sees which capability answered. Non-member senders
    (external agents, the seat) render unchanged."""
    member = roster.find(sender.split(":")[-1])
    if member is not None and not member.is_human and member.rig:
        return f"{sender} ({member.rig})"
    return sender


def render_envelope(envelope: dict, roster: Roster | None = None) -> str:
    sender = str(envelope.get("from", "?"))
    if roster is not None:
        sender = sender_label(roster, sender)
    _, _, _, body = taskmod.parse_header(str(envelope.get("content", "")))
    lines = body.strip().splitlines() or ["(empty)"]
    out = [f"{sender}: {lines[0]}"]
    out.extend(f"    {line}" for line in lines[1:])
    return "\n".join(out)


def filter_log_line(line: str) -> str | None:
    """Compact one team-log line into an activity event, or None to skip.

    The daily log interleaves single-line events with full multi-line turn
    transcripts; chat shows the events and the turn boundaries, never the
    transcript bodies."""
    if line.startswith("r4t: "):
        return line
    if line.startswith("## ") and " dispatch " in line:
        _, _, rest = line.partition(" dispatch ")
        return f"turn: {rest}"
    if line.startswith("### Output ("):
        return f"done: {line[len('### Output ('):].rstrip(')')}"
    return None


def resolve_target(roster: Roster, node: str, arg: str) -> str | None:
    """A /to argument as a dispatchable address: bare team name = leader,
    a member name = that member; None when nothing in the roster matches."""
    arg = arg.strip().lower()
    if not arg or arg == node:
        return node
    if any(not m.is_human and m.name.lower() == arg for m in roster.members):
        return f"{node}:{arg}"
    return None


def send_as_human(ctx: DispatchContext, human: Member, to: str, text: str) -> str | None:
    """Speak as the seat: enqueue the message, then run the recipient's turn
    synchronously (and drain the fallout). Blocks for the whole exchange —
    callers own threading. Returns a note when the recipient is resting (the
    message is safely queued and runs when the bucket refills), else None."""
    from dispatch import resting_note

    sender = f"{ctx.node}:{human.name.lower()}"
    handle_message(ctx, sender, to, text)
    return resting_note(ctx, to)


class SeatFeed:
    """Polls the seat inbox and the team's daily log into (kind, payload)
    events: 'in' = the raw envelope dict parked for the human (marked read
    on read) — consumers render it (the line UI flattens, the TUI draws
    markdown); 'act' = compacted activity line as text. Log history before
    the first poll is skipped; unread inbox backlog is always delivered."""

    def __init__(self, node: str, human_name: str):
        self.node = node
        self.human_name = human_name
        self.log_path = None
        self.log_offset = 0

    def poll_inbox(self) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        for path in state.list_seat_messages(self.node, self.human_name):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            events.append(("in", envelope))
            state.mark_seat_read(self.node, self.human_name, path)
        return events

    def poll_log(self) -> list[tuple[str, str]]:
        # append_log names day files by UTC date — matching local time here
        # would watch a file that stops receiving writes after UTC midnight.
        path = state.team_dir(self.node) / "log" / (
            datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".md"
        )
        if path != self.log_path:
            self.log_path = path
            self.log_offset = path.stat().st_size if path.is_file() else 0
            return []
        if not path.is_file():
            return []
        size = path.stat().st_size
        if size <= self.log_offset:
            self.log_offset = min(self.log_offset, size)
            return []
        with path.open("r", encoding="utf-8") as f:
            f.seek(self.log_offset)
            chunk = f.read()
            self.log_offset = f.tell()
        events: list[tuple[str, str]] = []
        for line in chunk.splitlines():
            event = filter_log_line(line)
            if event:
                events.append(("act", event))
        return events

    def poll(self) -> list[tuple[str, object]]:
        return [*self.poll_inbox(), *self.poll_log()]


def _thread_age(task: dict) -> str:
    """Compact age of a thread from its creation stamp (e.g. 12s, 4m, 2h)."""
    try:
        created = datetime.fromisoformat(
            str(task.get("created_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return "?"
    secs = max(0, int(datetime.now(timezone.utc).timestamp() - created.timestamp()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def format_threads(node: str) -> list[str]:
    rows: list[str] = []
    for t in taskmod.list_tasks(node):
        if t.get("status") != taskmod.STATUS_OPEN:
            continue
        short = str(t.get("id", "?"))[-8:]
        rows.append(
            f"{short}  creator={str(t.get('creator', '?')):12}  {_thread_age(t)}"
        )
    return rows or ["(no open threads)"]


def format_who(node: str, roster: Roster, human: Member) -> list[str]:
    locks = {
        lock.get("name", "").lower(): lock
        for lock in state.live_locks(node, prune=False)
    }
    rows: list[str] = []
    for m in roster.members:
        if m.is_human:
            seat = "you" if m.name == human.name else "human"
            bell = f", doorbell {m.address}" if m.address else ""
            rows.append(f"{m.name:12} human   ({seat}{bell})")
            continue
        lock = locks.get(m.name.lower())
        status = f"ACTIVE (pid {lock.get('pid')})" if lock else "idle"
        leader = " leader" if m.leader else ""
        rows.append(f"{m.name:12} {m.rig or '?':7} {status}{leader}")
    return rows


HELP = """\
/to <name|team>   set message target (bare team = leader)
/who              roster and live turn locks
/threads          open threads for this team
/help             this help
/quit             leave the seat"""


@dataclass
class CommandResult:
    """Outcome of a shared /command: system lines both surfaces display, plus
    any state change the surface applies itself (quit, adopt a new target)."""

    lines: list[str] = field(default_factory=list)
    quit: bool = False
    target: str | None = None


def handle_command(roster: Roster, node: str, human: Member, line: str) -> CommandResult:
    """Parse one slash command shared by the line UI and the TUI. Unknown
    commands and /to misses come back as displayable lines, never silence."""
    cmd, _, arg = line.partition(" ")
    cmd = cmd.lower()
    if cmd == "/quit":
        return CommandResult(quit=True)
    if cmd == "/help":
        return CommandResult(lines=HELP.splitlines())
    if cmd == "/who":
        return CommandResult(lines=format_who(node, roster, human))
    if cmd == "/threads":
        return CommandResult(lines=format_threads(node))
    if cmd == "/to":
        target = resolve_target(roster, node, arg)
        if target is None:
            return CommandResult(
                lines=[f"no AI member named {arg.strip().lower()!r} in the roster"]
            )
        return CommandResult(lines=[f"target: {target}"], target=target)
    return CommandResult(lines=[f"unknown command: {cmd} (try /help)"])


class ChatSession:
    def __init__(self, ctx: DispatchContext, roster: Roster, human: Member):
        self.ctx = ctx
        self.roster = roster
        self.human = human
        self.target = ctx.node
        self.feed = SeatFeed(ctx.node, human.name)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.sends: queue.Queue[tuple[str, str]] = queue.Queue()
        self.tty = sys.stdout.isatty()

    # ---------- output ----------

    _STYLE = {"in": "\x1b[36m", "you": "\x1b[32m", "sys": "\x1b[33m", "act": "\x1b[2m"}

    def emit(self, kind: str, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in text.splitlines():
            if self.tty:
                print(f"\x1b[2m{stamp}\x1b[0m {self._STYLE[kind]}{line}\x1b[0m")
            else:
                print(f"{stamp} {line}")
        sys.stdout.flush()

    # ---------- sending ----------

    def _send_worker(self) -> None:
        while True:
            to, text = self.sends.get()
            try:
                note = send_as_human(self.ctx, self.human, to, text)
                if note:
                    self.events.put(("sys", note))
            except Exception as e:  # noqa: BLE001 — surface, don't kill the seat
                self.events.put(("sys", f"send failed: {e}"))

    # ---------- input ----------

    def _read_stdin(self, lines: queue.Queue[str]) -> None:
        for line in sys.stdin:
            lines.put(line.rstrip("\n"))
        lines.put("/quit")

    def handle_line(self, line: str) -> bool:
        """Returns False when the session should end."""
        line = line.strip()
        if not line:
            return True
        if line.startswith("/"):
            result = handle_command(self.roster, self.ctx.node, self.human, line)
            if result.quit:
                return False
            if result.target is not None:
                self.target = result.target
            if result.lines:
                self.emit("sys", "\n".join(result.lines))
            return True
        self.sends.put((self.target, line))
        self.emit("you", f"you -> {self.target}: {line}")
        return True

    # ---------- main loop ----------

    def _pump_feed(self) -> None:
        for kind, payload in self.feed.poll():
            text = render_envelope(payload, self.roster) if kind == "in" else payload
            self.events.put((kind, text))

    def run(self) -> int:
        self.emit(
            "sys",
            f"seat: {self.human.name} on {self.ctx.node} — messages go to"
            f" {self.target} (leader). /help for commands.",
        )
        state.touch_seat_presence(self.ctx.node, self.human.name)
        self._pump_feed()

        threading.Thread(target=self._send_worker, daemon=True).start()
        lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._read_stdin, args=(lines,), daemon=True).start()
        try:
            while True:
                try:
                    while True:
                        if not self.handle_line(lines.get_nowait()):
                            return 0
                except queue.Empty:
                    pass
                state.touch_seat_presence(self.ctx.node, self.human.name)
                self._pump_feed()
                try:
                    while True:
                        kind, text = self.events.get_nowait()
                        self.emit(kind, text)
                except queue.Empty:
                    pass
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            return 0
        finally:
            state.clear_seat_presence(self.ctx.node, self.human.name)
            self.emit("sys", "seat detached — mail parks and the doorbell rings again")


def run_chat(ctx: DispatchContext) -> int:
    try:
        roster, human = load_seat(ctx)
    except SeatError as e:
        print(f"r4t chat: {e}", file=sys.stderr)
        return 2
    return ChatSession(ctx, roster, human).run()
