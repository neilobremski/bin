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


def render_envelope(envelope: dict) -> str:
    sender = envelope.get("from", "?")
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


def send_as_human(ctx: DispatchContext, human: Member, to: str, text: str) -> None:
    """Speak as the seat: drain anything parked, run the recipient's turn
    synchronously, then drain the fallout. Blocks for the whole exchange —
    callers own threading."""
    sender = f"{ctx.node}:{human.name.lower()}"
    drain_until_quiet(ctx)
    handle_message(ctx, sender, to, text)
    drain_until_quiet(ctx)


class SeatFeed:
    """Polls the seat inbox and the team's daily log into (kind, text)
    events: 'in' = message parked for the human (marked read on read),
    'act' = compacted activity line. Log history before the first poll is
    skipped; unread inbox backlog is always delivered."""

    def __init__(self, node: str, human_name: str):
        self.node = node
        self.human_name = human_name
        self.log_path = None
        self.log_offset = 0

    def poll_inbox(self) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        for path in state.list_seat_messages(self.node, self.human_name):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            events.append(("in", render_envelope(envelope)))
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

    def poll(self) -> list[tuple[str, str]]:
        return self.poll_inbox() + self.poll_log()


def format_tasks(node: str) -> list[str]:
    rows: list[str] = []
    for t in taskmod.list_tasks(node):
        rows.append(
            f"{t.get('id', '?')}  {t.get('status', '?'):8}"
            f"  turns={t.get('turns', 0)}"
            f"  used={t.get('used', 0):.2f}/{t.get('budget', 0):.2f}"
            f"  creator={t.get('creator', '?')}"
        )
    return rows or ["(no open tasks)"]


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
/tasks            task ledgers for this team
/help             this help
/quit             leave the seat"""


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
                send_as_human(self.ctx, self.human, to, text)
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
            cmd, _, arg = line.partition(" ")
            cmd = cmd.lower()
            if cmd == "/quit":
                return False
            if cmd == "/help":
                self.emit("sys", HELP)
            elif cmd == "/to":
                target = resolve_target(self.roster, self.ctx.node, arg)
                if target is None:
                    self.emit(
                        "sys", f"no AI member named {arg.strip().lower()!r} in the roster"
                    )
                    return True
                self.target = target
                self.emit("sys", f"target: {self.target}")
            elif cmd == "/who":
                self.emit(
                    "sys",
                    "\n".join(format_who(self.ctx.node, self.roster, self.human)),
                )
            elif cmd == "/tasks":
                self.emit("sys", "\n".join(format_tasks(self.ctx.node)))
            else:
                self.emit("sys", f"unknown command {cmd!r} (/help)")
            return True
        self.sends.put((self.target, line))
        self.emit("you", f"you -> {self.target}: {line}")
        return True

    # ---------- main loop ----------

    def run(self) -> int:
        self.emit(
            "sys",
            f"seat: {self.human.name} on {self.ctx.node} — messages go to"
            f" {self.target} (leader). /help for commands.",
        )
        state.touch_seat_presence(self.ctx.node, self.human.name)
        for event in self.feed.poll():
            self.events.put(event)

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
                for event in self.feed.poll():
                    self.events.put(event)
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
