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
import shlex
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
    detach_request_path,
    inbox_dir,
    kill_request_path,
    out_agent,
    pid_path,
    trash_dir,
    unique_path,
)
from definitions import build_command, load_definition, select_verb
from mailbox import ensure_mailboxes, next_inbox_message, route_outboxes
from network import (
    load_remotes,
    make_publish_remotes,
    start_remotes,
    stop_remotes,
)
from registry import participants_from_registry


# ---------- subprocess execution ----------

# Set by run_with_prefix; read by _kill_wake_subprocess_group via the signal
# handler. _CURRENT_WAKE_NAME pairs with _CURRENT_WAKE_PROC so the SIGUSR1
# kill-request handler can decide whether the in-flight wake is the one
# being killed (per-agent kill, issue #68 follow-up).
_CURRENT_WAKE_PROC: subprocess.Popen | None = None
_CURRENT_WAKE_NAME: str | None = None


def run_with_prefix(name: str, cmd: list[str], cwd: Path) -> int:
    """Run the wake subprocess in its own session so SIGKILL can target the
    whole process group (LLM CLI + any helpers it spawns). Tracks the live
    process in `_CURRENT_WAKE_PROC` and the agent in `_CURRENT_WAKE_NAME` so
    signal handlers can identify which agent's wake is in-flight."""
    global _CURRENT_WAKE_PROC, _CURRENT_WAKE_NAME
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
    _CURRENT_WAKE_NAME = name
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
        _CURRENT_WAKE_NAME = None


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

    trashed = unique_path(trash_dir(p.name) / msg_path.name)
    msg_path.rename(trashed)
    if verb == "clear":
        out_agent(p.name, f"[{p.name}] waking ({verb}) from {trashed.name}")
    else:
        out_agent(p.name, f"[{p.name}] waking ({verb}) from {trashed.name}: {_preview(msg.get('content', ''))}")
    cmd = build_command(definition, msg, verb)
    out_agent(p.name, f"[{p.name}] exec: {shlex.join(cmd)}")
    run_with_prefix(p.name, cmd, p.root)


# ---------- per-agent attachment ----------

def _read_handler_pid(name: str) -> int | None:
    """Return the live PID currently handling <name>, or None. Cleans up stale
    pid files. Treats empty / non-int / non-positive contents as stale (the
    O_CREAT|O_EXCL window allows a partial-write to leave an empty pid file
    if the writer dies before `os.write`; non-positive values don't refer to
    any real process — `os.kill(0, ...)` would target the whole process group)."""
    p = pid_path(name)
    if not p.is_file():
        return None
    try:
        pid = int(p.read_text().strip())
        if pid <= 0:
            raise ValueError("non-positive pid")
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
    Returns True iff this process now holds the handler attachment.

    `os.fsync` after the write makes the pid bytes durable before the fd
    closes — without it, a kernel-level crash window between create and write
    could leave readers parsing an empty file (which `_read_handler_pid` now
    treats as stale and cleans up)."""
    p = pid_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, str(pid).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


# How long to wait for the holder to honor a detach-request before giving up.
# Long enough to cover an in-flight LLM wake (the holder only checks the
# request between iterations, so an active subprocess delays response).
DETACH_TIMEOUT_S = 60.0
DETACH_POLL_S = 0.2


def _write_detach_request(name: str, requester_pid: int) -> None:
    """Write `requester_pid` into the detach-request file for `name` (overwrites
    any prior request — last writer wins, which is fine since whichever
    requester is the most recent will get the agent next)."""
    p = detach_request_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(requester_pid))


def _read_detach_request(name: str) -> int | None:
    """Return the requester pid in the detach-request file for `name`, or None.
    Reaps malformed contents (empty / non-int / non-positive) and stale
    requests from dead requesters — without the liveness check, an
    `acquire()` caller that crashes after writing the request would cause
    the holder's next iteration to release the agent to nobody (issue #71)."""
    p = detach_request_path(name)
    if not p.is_file():
        return None
    try:
        pid = int(p.read_text().strip())
        if pid <= 0:
            raise ValueError("non-positive pid")
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


def _clear_detach_request(name: str) -> None:
    """Best-effort unlink of the detach-request file."""
    try:
        detach_request_path(name).unlink()
    except OSError:
        pass


def _write_kill_request(name: str, requester_pid: int) -> None:
    p = kill_request_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(requester_pid))


def _read_kill_request(name: str) -> int | None:
    """Same parse-and-reap discipline as `_read_detach_request`, including
    the dead-requester reap (issue #71)."""
    p = kill_request_path(name)
    if not p.is_file():
        return None
    try:
        pid = int(p.read_text().strip())
        if pid <= 0:
            raise ValueError("non-positive pid")
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


def _clear_kill_request(name: str) -> None:
    try:
        kill_request_path(name).unlink()
    except OSError:
        pass


def acquire(name: str) -> None:
    """Attach this process as the handler of <name>.

    If another live process holds <name>, write a detach-request file and
    poll for it to release. The holder's `attached_loop` checks the request
    at the top of each iteration and releases just <name> (not its other
    handled agents) — so a multi-agent handler losing one member keeps
    serving the rest. Raises `TimeoutError` if the holder doesn't honor
    the request within `DETACH_TIMEOUT_S` (typically because an in-flight
    LLM wake is taking a long time; `a8s kill <name>` breaks the deadlock).

    Stale pid files (writer dead) are reaped by `_read_handler_pid` and
    the claim retried."""
    me = os.getpid()
    requested = False
    deadline: float | None = None
    while True:
        if _try_atomic_claim(name, me):
            # If the pending request was OURS (we placed it earlier in this
            # call), clear it — it's been satisfied. Leave foreign requests
            # alone: those belong to whichever process placed them, and our
            # next iteration as the new holder will honor them.
            if _read_detach_request(name) == me:
                _clear_detach_request(name)
            return
        existing = _read_handler_pid(name)
        if existing is None:
            continue  # stale; retry the claim
        if existing == me:
            return
        if not requested:
            _write_detach_request(name, me)
            sys.stderr.write(
                f"[a8s] {name}: requesting release from PID {existing}...\n"
            )
            sys.stderr.flush()
            requested = True
            deadline = _time.time() + DETACH_TIMEOUT_S
        if deadline is not None and _time.time() >= deadline:
            _clear_detach_request(name)
            raise TimeoutError(
                f"PID {existing} did not release {name} within {DETACH_TIMEOUT_S}s — "
                f"try `a8s kill {name}`"
            )
        _time.sleep(DETACH_POLL_S)


def release(name: str) -> None:
    """Unlink the pid file iff it points at our pid. Safe to call repeatedly.
    Also clears any pending detach-request for `name` since the request was
    aimed at the now-released attachment."""
    p = pid_path(name)
    try:
        if p.is_file():
            pid = int(p.read_text().strip())
            if pid == os.getpid():
                p.unlink()
    except (OSError, ValueError):
        pass
    _clear_detach_request(name)


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


def _on_kill_signal(_signum, _frame):
    """SIGUSR1 from `cmd_kill`. If the in-flight wake's target agent has a
    foreign kill-request, kill the subprocess group so `run_with_prefix`'s
    `wait()` returns immediately. The actual release of the agent (and any
    others with a kill-request, even when no wake is in flight) happens at
    the next iteration top via `_read_kill_request`."""
    name = _CURRENT_WAKE_NAME
    if name is None:
        return
    req = _read_kill_request(name)
    if req is None or req == os.getpid():
        return
    _kill_wake_subprocess_group()


def attached_loop(names: list[str], interval: float, *, single_pass: bool = False) -> int:
    """Body of `a8s run` / `a8s start` / `a8s step`. ONE process handles every
    name in `names`; multi-agent handlers share a PID across each member's
    pid file.

    Per iteration:
      - honor any detach-requests for our handled agents (per-agent take-over,
        issue #68): release just the requested agent and keep serving the rest
      - reload registry (so newly-added agents become routable recipients)
      - drop any agent whose pid file no longer points at us (defense)
      - route each handled agent's outbox; drain each handled agent's inbox

    On 1st signal: detach all currently-handled agents (graceful — finish the
    in-flight wake first). On 2nd signal: SIGTERM-then-SIGKILL the wake
    subprocess group. The whole-process detach is the path for explicit
    `a8s stop` / `a8s kill`; per-agent take-over for `a8s start`/`run`/`step`
    against an already-attached agent goes through the detach-request file
    instead, leaving siblings handled — no orphans."""
    global _STOP_EVENT, _SIGNAL_COUNT
    core.PRINT_LOCK = threading.Lock()
    _STOP_EVENT = threading.Event()
    _SIGNAL_COUNT = 0

    if not names:
        print("attached_loop: empty names list", file=sys.stderr)
        return 2

    # Acquire each pid file. If any fails (holder didn't honor the
    # detach-request in time), release whatever we got.
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
    prev_sigusr1 = signal.signal(signal.SIGUSR1, _on_kill_signal)

    pid = os.getpid()
    for n in names:
        out_agent(n, f"[a8s] {n}: attached (PID {pid}{', shared' if len(names) > 1 else ''})")

    # Issue #63: load configured remotes and start one subscriber loop per
    # remote. The receive callback always asks the registry for the current
    # participant list so agents added after startup become routable without
    # restarting the daemon.
    started_remotes = start_remotes(load_remotes(), participants_from_registry)
    publish_remotes = make_publish_remotes(started_remotes) if started_remotes else None
    configured_remote_ids = [r.id for r in started_remotes]
    try:
        while not _STOP_EVENT.is_set():
            try:
                # Honor kill-requests and detach-requests at the iteration
                # top. Kill takes precedence (SIGUSR1 may have already killed
                # the subprocess group, but the release happens here so the
                # iteration body skips that agent for the rest of the pass).
                for name in list(names):
                    kill_req = _read_kill_request(name)
                    if kill_req is not None and kill_req != pid:
                        out_agent(name, f"[a8s] {name}: killed by PID {kill_req}")
                        release(name)
                        _clear_kill_request(name)
                        names.remove(name)
                        continue
                    requester = _read_detach_request(name)
                    if requester is not None and requester != pid:
                        out_agent(name, f"[a8s] {name}: releasing to PID {requester}")
                        release(name)
                        names.remove(name)

                all_agents = participants_from_registry()
                handled: list[Participant] = []
                for name in list(names):
                    p = next((q for q in all_agents if q.name == name), None)
                    if p is None:
                        out_agent(name, f"[a8s] {name}: removed from registry; dropping")
                        names.remove(name)
                        continue
                    holder = _read_handler_pid(name)
                    if holder is not None and holder != pid:
                        # Defense: someone manually overwrote the pid file
                        # outside of the detach-request handshake.
                        out_agent(name, f"[a8s] {name}: pid file diverged (now PID {holder}); dropping")
                        names.remove(name)
                        continue
                    handled.append(p)
                if not handled:
                    out_agent(label, f"[a8s] {label}: nothing left to handle; exiting")
                    break
                for p in handled:
                    ensure_mailboxes(p)
                route_outboxes(
                    handled,
                    all_agents=all_agents,
                    publish_remotes=publish_remotes,
                    configured_remote_ids=configured_remote_ids,
                )
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
        # Stop subscriber threads first so paho's network loop unwinds before
        # we release pid files (otherwise an in-flight envelope arriving
        # during shutdown could try to write into a directory we're about to
        # forget).
        stop_remotes(started_remotes)
        # Release every pid file we still hold.
        for n in acquired:
            holder = _read_handler_pid(n)
            if holder is None or holder == pid:
                release(n)
                out_agent(n, f"[a8s] {n}: detached")
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGUSR1, prev_sigusr1)
        _STOP_EVENT = None
    return 0
