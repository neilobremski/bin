"""`r4t sandbox` — disposable end-to-end team run with a graded report.

Creates a temp dir holding a private A8S_HOME + R4T_HOME, copies the
bundled team-of-3 seed (apps/r4t/sandbox/) into a temp repo, registers the
node + namespace through the real a8s CLI, starts a handler, kicks off the
GOAL.md task as a registered "human" agent, waits for quiescence, tears
everything down (a8s stop is a graceful SIGTERM; the no-orphans invariant
is verified with a process scan), and writes one self-contained markdown
report whose MECHANICAL CHECKS section is computed — an external judge
needs nothing but the report.

`--fake` swaps every tier's invoke for sandbox/fake-agent.py: scripted
role-play that exercises dispatch, staging release, header stamping,
delegation, and the final leader answer with zero LLM calls. Live mode uses
`--preset` (any `r4t harness presets` entry; default `opencode`) and
optional `--model` for presets like `opencode-ollama`. The chosen argv is
passed to live-agent.py via R4T_SANDBOX_INVOKE.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import state

from harness import HarnessError, build_preset_invoke, format_preset_invoke, preset_names

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


def _write_harness_config(path: Path, fake: bool) -> None:
    config = json.loads((SANDBOX_DIR / "harnesses.json").read_text(encoding="utf-8"))
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
    state.atomic_write_json(path, config)


def _kickoff(human_root: Path, goal: str, repo: Path) -> None:
    outbox = human_root / ".outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    msg_id = f"{time.time_ns():026d}"
    workspace = repo.resolve()
    state.atomic_write_json(
        outbox / f"{msg_id}.json",
        {
            "id": msg_id,
            "to": f"{TEAM}:lead",
            "content": (
                "Please build this and report back to me when it is done and "
                "verified. All project files must live in the team repo root "
                f"({workspace}) using relative paths only — never ~/ or "
                "paths outside that directory:\n\n" + goal
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
        if msg.get("from") == NODE and str(msg.get("content", "")).strip():
            return msg
    return None


def _busy(a8s_home: Path, repo: Path) -> bool:
    if state.live_locks(TEAM):
        return True
    if state.list_pending(TEAM):
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
        "| time | agent | tier | task | hop | seconds | exit |",
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
    out: Path,
    preset: str = "opencode",
    model: str | None = None,
) -> int:
    start = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="r4t-sandbox-"))
    saved_env = {k: os.environ.get(k) for k in ("A8S_HOME", "R4T_HOME", "R4T_SANDBOX_INVOKE")}
    a8s_home = tmp / "a8s-home"
    os.environ["A8S_HOME"] = str(a8s_home)
    os.environ["R4T_HOME"] = str(tmp / "r4t-home")
    mode = "fake" if fake else "live"
    harness_line = ""
    try:
        if fake:
            os.environ.pop("R4T_SANDBOX_INVOKE", None)
        else:
            try:
                invoke = build_preset_invoke(preset, model=model)
            except HarnessError as e:
                print(f"sandbox: {e}", file=sys.stderr)
                return 1
            os.environ["R4T_SANDBOX_INVOKE"] = json.dumps(invoke)
            harness_line = format_preset_invoke(preset.strip().lower())
            if model:
                harness_line = f"{preset} (model={model}) — {harness_line}"
            else:
                harness_line = f"{preset} — {harness_line}"
            print(f"sandbox: harness {harness_line}", file=sys.stderr)
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

        _write_harness_config(tmp / "r4t-home" / "harnesses.json", fake)
        definition = tmp / "r4t-def.json"
        _write_definition(definition)
        human_root = tmp / "human"
        human_root.mkdir()

        _a8s("add", NODE, str(repo), str(definition))
        _a8s("add", "human", str(human_root), str(A8S_DIR / "definitions" / "default.json"))
        _a8s("namespace", TEAM, NODE)
        _a8s("alias", ALIAS, NODE)
        _a8s("alias", ALIAS, "human")
        _a8s("start", ALIAS)

        _kickoff(human_root, goal, repo)

        deadline = time.time() + timeout
        quiet_polls = 0
        final = None
        while time.time() < deadline:
            time.sleep(2)
            final = _final_answer(a8s_home)
            if _busy(a8s_home, repo):
                quiet_polls = 0
                continue
            quiet_polls += 1
            if final is not None and quiet_polls >= 2:
                break
            if quiet_polls >= 20:
                break

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
        suppressions = dead.get("pair-repeat", 0) + dead.get("bulk-window", 0)

        checks: list[tuple[str, object, str]] = [
            (
                "Program file(s) created",
                bool(repo_files),
                ", ".join(sorted(repo_files)) or "no .py files in repo",
            ),
            ("Program runs and exits 0", program_ok, program_detail),
            (
                "Leader answered the originator",
                final is not None,
                (str(final.get("content", ""))[:120] if final else "no message from the node reached the human"),
            ),
            ("Turn count within budget", turns <= MAX_TURNS, f"{turns} turn(s) <= {MAX_TURNS}"),
            ("Zero orphan processes", not orphans, "; ".join(orphans) or "clean"),
            ("Dead letters", sum(dead.values()), json.dumps(dead) if dead else "none"),
            ("Suppressions", suppressions, ""),
            ("Hop cuts", dead.get("hop-cut", 0), ""),
        ]

        report = _build_report(
            mode=mode,
            wall_clock=time.time() - start,
            checks=checks,
            goal=goal,
            repo_files=repo_files,
            harness=harness_line,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        failed = [name for name, result, _ in checks if isinstance(result, bool) and not result]
        print(f"sandbox ({mode}): report written to {out}")
        if failed:
            print(f"sandbox: FAILED checks: {', '.join(failed)}", file=sys.stderr)
            return 1
        print("sandbox: all mechanical checks passed")
        return 0
    except SandboxError as e:
        print(f"sandbox: {e}", file=sys.stderr)
        return 1
    finally:
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
