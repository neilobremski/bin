"""OS-level isolation for rig invocation — run-as-user and container variants.

The through-line (see plans/ISOLATE-SPEC.md): harness sandbox flags are not a
security boundary; the operating system is. A rig may name `run_as` (a Unix
user with no sudo) or `container` (an image) and the agent runs fully
permissive INSIDE that boundary. r4t never provisions the boundary — it
verifies operator-provisioned prerequisites and fails closed with an
action-first error — except for the shared message dirs, which are r4t's own
state and are re-asserted to the correct owner-group/mode before every turn.

Both wrappers are pure argv builders so the exact shape is unit-testable
against fake `sudo`/`docker` binaries; nothing here shells out except the
prereq probes and the container kill, which run the real tool by name.
"""
from __future__ import annotations

import os
import pwd
import re
import subprocess
import time
from pathlib import Path

PROBE_TIMEOUT_SECONDS = 10

# The bash the wrapped `sudo` runs: env cannot survive sudoers env_reset, so
# TELL_OUTBOX_DIR rides as $1 and the workplace as $2; `exec "$@"` hands the
# remaining positionals to the harness verbatim — no quoted command string, so
# quoting bugs are structurally impossible.
_RUN_AS_BOOTSTRAP = 'export TELL_OUTBOX_DIR="$1"; cd "$2"; shift 2; exec "$@"'

# A standard system PATH the container shim prepends the a8s client dir to, so
# an unmodified `tell` resolves inside the image. Operators can override with a
# later `-e PATH=...` in container_args (docker keeps the last value).
_CONTAINER_BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class IsolationError(Exception):
    pass


def a8s_client_dir() -> Path:
    """The repo bin root that holds the `tell` client shim — mounted read-only
    into a container so `tell` resolves on PATH. isolate.py lives at
    apps/r4t/, so two parents up is the bin root."""
    return Path(__file__).resolve().parents[2]


# ---------- run_as ----------

def wrap_run_as(
    argv: list[str], user: str, staging_dir: str | Path, workplace: str | Path
) -> list[str]:
    return [
        "sudo", "-u", user, "bash", "--login", "-c",
        _RUN_AS_BOOTSTRAP, "_", str(staging_dir), str(workplace), *argv,
    ]


def _run_probe(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS
    )


def probe_run_as(user: str, workplace: str | Path) -> str | None:
    """Verify the two operator-provisioned prerequisites for `run_as`: a
    passwordless sudo grant to `user`, and a workplace writable by `user`.
    Returns None when both pass, else an action-first error carrying the fix.
    Never provisions anything."""
    try:
        grant = _run_probe(["sudo", "-n", "-u", user, "true"])
    except (OSError, subprocess.TimeoutExpired) as e:
        return (
            f"cannot probe sudo to {user!r}: {e} "
            f"(try: install sudo and grant the router NOPASSWD — see "
            f"apps/r4t/docs/isolation.md)"
        )
    if grant.returncode != 0:
        detail = (grant.stderr or grant.stdout or "").strip()
        return (
            f"no passwordless sudo to user {user!r}"
            + (f": {detail}" if detail else "")
            + " (try: add a NOPASSWD sudoers grant for the wake command — see "
            "apps/r4t/docs/isolation.md)"
        )
    probe = f".r4t-write-probe.{os.getpid()}.{time.time_ns()}"
    try:
        write = _run_probe([
            "sudo", "-n", "-u", user, "bash", "--login", "-c",
            'f="$1/$2"; touch "$f" && rm -f "$f"', "_", str(workplace), probe,
        ])
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"cannot probe workplace write as {user!r}: {e}"
    if write.returncode != 0:
        detail = (write.stderr or write.stdout or "").strip()
        return (
            f"workplace {workplace} not writable by user {user!r}"
            + (f": {detail}" if detail else "")
            + " (try: give the agent user's group g+ws on the workplace — see "
            "apps/r4t/docs/isolation.md)"
        )
    return None


def agent_gid(user: str) -> int | None:
    """The agent user's primary gid, for group-owning the shared dirs. None if
    the user is unknown (the sudo probe reports that failure first)."""
    try:
        return pwd.getpwnam(user).pw_gid
    except KeyError:
        return None


def assert_writable_shared_dir(path: str | Path, gid: int | None) -> None:
    """Re-assert r4t's writable message channel: owned by the router, group set
    to the agent's group, mode 2770 setgid so the agent writes envelopes and
    everything the agent creates stays group-owned. Idempotent; called before
    every turn, not just at setup — re-assertion is what made the precedent
    robust against drift."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    if gid is not None:
        os.chown(p, -1, gid)
    os.chmod(p, 0o2770)


def assert_readonly_shared_dir(path: str | Path, gid: int | None) -> None:
    """Re-assert a dir the agent may only READ (delivered files): router-owned,
    agent's group, mode 2750 setgid, no group write bit."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    if gid is not None:
        os.chown(p, -1, gid)
    os.chmod(p, 0o2750)


# ---------- container ----------

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").strip()) or "x"


def container_name(node: str, member: str, ts: int | None = None) -> str:
    """Deterministic per-turn container name so a timeout can kill by name."""
    stamp = ts if ts is not None else time.time_ns()
    return f"r4t-{_slug(node)}-{_slug(member)}-{stamp}"


def build_container_argv(
    argv: list[str],
    image: str,
    *,
    name: str,
    staging_dir: str | Path,
    workplace: str | Path,
    tell_outbox: str | Path,
    container_args: list[str] | None = None,
    delivered_dir: str | Path | None = None,
    client_dir: str | Path | None = None,
) -> list[str]:
    """`docker run --rm` with the workplace bind-mounted rw at the same path and
    used as workdir, the staging dir rw at the same path with TELL_OUTBOX_DIR
    injected (no env_reset inside a container), the a8s client ro with a PATH
    shim, an optional delivered-files dir ro, then per-rig container_args
    verbatim, then the image and the harness argv. r4t never builds, pulls, or
    inspects the image — a missing image is an ordinary turn failure."""
    client = Path(client_dir) if client_dir is not None else a8s_client_dir()
    cmd = [
        "docker", "run", "--rm", "--name", name,
        "-v", f"{workplace}:{workplace}",
        "-w", str(workplace),
        "-v", f"{staging_dir}:{staging_dir}",
        "-e", f"TELL_OUTBOX_DIR={tell_outbox}",
        "-v", f"{client}:{client}:ro",
        "-e", f"PATH={client}:{_CONTAINER_BASE_PATH}",
    ]
    if delivered_dir is not None:
        cmd += ["-v", f"{delivered_dir}:{delivered_dir}:ro"]
    cmd += list(container_args or [])
    cmd += [image, *argv]
    return cmd


def kill_container(name: str) -> None:
    """Best-effort kill a container by name after a rig timeout; `--rm` reaps
    it. Never raises — the turn already failed."""
    try:
        subprocess.run(
            ["docker", "kill", name],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
