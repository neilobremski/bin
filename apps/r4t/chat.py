"""r4t chat — the human seat for a team, in one window.

Interleaves three streams: messages delivered to the roster's human member,
r4t turn/event lines from the team's daily log, and router activity from any
`a8s run` child this process spawns. Typed lines go out as tells from the
human seat; slash commands inspect the team.

Deliberately transport-respecting: chat never imports a8s code. It reads the
same on-disk surfaces a8s documents (`~/.a8s/a8s.json`, pid files, the human
agent's file-proxy inbox), spawns `a8s run` for unhandled agents, and sends
via the `tell` CLI with TELL_OUTBOX_DIR pointed at the human agent's outbox.
The human agent must use a file-proxy definition (`"proxy": "file"`) so the
router delivers messages as JSON files instead of waking a CLI — chat checks
this at startup and prints the fix if not.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import state
from roster import Member, Roster, RosterError, load_roster

POLL_SECONDS = 0.5
RECENT_ON_START = 3


def a8s_home() -> Path:
    override = os.environ.get("A8S_HOME")
    return Path(override) if override else Path.home() / ".a8s"


def load_a8s_registry(home: Path) -> dict:
    path = home / "a8s.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"agents": {}, "aliases": {}, "namespaces": {}}
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"cannot read a8s registry at {path}: {e}") from e


def handler_pid(home: Path, agent: str) -> int | None:
    pid_file = home / "agents" / agent / "pid"
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    return pid


@dataclass
class Seat:
    node: str
    node_agent: str
    human: Member
    human_agent: str
    human_root: Path
    inbox_dir: Path
    outbox_dir: Path


def resolve_seat(
    registry: dict, node: str, roster: Roster
) -> tuple[Seat | None, list[str]]:
    """Map roster + a8s registry onto a chat seat, or explain what's missing.

    Returns (seat, problems); seat is None whenever problems is non-empty."""
    problems: list[str] = []
    agents = registry.get("agents", {})
    namespaces = {k.lower(): v for k, v in registry.get("namespaces", {}).items()}

    node_agent = namespaces.get(node.lower())
    if node_agent is None:
        problems.append(
            f"namespace {node!r} is not bound to an a8s agent"
            f" (try: a8s namespace {node} <agent>)"
        )
    elif node_agent not in agents:
        problems.append(
            f"namespace {node!r} points at unregistered agent {node_agent!r}"
            f" (try: a8s add {node_agent} <dir>)"
        )

    human = next((m for m in roster.members if m.is_human and m.address), None)
    if human is None:
        problems.append(
            "roster has no human member with an Address:"
            " — add one to ROSTER.md (Status: Human, Address: <a8s-agent>)"
        )
        return None, problems

    entry = None
    human_agent = None
    for name, e in agents.items():
        if name.lower() == human.address.lower():
            human_agent, entry = name, e
            break
    if entry is None:
        problems.append(
            f"human address {human.address!r} is not a registered a8s agent"
            f" (try: a8s add {human.address} <dir>)"
        )
        return None, problems

    definition_path = Path(entry.get("definition", ""))
    inbox_spec = None
    try:
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        problems.append(f"cannot read {human_agent}'s definition: {e}")
        definition = {}
    if definition and definition.get("proxy") != "file":
        problems.append(
            f"{human_agent}'s definition is not a file-proxy, so the router"
            f" would wake a CLI instead of delivering messages as files"
            f" (try: a8s define {human_agent}"
            f" $A8S_DIR/definitions/human.json)"
        )
    else:
        inbox_spec = definition.get("inbox_dir")

    if problems:
        return None, problems

    human_root = Path(entry["root"]).expanduser()
    raw = (inbox_spec or ".inbox").strip() or ".inbox"
    inbox = Path(raw).expanduser()
    if not inbox.is_absolute():
        inbox = human_root / inbox
    return (
        Seat(
            node=node,
            node_agent=node_agent,
            human=human,
            human_agent=human_agent,
            human_root=human_root,
            inbox_dir=inbox,
            outbox_dir=human_root / ".outbox",
        ),
        [],
    )


def render_envelope(envelope: dict) -> str:
    sender = envelope.get("from", "?")
    content = (envelope.get("content") or "").strip()
    files = [
        e.get("filename")
        for e in (envelope.get("files") or [])
        if e.get("filename")
    ]
    lines = content.splitlines() or ["(empty)"]
    out = [f"{sender}: {lines[0]}"]
    out.extend(f"    {line}" for line in lines[1:])
    if files:
        out.append(f"    [files: {', '.join(files)}]")
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


def format_tasks(node: str) -> list[str]:
    tasks_dir = state.team_dir(node) / "tasks"
    rows: list[str] = []
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            f"{t.get('id', path.stem)}  {t.get('status', '?'):8}"
            f"  turns={t.get('turns', 0)}"
            f"  used={t.get('used', 0):.2f}/{t.get('budget', 0):.2f}"
            f"  creator={t.get('creator', '?')}"
        )
    return rows or ["(no task ledgers)"]


def format_who(node: str, roster: Roster, home: Path, seat: Seat) -> list[str]:
    locks = {
        lock.get("name", "").lower(): lock
        for lock in state.live_locks(node, prune=False)
    }
    rows: list[str] = []
    for m in roster.members:
        if m.is_human:
            handled = handler_pid(home, m.address or "") if m.address else None
            via = f"address {m.address}" + (
                f", routed by PID {handled}" if handled else ", no router"
            )
            rows.append(f"{m.name:12} human   {via}")
            continue
        lock = locks.get(m.name.lower())
        status = f"ACTIVE (pid {lock.get('pid')})" if lock else "idle"
        leader = " leader" if m.leader else ""
        rows.append(f"{m.name:12} {m.rig or '?':7} {status}{leader}")
    node_pid = handler_pid(home, seat.node_agent)
    rows.append(
        f"{'[' + seat.node_agent + ']':12} node    "
        + (f"routed by PID {node_pid}" if node_pid else "NO ROUTER")
    )
    return rows


HELP = """\
/to <name|node>   set message target (bare node = leader)
/who              roster, live turn locks, router pids
/tasks            task ledgers for this team
/help             this help
/quit             leave (spawned routers are stopped)"""


class ChatSession:
    def __init__(self, seat: Seat, roster: Roster, *, home: Path | None = None):
        self.seat = seat
        self.roster = roster
        self.home = home or a8s_home()
        self.target = seat.node
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.children: list[subprocess.Popen] = []
        self.seen: set[str] = set()
        self.log_path: Path | None = None
        self.log_offset = 0
        self.quitting = False
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

    # ---------- plumbing ----------

    def ensure_routers(self) -> None:
        """Human seat always gets a dedicated router: a shared handler drains
        no inboxes while any wake is in flight, so replies to the human can
        sit undelivered behind a teammate's long turn. The node keeps an
        existing handler if it has one — its wakes are the team's business."""
        human, node = self.seat.human_agent, self.seat.node_agent
        human_holder = handler_pid(self.home, human)
        if human_holder:
            self.emit(
                "sys",
                f"{human}: taking over from PID {human_holder} for dedicated"
                f" delivery (restart that router after /quit if you still need it)",
            )
        self._spawn_router(human)
        if human != node:
            node_pid = handler_pid(self.home, node)
            if node_pid:
                self.emit("sys", f"{node}: already routed by PID {node_pid}")
            else:
                self._spawn_router(node)

    def _spawn_router(self, agent: str) -> None:
        child = subprocess.Popen(
            ["a8s", "run", agent],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        self.children.append(child)
        threading.Thread(
            target=self._pump_child, args=(agent, child), daemon=True
        ).start()
        self.emit("sys", f"{agent}: started router (PID {child.pid})")

    def _pump_child(self, agent: str, child: subprocess.Popen) -> None:
        assert child.stdout is not None
        for line in child.stdout:
            self.events.put(("act", line.rstrip()))
        if not self.quitting:
            self.events.put(("sys", f"{agent}: router exited ({child.returncode})"))

    def stop_children(self) -> None:
        self.quitting = True
        for child in self.children:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 5
        for child in self.children:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                child.kill()

    # ---------- polling ----------

    def poll_inbox(self, *, announce: bool = True) -> None:
        try:
            files = sorted(
                f for f in self.seat.inbox_dir.iterdir()
                if f.is_file() and f.name.endswith(".json")
            )
        except OSError:
            return
        for f in files:
            if f.name in self.seen:
                continue
            self.seen.add(f.name)
            if not announce:
                continue
            try:
                envelope = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            self.events.put(("in", render_envelope(envelope)))

    def seed_inbox(self) -> None:
        """Mark pre-existing messages seen, replaying only the newest few."""
        try:
            files = sorted(
                f for f in self.seat.inbox_dir.iterdir()
                if f.is_file() and f.name.endswith(".json")
            )
        except OSError:
            return
        for f in files[:-RECENT_ON_START] if RECENT_ON_START else files:
            self.seen.add(f.name)
        if files[-RECENT_ON_START:]:
            self.emit("sys", "recent messages:")
        self.poll_inbox()

    def poll_log(self) -> None:
        path = state.team_dir(self.seat.node) / "log" / (
            datetime.now().strftime("%Y-%m-%d") + ".md"
        )
        if path != self.log_path:
            self.log_path = path
            self.log_offset = path.stat().st_size if path.is_file() else 0
            return
        if not path.is_file():
            return
        size = path.stat().st_size
        if size <= self.log_offset:
            self.log_offset = min(self.log_offset, size)
            return
        with path.open("r", encoding="utf-8") as f:
            f.seek(self.log_offset)
            chunk = f.read()
            self.log_offset = f.tell()
        for line in chunk.splitlines():
            event = filter_log_line(line)
            if event:
                self.events.put(("act", event))

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
                arg = arg.strip().lower()
                if not arg or arg == self.seat.node:
                    self.target = self.seat.node
                elif any(
                    not m.is_human and m.name.lower() == arg
                    for m in self.roster.members
                ):
                    self.target = f"{self.seat.node}:{arg}"
                else:
                    self.emit("sys", f"no AI member named {arg!r} in the roster")
                    return True
                self.emit("sys", f"target: {self.target}")
            elif cmd == "/who":
                self.emit(
                    "sys",
                    "\n".join(
                        format_who(self.seat.node, self.roster, self.home, self.seat)
                    ),
                )
            elif cmd == "/tasks":
                self.emit("sys", "\n".join(format_tasks(self.seat.node)))
            else:
                self.emit("sys", f"unknown command {cmd!r} (/help)")
            return True
        self.send(line)
        return True

    def send(self, text: str) -> None:
        env = dict(os.environ)
        env["TELL_OUTBOX_DIR"] = str(self.seat.outbox_dir)
        proc = subprocess.run(
            ["tell", self.target, text],
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self.emit("sys", f"tell failed: {proc.stderr.strip() or proc.stdout.strip()}")
            return
        self.emit("you", f"you -> {self.target}: {text}")

    # ---------- main loop ----------

    def run(self) -> int:
        self.emit(
            "sys",
            f"chat: {self.seat.node} — you are {self.seat.human.name}"
            f" ({self.seat.human_agent}); messages go to {self.target}"
            f" (leader). /help for commands.",
        )
        self.ensure_routers()
        self.seed_inbox()
        self.poll_log()

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
                self.poll_inbox()
                self.poll_log()
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
            self.stop_children()
            if self.children:
                self.emit("sys", "stopped spawned routers; other routers untouched")


def run_chat(node: str, roster_path: Path) -> int:
    try:
        roster = load_roster(roster_path)
    except RosterError as e:
        print(f"r4t chat: {e}", file=sys.stderr)
        return 2
    home = a8s_home()
    try:
        registry = load_a8s_registry(home)
    except RuntimeError as e:
        print(f"r4t chat: {e}", file=sys.stderr)
        return 2
    seat, problems = resolve_seat(registry, node, roster)
    if seat is None:
        print("r4t chat: not ready:", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 2
    return ChatSession(seat, roster, home=home).run()
