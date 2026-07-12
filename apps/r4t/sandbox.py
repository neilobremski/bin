"""`r4t sandbox` — disposable end-to-end team run with a graded report.

Creates a temp dir holding a private A8S_HOME + R4T_HOME, copies the
bundled team-of-3 seed (apps/r4t/sandbox/) into a temp repo, registers the
node + namespace through the real a8s CLI, starts a handler, kicks off the
GOAL.md task as a registered "human" agent, waits for quiescence, tears
everything down (a8s stop is a graceful SIGTERM; the no-orphans invariant
is verified with a process scan), and writes one self-contained markdown
report whose MECHANICAL CHECKS section is computed — an external judge
needs nothing but the report. Progress logs go to stderr; the final report
is written to stdout (pipe or redirect to save it).

`--fake` swaps every rig's invoke for sandbox/fake-agent.py: scripted
role-play that exercises dispatch, staging release, header stamping,
delegation, and the final leader answer with zero LLM calls. Live mode uses
`--preset` (any `r4t rig presets` entry; default `opencode`) and
optional `--model` for presets like `opencode-ollama`. The chosen argv is
passed to live-agent.py via R4T_SANDBOX_INVOKE.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import state

from rig import RigError, build_preset_invoke, format_preset_invoke, preset_names

R4T_DIR = Path(__file__).resolve().parent
SANDBOX_DIR = R4T_DIR / "sandbox"
A8S_DIR = R4T_DIR.parent / "a8s"
A8S_PY = A8S_DIR / "a8s.py"

TEAM = "crew"
NODE = "crew-node"
ALIAS = "sandboxcrew"
MAX_TURNS = 15


class SandboxError(Exception):
    pass


def _log(msg: str) -> None:
    print(f"sandbox: {msg}", file=sys.stderr, flush=True)


def _a8s(*args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, str(A8S_PY), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise SandboxError(
            f"a8s {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def _write_definition(path: Path) -> None:
    state.atomic_write_json(
        path,
        {
            "description": "r4t sandbox node",
            "invoke": [
                sys.executable,
                str(R4T_DIR / "r4t.py"),
                "dispatch",
                "--root",
                ".",
                "--from",
                "$SENDER",
                "--to",
                "$RECIPIENT",
                "--message",
                "$MESSAGE",
            ],
            "max_wake_seconds": 2700,
            "idle": {
                "timeout": 10,
                "invoke": [
                    sys.executable,
                    str(R4T_DIR / "r4t.py"),
                    "idle",
                    "--root",
                    ".",
                    "--node",
                    TEAM,
                ],
            },
        },
    )


def _write_rig_config(path: Path, fake: bool, break_member: str | None = None) -> None:
    config = json.loads((SANDBOX_DIR / "rigs.json").read_text(encoding="utf-8"))
    if fake:
        for value in config.values():
            if isinstance(value, dict) and "invoke" in value:
                value["invoke"] = [
                    sys.executable,
                    str(SANDBOX_DIR / "fake-agent.py"),
                    "{prompt}",
                ]
                value["timeout_seconds"] = 60
        config["throttle"] = {"max_concurrent": 1, "min_seconds_between_turn_starts": 0}
    else:
        for value in config.values():
            if isinstance(value, dict) and "invoke" in value:
                value["invoke"] = [
                    sys.executable,
                    str(SANDBOX_DIR / "live-agent.py"),
                    "{prompt}",
                ]
    if break_member:
        config["broken"] = {
            "invoke": [sys.executable, "-c", "import sys; sys.exit(1)", "{prompt}"],
            "timeout_seconds": 30,
        }
        config["pins"] = {break_member.lower(): "broken"}
        config["breaker_cap"] = 2
    state.atomic_write_json(path, config)


def _kickoff(human_root: Path, goal: str) -> None:
    outbox = human_root / ".outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    msg_id = f"{time.time_ns():026d}"
    state.atomic_write_json(
        outbox / f"{msg_id}.json",
        {
            "id": msg_id,
            "to": f"{TEAM}:lead",
            "content": (
                "Build the battleship game in GOAL.md.\n\n"
                "Lead: your only action this turn is to delegate — run:\n"
                f'  tell {TEAM}:dev "Build battleship.py per GOAL.md in this directory."\n'
                "Do not implement it yourself.\n\n" + goal
            ),
            "files": [],
        },
    )


def _agent_messages(a8s_home: Path, agent: str) -> list[dict]:
    out: list[dict] = []
    for sub in ("inbox", "trash"):
        d = a8s_home / "agents" / agent / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append(data)
    return out


def _final_answer(a8s_home: Path) -> dict | None:
    for msg in _agent_messages(a8s_home, "human"):
        sender = str(msg.get("from", ""))
        if sender != NODE and not sender.startswith(f"{TEAM}:"):
            continue
        if str(msg.get("content", "")).strip():
            return msg
    return None


def _busy(a8s_home: Path, repo: Path) -> bool:
    if state.live_locks(TEAM):
        return True
    if any(state.queue_depth(TEAM, m) for m in state.members_with_queue(TEAM)):
        return True
    for d in (
        a8s_home / "agents" / NODE / "inbox",
        a8s_home / "agents" / "human" / "inbox",
        repo / ".outbox",
        a8s_home / "agents" / "human" / ".outbox",
    ):
        if d.is_dir() and any(d.glob("*.json")):
            return True
    return False


def _handler_pids(a8s_home: Path) -> list[int]:
    pids = []
    for pid_file in (a8s_home / "agents").glob("*/pid"):
        try:
            pids.append(int(pid_file.read_text().strip()))
        except (OSError, ValueError):
            continue
    return pids


def _stop_handlers(a8s_home: Path) -> None:
    try:
        _a8s("stop", ALIAS)
    except SandboxError:
        pass
    deadline = time.time() + 30
    while time.time() < deadline and _handler_pids(a8s_home):
        time.sleep(0.5)
    for agent in (NODE, "human"):
        if (a8s_home / "agents" / agent / "pid").is_file():
            try:
                _a8s("kill", agent)
            except SandboxError:
                pass
    deadline = time.time() + 15
    while time.time() < deadline and _handler_pids(a8s_home):
        time.sleep(0.5)


def _orphans(tmp: Path) -> list[str]:
    result = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True
    )
    needle = str(tmp)
    return [line.strip() for line in result.stdout.splitlines() if needle in line]


def _kill_sandbox_processes(tmp: Path) -> int:
    """Terminate any process still referencing the temp sandbox dir."""
    lines = _orphans(tmp)
    pids: list[int] = []
    for line in lines:
        try:
            pids.append(int(line.split(None, 1)[0]))
        except (ValueError, IndexError):
            continue
    if not pids:
        return 0
    for pid in pids:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    time.sleep(2)
    for pid in pids:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return len(pids)


def _run_program(repo: Path) -> tuple[bool, str]:
    candidates = sorted(repo.glob("*.py"))
    if not candidates:
        return False, "no program file to run"
    program = next((p for p in candidates if "battleship" in p.name), candidates[0])
    guesses = "\n".join(f"{r} {c}" for r in range(5) for c in range(5)) + "\n"
    try:
        result = subprocess.run(
            [sys.executable, str(program)],
            input=guesses,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo),
        )
    except subprocess.TimeoutExpired:
        return False, f"{program.name} timed out after 30s"
    ok = result.returncode == 0
    tail = (result.stdout or "").strip().splitlines()[-1:] or [""]
    return ok, f"{program.name} exited {result.returncode} ({tail[0]})"


def _velocity_rows() -> list[list[str]]:
    path = state.team_dir(TEAM) / "velocity.csv"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [line.split(",") for line in lines[1:]]


def _governance_lines() -> list[str]:
    lines: list[str] = []
    log_dir = state.team_dir(TEAM) / "log"
    if not log_dir.is_dir():
        return lines
    for f in sorted(log_dir.glob("*.md")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("r4t: "):
                lines.append(line)
    return lines


HISTORY_ENTRY_RE = re.compile(
    r"(?m)^## (\S+) (from|to) (\S+)\n\n(.*?)(?=\n## |\Z)", re.DOTALL
)


def _conversation() -> list[tuple[str, str, str, str]]:
    """(timestamp, sender, recipient, body) from every agent's history.
    Intra-team messages appear once (the recipient's `from` entry); external
    releases come from `to` entries addressed outside the team."""
    events: list[tuple[str, str, str, str]] = []
    agents_dir = state.team_dir(TEAM) / "agents"
    if not agents_dir.is_dir():
        return events
    for history in agents_dir.glob("*/history.md"):
        agent = f"{TEAM}:{history.parent.name}"
        for ts, direction, other, body in HISTORY_ENTRY_RE.findall(
            history.read_text(encoding="utf-8")
        ):
            if direction == "from":
                events.append((ts, other, agent, body.strip()))
            elif not other.lower().startswith(TEAM):
                events.append((ts, agent, other, body.strip()))
    events.sort(key=lambda e: e[0])
    return events


def _dead_letter_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in state.list_dead_letters(TEAM):
        reason = record.get("reason", "?")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _emit_progress(
    *,
    seen_velocity: int,
    seen_gov: set[str],
    seen_locks: set[tuple[str, str]],
) -> tuple[int, set[str], set[tuple[str, str]]]:
    rows = _velocity_rows()
    for row in rows[seen_velocity:]:
        if len(row) >= 7:
            _log(f"turn done: {row[1]} ({row[2]}) task={row[3][:8]}… "
                 f"exit={row[6]} in {row[5]}s")
        else:
            _log(f"turn done: {', '.join(row)}")
    seen_velocity = len(rows)

    for line in _governance_lines():
        if line not in seen_gov:
            _log(line)
            seen_gov.add(line)

    for lock in state.live_locks(TEAM, prune=True):
        key = (str(lock.get("agent", "")), str(lock.get("task", "")))
        if key not in seen_locks:
            seen_locks.add(key)
            _log(
                f"turn started: {lock.get('agent', '?')} "
                f"(rig {lock.get('rig', '?')}, task {str(lock.get('task', ''))[:8]}…)"
            )
    return seen_velocity, seen_gov, seen_locks


def _build_report(
    *,
    mode: str,
    wall_clock: float,
    checks: list[tuple[str, object, str]],
    goal: str,
    repo_files: dict[str, str],
    harness: str = "",
) -> str:
    lines = [
        f"# r4t sandbox report — {mode} run",
        "",
        f"Generated {state.utc_now()} by `r4t sandbox`. Self-contained: the",
        "mechanical section is computed by the runner; everything else is the",
        "raw record of the run.",
        "",
        "## Mechanical checks",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for name, result, detail in checks:
        if isinstance(result, bool):
            shown = "PASS" if result else "FAIL"
        else:
            shown = str(result)
        lines.append(f"| {name} | {shown} | {detail} |")
    lines += [
        "",
        "## Run",
        "",
        f"- mode: {mode}",
    ]
    if harness:
        lines.append(f"- harness: {harness}")
    lines += [
        f"- wall clock: {wall_clock:.1f}s",
        "",
        "### Turns (velocity)",
        "",
        "| time | agent | rig | task | hop | seconds | exit |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in _velocity_rows():
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## Scenario (GOAL.md)", "", goal.strip(), "", "## Conversation", ""]
    for ts, sender, recipient, body in _conversation():
        lines.append(f"**{sender} → {recipient}** ({ts})")
        lines.append("")
        lines.extend(f"> {line}" for line in body.splitlines())
        lines.append("")
    lines += ["## Governance events", ""]
    events = _governance_lines()
    if events:
        lines.extend(f"- `{line}`" for line in events)
    else:
        lines.append("(none)")
    lines += ["", "## Produced files", ""]
    if repo_files:
        for name, content in sorted(repo_files.items()):
            lines += [f"### {name}", "", "```python", content.rstrip(), "```", ""]
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def run_sandbox(
    *,
    fake: bool,
    timeout: float,
    preset: str = "opencode",
    model: str | None = None,
    break_member: str | None = None,
) -> int:
    start = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="r4t-sandbox-"))
    saved_env = {k: os.environ.get(k) for k in ("A8S_HOME", "R4T_HOME", "R4T_SANDBOX_INVOKE", "R4T_SANDBOX")}
    a8s_home = tmp / "a8s-home"
    os.environ["A8S_HOME"] = str(a8s_home)
    os.environ["R4T_HOME"] = str(tmp / "r4t-home")
    os.environ["R4T_SANDBOX"] = "1"
    mode = "fake" if fake else "live"
    if break_member:
        mode += f"+break:{break_member.lower()}"
    harness_line = ""
    seen_velocity = 0
    seen_gov: set[str] = set()
    seen_locks: set[tuple[str, str]] = set()
    try:
        if fake:
            os.environ.pop("R4T_SANDBOX_INVOKE", None)
            _log("mode=fake (deterministic agents, no LLM)")
        else:
            try:
                invoke = build_preset_invoke(preset, model=model)
            except RigError as e:
                _log(str(e))
                return 1
            os.environ["R4T_SANDBOX_INVOKE"] = json.dumps(invoke)
            harness_line = format_preset_invoke(preset.strip().lower())
            if model:
                harness_line = f"{preset} (model={model}) — {harness_line}"
            else:
                harness_line = f"{preset} — {harness_line}"
            _log(f"harness {harness_line}")
        repo = tmp / "repo"
        repo.mkdir(parents=True)
        seed_names = {"ROSTER.md", "GOAL.md"}
        for name in seed_names:
            shutil.copy(SANDBOX_DIR / name, repo / name)
        workspace = repo.resolve()
        (repo / "WORKSPACE.md").write_text(
            f"# Workspace\n\nTeam repo root: `{workspace}`\n\n"
            "Write all project files here using relative paths (e.g. "
            "`battleship.py`). Do not write to ~/ or any path outside this "
            "directory.\n",
            encoding="utf-8",
        )
        goal = (repo / "GOAL.md").read_text(encoding="utf-8")

        _write_rig_config(
            tmp / "r4t-home" / "rigs.json", fake, break_member=break_member
        )
        definition = tmp / "r4t-def.json"
        _write_definition(definition)
        human_root = tmp / "human"
        human_root.mkdir()

        _log("registering node and handlers")
        _a8s("add", NODE, str(repo), str(definition))
        _a8s("add", "human", str(human_root), str(A8S_DIR / "definitions" / "default.json"))
        _a8s("namespace", TEAM, NODE)
        _a8s("alias", ALIAS, NODE)
        _a8s("alias", ALIAS, "human")
        _a8s("start", ALIAS)

        _kickoff(human_root, goal)
        _log("kickoff sent to crew:lead")

        deadline = time.time() + timeout
        quiet_polls = 0
        final = None
        while True:
            now = time.time()
            if now >= deadline:
                if _busy(a8s_home, repo):
                    _log("timeout with work in flight — killing harness processes")
                    killed = _kill_sandbox_processes(tmp)
                    if killed:
                        _log(f"sent SIGTERM/SIGKILL to {killed} process(es)")
                    drain_until = now + 45
                    while time.time() < drain_until:
                        time.sleep(2)
                        seen_velocity, seen_gov, seen_locks = _emit_progress(
                            seen_velocity=seen_velocity,
                            seen_gov=seen_gov,
                            seen_locks=seen_locks,
                        )
                        final = _final_answer(a8s_home) or final
                        if not _busy(a8s_home, repo):
                            _log("drained after timeout kill")
                            break
                else:
                    _log("timeout reached")
                break

            time.sleep(2)
            seen_velocity, seen_gov, seen_locks = _emit_progress(
                seen_velocity=seen_velocity,
                seen_gov=seen_gov,
                seen_locks=seen_locks,
            )
            final = _final_answer(a8s_home)
            if final is not None:
                _log("leader answered the human")
            if _busy(a8s_home, repo):
                quiet_polls = 0
                continue
            quiet_polls += 1
            # In break mode the scenario isn't over until the breaker has
            # tripped AND blocked a message — the leader's early ack to the
            # human must not end the run before either.
            breaker_pending = break_member and (
                not any(
                    "BREAKER" in line and "tripped" in line
                    for line in _governance_lines()
                )
                or not any(
                    "BREAKER" in line and "breaker open" in line
                    for line in _governance_lines()
                )
            )
            if final is not None and quiet_polls >= 2 and not breaker_pending:
                _log("quiescent with final answer")
                break
            if quiet_polls >= 20:
                _log("quiescent without final answer")
                break
            if quiet_polls == 1:
                _log("waiting for team to finish…")

        _log("stopping handlers")
        _kill_sandbox_processes(tmp)
        _stop_handlers(a8s_home)
        orphans = _orphans(tmp)
        final = final or _final_answer(a8s_home)

        repo_files = {
            p.name: p.read_text(encoding="utf-8")
            for p in sorted(repo.glob("*.py"))
        }
        program_ok, program_detail = _run_program(repo)
        turns = len(_velocity_rows())
        dead = _dead_letter_counts()

        checks: list[tuple[str, object, str]] = []
        if break_member:
            gov = _governance_lines()
            tripped = any("BREAKER" in line and "tripped" in line for line in gov)
            held = any("BREAKER" in line and "breaker open" in line for line in gov)
            checks += [
                (
                    "Breaker tripped",
                    tripped,
                    f"{break_member} pinned to an always-failing rig (breaker_cap 2)",
                ),
                (
                    "Breaker held queued message(s)",
                    held,
                    "messages hold in the queue while the breaker is open — none dropped",
                ),
            ]
        else:
            checks += [
                (
                    "Program file(s) created",
                    bool(repo_files),
                    ", ".join(sorted(repo_files)) or "no .py files in repo",
                ),
                ("Program runs and exits 0", program_ok, program_detail),
            ]
        checks += [
            (
                "Leader answered the originator",
                final is not None,
                (str(final.get("content", ""))[:120] if final else "no message from the node reached the human"),
            ),
            ("Turn count within budget", turns <= MAX_TURNS, f"{turns} turn(s) <= {MAX_TURNS}"),
            ("Zero orphan processes", not orphans, "; ".join(orphans) or "clean"),
            ("Dead letters", sum(dead.values()), json.dumps(dead) if dead else "none"),
        ]

        report = _build_report(
            mode=mode,
            wall_clock=time.time() - start,
            checks=checks,
            goal=goal,
            repo_files=repo_files,
            harness=harness_line,
        )
        failed = [name for name, result, _ in checks if isinstance(result, bool) and not result]
        sys.stdout.write(report)
        sys.stdout.flush()
        if failed:
            _log(f"FAILED checks: {', '.join(failed)}")
            return 1
        _log("all mechanical checks passed")
        return 0
    except SandboxError as e:
        _log(str(e))
        return 1
    finally:
        try:
            _kill_sandbox_processes(tmp)
        except Exception:
            pass
        try:
            _stop_handlers(a8s_home)
        except Exception:
            pass
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp, ignore_errors=True)
