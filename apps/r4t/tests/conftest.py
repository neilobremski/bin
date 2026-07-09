"""pytest scaffolding for r4t."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG))


ROSTER_TEXT = textwrap.dedent(
    """\
    # Team Roster

    Preamble prose that is not a member block.

    ### Gerry
    - **Status:** AI
    - **Harness:** leader
    - **Role:** Technical Producer
    - **Leader:** yes

    The Orchestrator. Defends the schedule.

    ### Phil
    - **Status:** AI
    - **Harness:** junior-dev
    - **Role:** Lead Backend Engineer

    Grumpy, cynical veteran. Despises feature creep.

    ### Neil
    - **Status:** Human
    - **Address:** neil
    - **Role:** Game Director

    ### Broken
    - **Status:** Sometimes
    - **Harness:** junior-dev
    """
)


@pytest.fixture
def r4t_home(tmp_path, monkeypatch):
    home = tmp_path / "r4t-home"
    monkeypatch.setenv("R4T_HOME", str(home))
    return home


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "ROSTER.md").write_text(ROSTER_TEXT, encoding="utf-8")
    return root


@pytest.fixture
def fake_harness(tmp_path):
    """A tiny harness that records its prompt and echoes — no LLM calls."""
    script = tmp_path / "fake-harness.py"
    out = tmp_path / "harness-calls"
    out.mkdir()
    script.write_text(
        textwrap.dedent(
            f"""\
            import os, sys, time
            calls_dir = {str(out)!r}
            n = len(os.listdir(calls_dir))
            with open(os.path.join(calls_dir, f"call-{{n:03d}}.txt"), "w") as f:
                f.write(sys.argv[1])
            print("fake harness ran in", os.getcwd())
            """
        ),
        encoding="utf-8",
    )
    return script, out


@pytest.fixture
def harness_config(tmp_path, fake_harness):
    script, _out = fake_harness
    path = tmp_path / "harnesses.json"
    path.write_text(
        json.dumps(
            {
                "_comment": "test config",
                "leader": {
                    "invoke": [sys.executable, str(script), "{prompt}"],
                    "timeout_seconds": 30,
                    "concurrency": 2,
                    "max_turns_per_task": 4,
                    "hop_limit": 4,
                },
                "junior-dev": {
                    "invoke": [sys.executable, str(script), "{prompt}"],
                    "timeout_seconds": 30,
                    "concurrency": 1,
                    "max_turns_per_task": 2,
                    "hop_limit": 2,
                },
                "pins": {"_comment": "x", "gerry": "leader"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def chatty_harness(tmp_path):
    """A harness that records its prompt, then sends tells through whatever
    `tell` is first on PATH (i.e. the r4t shim). Send count via CHATTY_SENDS."""
    script = tmp_path / "chatty-harness.py"
    out = tmp_path / "chatty-calls"
    out.mkdir()
    script.write_text(
        textwrap.dedent(
            f"""\
            import os, subprocess, sys
            calls_dir = {str(out)!r}
            n = len(os.listdir(calls_dir))
            with open(os.path.join(calls_dir, f"call-{{n:03d}}.txt"), "w") as f:
                f.write(sys.argv[1])
            sends = int(os.environ.get("CHATTY_SENDS", "2"))
            for i in range(sends):
                r = subprocess.run(
                    ["tell", "gerry", f"reply number {{i}}"],
                    capture_output=True,
                    text=True,
                )
                sys.stdout.write(r.stdout + r.stderr)
                sys.stdout.write(f"tell-exit-{{i}}:{{r.returncode}}\\n")
            """
        ),
        encoding="utf-8",
    )
    return script, out


@pytest.fixture
def chatty_config(tmp_path, chatty_harness):
    script, _out = chatty_harness
    path = tmp_path / "chatty-harnesses.json"
    path.write_text(
        json.dumps(
            {
                "junior-dev": {
                    "invoke": [sys.executable, str(script), "{prompt}"],
                    "timeout_seconds": 30,
                    "max_turns_per_task": 10,
                    "max_sends_per_turn": 2,
                },
                "leader": {
                    "invoke": [sys.executable, str(script), "{prompt}"],
                    "timeout_seconds": 30,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def tells():
    sent: list[tuple[str, str]] = []

    def capture(agent: str, body: str) -> None:
        sent.append((agent, body))

    return sent, capture


@pytest.fixture
def ctx(r4t_home, repo, harness_config, tells):
    from dispatch import DispatchContext

    _sent, capture = tells
    return DispatchContext(
        root=repo,
        node="s1l",
        roster_path=repo / "ROSTER.md",
        config_path=harness_config,
        tell_fn=capture,
        tell_mode="drop",
    )
