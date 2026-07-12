"""r4t logs — the team's own event stream, compact by default."""
from __future__ import annotations

import state
from r4t import main as r4t_main

NODE = "acme"


def seed_log():
    state.append_log(NODE, "## 2026-07-11T10:00:00Z dispatch gerry -> Phil (task 01X hop 1, rig junior-dev)")
    state.append_log(NODE, "### Prompt\n\nYou are Phil, a member of the acme team.\nSecret persona details here.")
    state.append_log(NODE, "### Output (Phil, exit 0 in 2.0s)")
    state.append_log(NODE, "r4t: RELEASED-internal acme:phil -> acme:gerry task=01X hop=2")
    state.append_log(NODE, "r4t: SUPPRESSED acme:phil -> acme:gerry task=01X repeat=2")


def run_logs(*args):
    return r4t_main(["logs", "--node", NODE, *args])


def test_compact_shows_events_hides_transcripts(r4t_home, capsys):
    seed_log()
    assert run_logs() == 0
    out = capsys.readouterr().out
    assert "turn: gerry -> Phil (task 01X hop 1, rig junior-dev)" in out
    assert "done: Phil, exit 0 in 2.0s" in out
    assert "r4t: RELEASED-internal acme:phil -> acme:gerry task=01X hop=2" in out
    assert "r4t: SUPPRESSED" in out
    assert "Secret persona" not in out
    assert "### Prompt" not in out


def test_full_shows_raw_log(r4t_home, capsys):
    seed_log()
    assert run_logs("--full") == 0
    out = capsys.readouterr().out
    assert "Secret persona details here." in out
    assert "### Prompt" in out


def test_lines_limits_backfill(r4t_home, capsys):
    seed_log()
    assert run_logs("-n", "1") == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["r4t: SUPPRESSED acme:phil -> acme:gerry task=01X repeat=2"]


def test_no_log_yet(r4t_home, capsys):
    assert run_logs() == 0
    err = capsys.readouterr().err
    assert "no log yet" in err
