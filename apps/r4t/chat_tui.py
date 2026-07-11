"""r4t chat — Textual front end over the seat.

One window, three regions: a health header fed by the same verdict engine
as `r4t status` (worst-level summary, non-ok verdicts, open-task budget
bars), a conversation pane (seat messages and what you say) beside a
fly-on-the-wall activity pane (turn starts/completions and every
governance decision line), and an input line that speaks as the roster
human. Sends run on a background thread — dispatch turns are synchronous
and slow, and the wall must keep scrolling while a rig thinks.

Importing this module requires textual; cmd_chat falls back to the line
UI in chat.py when it is missing.
"""
from __future__ import annotations

import queue
import sys
import threading
from datetime import datetime

from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Input, RichLog, Static

import state
import tasks as taskmod
import verdict
from chat import (
    HELP,
    SeatError,
    SeatFeed,
    format_tasks,
    format_who,
    load_seat,
    resolve_target,
    send_as_human,
)
from dispatch import DispatchContext
from rig import RigError, load_rig_config
from roster import Member, Roster

FEED_POLL_SECONDS = 0.5
HEADER_REFRESH_SECONDS = 5.0
MAX_HEADER_VERDICTS = 4
MAX_HEADER_TASKS = 3

KIND_STYLE = {"in": "cyan", "you": "green", "sys": "yellow", "act": "dim"}
LEVEL_STYLE = {verdict.OK: "green", verdict.WARN: "yellow", verdict.BAD: "red"}


def budget_bar(used: float, budget: float, width: int = 8) -> str:
    frac = 0.0 if budget <= 0 else min(1.0, used / budget)
    filled = round(frac * width)
    return "▮" * filled + "▯" * (width - filled)


class ChatApp(App):
    TITLE = "r4t chat"

    CSS = """
    Screen { layout: vertical; }
    #header { height: auto; padding: 0 1; background: $panel; }
    #panes { height: 1fr; }
    #conversation { width: 2fr; }
    #activity { width: 1fr; border-left: solid $panel; }
    #composer { dock: bottom; }
    """

    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self, ctx: DispatchContext, roster: Roster, human: Member):
        super().__init__()
        self.ctx = ctx
        self.roster = roster
        self.human = human
        self.target = ctx.node
        self.feed = SeatFeed(ctx.node, human.name)
        self.sends: queue.Queue[tuple[str, str]] = queue.Queue()
        try:
            self.rig_config = load_rig_config(ctx.config_path)
        except RigError:
            self.rig_config = None

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="panes"):
            yield RichLog(id="conversation", wrap=True)
            yield RichLog(id="activity", wrap=True, max_lines=2000)
        yield Input(
            id="composer",
            placeholder=f"message {self.target} (leader) — /help for commands",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._emit(
            "sys",
            f"seat: {self.human.name} on {self.ctx.node} — messages go to "
            f"{self.target} (leader). /help for commands.",
        )
        self._pump_feed()
        self._refresh_header()
        threading.Thread(target=self._send_worker, daemon=True).start()
        self.set_interval(FEED_POLL_SECONDS, self._poll_feed)
        self.set_interval(HEADER_REFRESH_SECONDS, self._refresh_header)
        self.query_one("#composer", Input).focus()

    # ---------- output ----------

    def _emit(self, kind: str, text: str) -> None:
        log = self.query_one(
            "#activity" if kind == "act" else "#conversation", RichLog
        )
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in text.splitlines():
            log.write(Text.assemble((stamp + " ", "dim"), (line, KIND_STYLE[kind])))

    def _emit_message(self, envelope: dict) -> None:
        """An inbound message: sender header line, then the body as rendered
        markdown — agents write markdown, and raw ** and # are noise."""
        log = self.query_one("#conversation", RichLog)
        stamp = datetime.now().strftime("%H:%M:%S")
        sender = str(envelope.get("from", "?"))
        _, _, _, body = taskmod.parse_header(str(envelope.get("content", "")))
        log.write(Text.assemble((stamp + " ", "dim"), (sender, "bold cyan")))
        log.write(Padding(Markdown(body.strip() or "(empty)"), (0, 0, 1, 9)))

    # ---------- polling ----------

    def _pump_feed(self) -> None:
        for kind, payload in self.feed.poll():
            if kind == "in":
                self._emit_message(payload)
            else:
                self._emit(kind, payload)

    def _poll_feed(self) -> None:
        state.touch_seat_presence(self.ctx.node, self.human.name)
        self._pump_feed()

    def _refresh_header(self) -> None:
        verdicts = verdict.team_verdicts(self.ctx.node, self.roster, self.rig_config)
        worst = verdict.worst_level(verdicts)
        open_tasks = [
            t for t in taskmod.list_tasks(self.ctx.node)
            if t.get("status") == taskmod.STATUS_OPEN
        ]

        header = Text()
        header.append(self.ctx.node, style="bold")
        header.append(f" · seat {self.human.name} → {self.target}", style="")
        header.append(f" · {len(open_tasks)} open task(s)", style="dim")
        header.append("   ")
        header.append(
            f"{verdict.MARKS[worst]} "
            + {"ok": "healthy", "warn": "attention", "bad": "action needed"}[worst],
            style=LEVEL_STYLE[worst],
        )

        shown = [v for v in verdicts if v.level != verdict.OK]
        for v in shown[:MAX_HEADER_VERDICTS]:
            header.append("\n")
            header.append(f"{verdict.MARKS[v.level]} {v.text}", style=LEVEL_STYLE[v.level])
        if len(shown) > MAX_HEADER_VERDICTS:
            header.append(
                f"\n… {len(shown) - MAX_HEADER_VERDICTS} more (r4t status)", style="dim"
            )
        for t in open_tasks[:MAX_HEADER_TASKS]:
            used = float(t.get("used", 0.0))
            budget = float(t.get("budget", 1.0) or 1.0)
            header.append("\n")
            header.append(budget_bar(used, budget), style="magenta")
            header.append(
                f" {100 * used / budget:3.0f}% {t.get('id', '?')}"
                f"  turns={t.get('turns', 0)} creator={t.get('creator', '?')}",
                style="dim",
            )
        self.query_one("#header", Static).update(header)

    # ---------- sending ----------

    def _send_worker(self) -> None:
        while True:
            to, text = self.sends.get()
            try:
                send_as_human(self.ctx, self.human, to, text)
            except Exception as e:  # noqa: BLE001 — surface, don't kill the seat
                self.call_from_thread(self._emit, "sys", f"send failed: {e}")

    # ---------- input ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.clear()
        if not line:
            return
        if line.startswith("/"):
            self._command(line)
            return
        self.sends.put((self.target, line))
        self._emit("you", f"you -> {self.target}: {line}")

    def _command(self, line: str) -> None:
        cmd, _, arg = line.partition(" ")
        cmd = cmd.lower()
        if cmd == "/quit":
            self.exit(0)
        elif cmd == "/help":
            self._emit("sys", HELP)
        elif cmd == "/to":
            target = resolve_target(self.roster, self.ctx.node, arg)
            if target is None:
                self._emit(
                    "sys", f"no AI member named {arg.strip().lower()!r} in the roster"
                )
                return
            self.target = target
            suffix = " (leader)" if target == self.ctx.node else ""
            self.query_one("#composer", Input).placeholder = (
                f"message {self.target}{suffix} — /help for commands"
            )
            self._emit("sys", f"target: {self.target}")
            self._refresh_header()
        elif cmd == "/who":
            self._emit(
                "sys", "\n".join(format_who(self.ctx.node, self.roster, self.human))
            )
        elif cmd == "/tasks":
            self._emit("sys", "\n".join(format_tasks(self.ctx.node)))
        else:
            self._emit("sys", f"unknown command {cmd!r} (/help)")


def run_chat_tui(ctx: DispatchContext) -> int:
    try:
        roster, human = load_seat(ctx)
    except SeatError as e:
        print(f"r4t chat: {e}", file=sys.stderr)
        return 2
    app = ChatApp(ctx, roster, human)
    try:
        state.touch_seat_presence(ctx.node, human.name)
        result = app.run()
    finally:
        state.clear_seat_presence(ctx.node, human.name)
    return int(result or 0)
