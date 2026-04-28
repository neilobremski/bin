"""Transient agent directories under `~/.a8s/transient/`.

Used today by `a8s ask` to mint a per-CLI-invocation `ASK_<ulid>` name that
acts as the asker's pseudo-recipient. The asker registers a transient dir,
publishes the ask envelope with `from = ASK_<ulid>`, and either spawns its
own subscribers (remote case) or polls a per-message response file
(local case). When the reply arrives, it lands in
`~/.a8s/transient/ASK_<ulid>/inbox/`, the asker reads it, and cleanup
removes the dir.

A transient is "live" if its pid file points at a running process. Stale
dirs (process gone, mtime older than `max_age_s`) are pruned best-effort
on every new registration.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from core import (
    _pid_alive,
    transient_dir,
    transient_inbox_dir,
    transient_inbox_tmp_dir,
)


def register(name: str) -> Path:
    """Create the transient dir for `name`, write the pid file, return the
    transient root. Idempotent — re-registering the same name no-ops on the
    dirs but rewrites the pid file."""
    root = transient_dir(name)
    transient_inbox_dir(name).mkdir(parents=True, exist_ok=True)
    transient_inbox_tmp_dir(name).mkdir(parents=True, exist_ok=True)
    (root / "pid").write_text(str(os.getpid()), encoding="utf-8")
    return root


def cleanup(name: str) -> None:
    """Remove the transient dir entirely. Safe to call multiple times."""
    shutil.rmtree(str(transient_dir(name)), ignore_errors=True)


def is_live(name: str) -> bool:
    """True iff a transient dir for `name` exists AND its pid file points at
    a running process. Used by `network.receive_envelope` to decide whether
    to deliver an envelope addressed to a transient name."""
    root = transient_dir(name)
    if not root.is_dir():
        return False
    pid_file = root / "pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return _pid_alive(pid)


def prune_stale(max_age_s: int = 3600) -> None:
    """Best-effort cleanup of transient dirs whose pid is gone or whose mtime
    is older than `max_age_s`. Called on every `register` so leftover dirs
    from crashed asks don't accumulate."""
    # Derive the transient base from a known-name path so we don't hardcode it.
    base = transient_dir("placeholder").parent
    if not base.is_dir():
        return
    cutoff = time.time() - max_age_s
    for child in base.iterdir():
        if not child.is_dir():
            continue
        pid_file = child / "pid"
        alive = False
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            alive = _pid_alive(pid)
        except (OSError, ValueError):
            alive = False
        try:
            mtime = child.stat().st_mtime
        except OSError:
            mtime = 0
        if alive and mtime > cutoff:
            continue
        shutil.rmtree(str(child), ignore_errors=True)
