from __future__ import annotations

import cli


def test_rm_is_a_known_command():
    assert "rm" in cli.KNOWN_COMMANDS
    assert "rm <name>" in cli.CLI_EPILOG


def test_rm_dispatches_to_remove(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cmd_remove", lambda args: calls.append(args) or 7)

    assert cli.dispatch("rm", ["alice"], interval=1.0) == 7
    assert calls == [["alice"]]


def test_remove_still_dispatches_to_same_handler(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cmd_remove", lambda args: calls.append(args) or 0)

    assert cli.dispatch("remove", ["alice"], interval=1.0) == 0
    assert calls == [["alice"]]
