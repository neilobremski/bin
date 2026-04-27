"""a8s daemon — wake subprocess execution + per-agent attachment + signal handling.

This module owns the runtime engine:
- `acquire`/`release` manage exclusive pid-file attachment per agent.
- `run_with_prefix` spawns the wake subprocess in its own session group so
  SIGKILL can target the whole tree.
- `wake_once` processes one inbox message (with read-time wipe for CLEAR).
- `attached_loop` is the daemon body — handles 1+ agents in one process.

Module-level mutable state used by signal handlers:
  _STOP_EVENT          — set on 1st signal; checked in the loop body
  _SIGNAL_COUNT        — incremented per signal; 2 triggers force-kill
  _CURRENT_WAKE_PROC   — the currently-running wake subprocess (or None)

`attached_loop` also sets `core.PRINT_LOCK` to a fresh Lock so `core.out` /
`core.out_agent` serialize log writes across threads.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time as _time
from pathlib import Path

import core
from core import (
    Participant,
    _pid_alive,
    _preview,
    agent_dir,
    inbox_dir,
    out_agent,
    pid_path,
    trash_dir,
    unique_path,
)
from definitions import build_command, build_prompt, load_definition, select_verb
from mailbox import ensure_mailboxes, next_inbox_message, route_outboxes
from registry import participants_from_registry


# ---------- subprocess execution ----------

# Set by run_with_prefix; read by _kill_wake_subprocess_group via the signal handler.
_CURRENT_WAKE_PROC: subprocess.Popen | None = None


def run_with_prefix(name: str, cmd: list[str], cwd: Path) -> int:
    """Run the wake subprocess in its own session so SIGKILL can target the
    whole process group (LLM CLI + any helpers it spawns). Tracks the live
    process in `_CURRENT_WAKE_PROC` so the second-signal handler can find it."""
    global _CURRENT_WAKE_PROC
    prefix = f"{name}> "
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except FileNotFoundError:
        out_agent(name, f"{prefix}command not found: {cmd[0]}")
        return 127
    _CURRENT_WAKE_PROC = proc
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            out_agent(name, prefix + line.rstrip("\n"))
        proc.wait()
        if proc.returncode != 0:
            out_agent(name, f"{prefix}(exit {proc.returncode})")
        return proc.returncode
    finally:
        _CURRENT_WAKE_PROC = None


def wake_once(p: Participant, msg_path: Path) -> None:
    try:
        with msg_path.open("r", encoding="utf-8") as f:
            msg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out_agent(p.name, f"[{p.name}] inbox parse error on {msg_path.name}: {e}")
        bad = unique_path(trash_dir(p.name) / msg_path.name)
        msg_path.rename(bad)
        return

    try:
        definition = load_definition(p.name)
    except (FileNotFoundError, RuntimeError) as e:
        out_agent(p.name, f"[{p.name}] {e}")
        bad = unique_path(trash_dir(p.name) / msg_path.name)
        msg_path.rename(bad)
        return

    verb = select_verb(msg)
    if verb == "clear":
        # Read-time wipe (locked design Q1, belt-and-suspenders): trash any
        # other messages currently in the inbox so the clear is the only thing
        # this wake processes. Anything that arrives during the wake will land
        # for the next iteration.
        for f in inbox_dir(p.name).iterdir():
            if f.is_file() and f != msg_path:
                trashed = unique_path(trash_dir(p.name) / f.name)
                f.rename(trashed)

    prompt = build_prompt(msg, definition, verb)
    trashed = unique_path(trash_dir(p.name) / msg_path.name)
    msg_path.rename(trashed)
    if verb == "clear":
        out_agent(p.name, f"[{p.name}] waking ({verb}) from {trashed.name}")
    else:
        out_agent(p.name, f"[{p.name}] waking ({verb}) from {trashed.name}: {_preview(msg.get('content', ''))}")
    cmd = build_command(definition, prompt, verb)
    run_with_prefix(p.name, cmd, p.root)


# ---------- per-agent attachment ----------

def _read_handler_pid(name: str) -> int | None:
    """Return the live PID currently handling <name>, or None. Cleans up stale
    pid files."""
    p = pid_path(name)
    if not p.is_file():
        return None
    try:
        pid = int(p.read_text().strip())
    except (OSError, ValueError):
        try:
            p.unlink()
        except OSError:
            pass
        return None
    if _pid_alive(pid):
        return pid
    try:
        p.unlink()
    except OSError:
        pass
    return None


def _try_atomic_claim(name: str, pid: int) -> bool:
    """Attempt to write `pid` into `pid_path(name)` using O_CREAT|O_EXCL.
    Returns True iff this process now holds the handler attachment."""
    p = pid_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, str(pid).encode())
    finally:
        os.close(fd)
    return True


# Wait up to this long for an existing handler to release after SIGTERM.
ACQUIRE_TIMEOUT_S = 30.0
ACQUIRE_POLL_S = 0.1


def acquire(name: str) -> None:
    """Attach this process as the handler of <name>. If another live process
    is currently handling it, send SIGTERM (graceful detach) and wait for
    release. Always wins (per locked design). Raises TimeoutError if the prior
    handler doesn't release within ACQUIRE_TIMEOUT_S."""
    me = os.getpid()
    while True:
        if _try_atomic_claim(name, me):
            return
        existing = _read_handler_pid(name)
        if existing is None:
            continue  # stale or freed; retry
        if existing == me:
            return
        sys.stderr.write(f"[a8s] detach in progress: {name} from PID {existing}\n")
        sys.stderr.flush()
        try:
            os.kill(existing, signal.SIGTERM)
        except ProcessLookupError:
            try:
                pid_path(name).unlink()
            except OSError:
                pass
            continue
        deadline = _time.time() + ACQUIRE_TIMEOUT_S
        while _time.time() < deadline:
            if not pid_path(name).is_file():
                break
            if not _pid_alive(existing):
                try:
                    pid_path(name).unlink()
                except OSError:
                    pass
                break
            _time.sleep(ACQUIRE_POLL_S)
        else:
            raise TimeoutError(
                f"PID {existing} did not release {name} within {ACQUIRE_TIMEOUT_S}s — "
                f"try `a8s kill {name}`"
            )


def release(name: str) -> None:
    """Unlink the pid file iff it points at our pid. Safe to call repeatedly."""
    p = pid_path(name)
    try:
        if not p.is_file():
            return
        pid = int(p.read_text().strip())
        if pid == os.getpid():
            p.unlink()
    except (OSError, ValueError):
        pass


# ---------- attached loop (daemon body for 1+ agents) ----------

# Set when an attached loop is running. The signal handler closes over them.
_STOP_EVENT: threading.Event | None = None
_SIGNAL_COUNT = 0


def _kill_wake_subprocess_group() -> None:
    """SIGTERM-then-SIGKILL the current wake's subprocess group. Targets the
    whole process tree so the LLM CLI dies along with our wake wrapper."""
    proc = _CURRENT_WAKE_PROC
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        pass
    _time.sleep(0.5)
    if proc.poll() is None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


def _make_signal_handler(label: str):
    def handle(signum, _frame):
        global _SIGNAL_COUNT
        _SIGNAL_COUNT += 1
        if _SIGNAL_COUNT == 1:
            sys.stderr.write(
                f"[a8s] {label}: received signal {signum}; detaching after current wake\n"
            )
            sys.stderr.flush()
            if _STOP_EVENT is not None:
                _STOP_EVENT.set()
        else:
            sys.stderr.write(
                f"[a8s] {label}: second signal — killing wake subprocess group\n"
            )
            sys.stderr.flush()
            _kill_wake_subprocess_group()
    return handle


def attached_loop(names: list[str], interval: float, *, single_pass: bool = False) -> int:
    """Body of `a8s run` / `a8s start` / `a8s step`. ONE process handles every
    name in `names` (multiple agents share the same handler PID, recorded in
    each agent's `~/.a8s/agents/<NAME>/pid`).

    Per iteration:
      - reload registry (so newly-added agents become routable recipients)
      - check each handled agent's pid file is still ours (drop if taken over)
      - route each handled agent's outbox to recipients
      - drain each handled agent's inbox

    On 1st signal: detach all currently-handled agents (graceful — finish the
    in-flight wake first). On 2nd signal: SIGTERM-then-SIGKILL the wake
    subprocess group.

    Take-over collateral: SIGTERM is process-level, so when one of our handled
    agents is targeted by another `a8s start`, this whole handler detaches
    everything. Other agents in our set become orphaned. The user's footgun;
    documented in #52."""
    global _STOP_EVENT, _SIGNAL_COUNT
    core.PRINT_LOCK = threading.Lock()
    _STOP_EVENT = threading.Event()
    _SIGNAL_COUNT = 0

    if not names:
        print("attached_loop: empty names list", file=sys.stderr)
        return 2

    # Acquire each pid file. If any fails (timeout), release whatever we got.
    acquired: list[str] = []
    try:
        for name in names:
            acquire(name)
            acquired.append(name)
    except TimeoutError as e:
        print(str(e), file=sys.stderr)
        for n in acquired:
            release(n)
        return 1

    label = names[0] if len(names) == 1 else f"[{', '.join(names)}]"
    handler = _make_signal_handler(label)
    prev_sigterm = signal.signal(signal.SIGTERM, handler)
    prev_sigint = signal.signal(signal.SIGINT, handler)

    pid = os.getpid()
    for n in names:
        out_agent(n, f"[a8s] {n}: attached (PID {pid}{', shared' if len(names) > 1 else ''})")
    try:
        while not _STOP_EVENT.is_set():
            try:
                all_agents = participants_from_registry()
                # Filter to agents we still hold and that still exist.
                handled: list[Participant] = []
                for name in list(names):
                    p = next((q for q in all_agents if q.name == name), None)
                    if p is None:
                        out_agent(name, f"[a8s] {name}: removed from registry; dropping")
                        names.remove(name)
                        continue
                    holder = _read_handler_pid(name)
                    if holder is not None and holder != pid:
                        out_agent(name, f"[a8s] {name}: detaching (taken over by PID {holder})")
                        names.remove(name)
                        continue
                    handled.append(p)
                if not handled:
                    out_agent(label, f"[a8s] {label}: nothing left to handle; exiting")
                    break
                for p in handled:
                    ensure_mailboxes(p)
                route_outboxes(handled, all_agents=all_agents)
                for p in handled:
                    while not _STOP_EVENT.is_set():
                        msg = next_inbox_message(p)
                        if msg is None:
                            break
                        wake_once(p, msg)
            except Exception as e:
                out_agent(label, f"[a8s] {label}: iteration error: {e}")
            if single_pass:
                break
            _STOP_EVENT.wait(interval)
    finally:
        # Release every pid file we still hold.
        for n in acquired:
            holder = _read_handler_pid(n)
            if holder is None or holder == pid:
                release(n)
                out_agent(n, f"[a8s] {n}: detached")
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
        _STOP_EVENT = None
    return 0
