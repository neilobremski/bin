"""Inbound-side tests for gmail_cron — mock bridge HTTP and `tell`
shell-out, assert the unread → get → strip → resolve → tell → read flow."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_CONNECTOR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "connectors" / "gmail"
sys.path.insert(0, str(_CONNECTOR_DIR))

import gmail_cron  # noqa: E402


# ---------- helpers ----------

def _write_def(tmp_path: Path, to_addr: str = "human@example.com") -> Path:
    """Write a connector definition that references --to <to_addr>."""
    p = tmp_path / "neil-gmail.json"
    p.write_text(json.dumps({
        "description": "test",
        "invoke": [
            "python3",
            "$A8S_DIR/connectors/gmail/gmail_connector.py",
            "--to", to_addr,
            "--subject", "$SENDER",
            "--body", "$MESSAGE",
        ],
    }))
    return p


def _register_connector(fake_home: Path, def_path: Path, name: str = "gmailbot",
                        root: Path | None = None) -> Path:
    """Write a registry entry that uses `def_path` as the connector's
    definition. Also registers a separate target participant `NEIL` so the
    reply subject in tests resolves to a participant distinct from the
    connector itself. Returns the connector root."""
    import registry as reg
    if root is None:
        root = fake_home / "connector-root"
    root.mkdir(parents=True, exist_ok=True)
    target_root = fake_home / "neil-target"
    target_root.mkdir(parents=True, exist_ok=True)
    reg.save_registry({
        name: {"root": str(root), "definition": str(def_path)},
        "NEIL": {"root": str(target_root)},
    })
    return root


class _BridgeStub:
    """Records gmail.* POSTs and serves canned responses by action."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict) -> dict:
        self.calls.append({"url": url, "payload": payload})
        action = payload.get("action")
        resp = self.responses.get(action)
        if resp is None:
            raise AssertionError(f"unexpected bridge action: {action}")
        if callable(resp):
            return resp(payload)
        return resp


def _calls_for(stub: _BridgeStub, action: str) -> list[dict]:
    return [c for c in stub.calls if c["payload"].get("action") == action]


# ---------- tests ----------

def test_happy_path_unread_to_tell(monkeypatch, fake_home, tmp_path):
    monkeypatch.setenv("GAS_BRIDGE_URL", "https://example/exec")
    monkeypatch.setenv("GAS_BRIDGE_KEY", "TESTKEY")

    def_path = _write_def(tmp_path, to_addr="human@example.com")
    root = _register_connector(fake_home, def_path)

    bridge = _BridgeStub({
        "gmail.search": {"messages": [{"id": "T1"}], "count": 1},
        "gmail.get": {
            "thread_id": "T1",
            "messages": [{
                "subject": "Re: NEIL",
                "from": "human@example.com",
                "plain": "thanks for the message",
            }],
            "count": 1,
        },
        "gmail.read": {"status": "marked_read", "thread_id": "T1"},
    })

    tell_calls: list = []

    class _Result:
        returncode = 0

    def fake_run(argv, cwd=None, check=False):
        tell_calls.append({"argv": list(argv), "cwd": cwd, "check": check})
        return _Result()

    with patch.object(gmail_cron, "_bridge_post", bridge), \
         patch.object(gmail_cron.subprocess, "run", fake_run):
        rc = gmail_cron.run(def_path)

    assert rc == 0

    # Search query is is:unread from:<configured-to>
    search = _calls_for(bridge, "gmail.search")
    assert len(search) == 1
    assert search[0]["payload"]["query"] == "is:unread from:human@example.com"
    assert search[0]["payload"]["count"] == 20
    assert search[0]["payload"]["key"] == "TESTKEY"

    # gmail.get with the thread id
    gets = _calls_for(bridge, "gmail.get")
    assert len(gets) == 1
    assert gets[0]["payload"]["thread_id"] == "T1"

    # tell <name> <body>, cwd = connector root
    assert len(tell_calls) == 1
    assert tell_calls[0]["argv"] == ["tell", "NEIL", "thanks for the message"]
    assert Path(tell_calls[0]["cwd"]).resolve() == root.resolve()

    # gmail.read called only after tell succeeded
    reads = _calls_for(bridge, "gmail.read")
    assert len(reads) == 1
    assert reads[0]["payload"]["thread_id"] == "T1"


def test_unknown_participant_skipped(monkeypatch, fake_home, tmp_path):
    monkeypatch.setenv("GAS_BRIDGE_URL", "https://example/exec")
    monkeypatch.setenv("GAS_BRIDGE_KEY", "TESTKEY")

    def_path = _write_def(tmp_path, to_addr="human@example.com")
    _register_connector(fake_home, def_path)

    bridge = _BridgeStub({
        "gmail.search": {"messages": [{"id": "T2"}], "count": 1},
        "gmail.get": {
            "thread_id": "T2",
            "messages": [{
                "subject": "Re: NEVER-REGISTERED",
                "from": "human@example.com",
                "plain": "this should be skipped",
            }],
            "count": 1,
        },
        "gmail.read": {"status": "marked_read"},
    })

    tell_calls: list = []

    def fake_run(argv, cwd=None, check=False):
        tell_calls.append(argv)
        raise AssertionError("tell must NOT be called for unknown participant")

    with patch.object(gmail_cron, "_bridge_post", bridge), \
         patch.object(gmail_cron.subprocess, "run", fake_run):
        rc = gmail_cron.run(def_path)

    assert rc == 0  # no attempts means no failure
    # No tell, no gmail.read
    assert tell_calls == []
    assert _calls_for(bridge, "gmail.read") == []


def test_tell_failure_leaves_unread(monkeypatch, fake_home, tmp_path):
    monkeypatch.setenv("GAS_BRIDGE_URL", "https://example/exec")
    monkeypatch.setenv("GAS_BRIDGE_KEY", "TESTKEY")

    def_path = _write_def(tmp_path)
    _register_connector(fake_home, def_path)

    bridge = _BridgeStub({
        "gmail.search": {"messages": [{"id": "T3"}], "count": 1},
        "gmail.get": {
            "thread_id": "T3",
            "messages": [{
                "subject": "Re: NEIL",
                "plain": "should fail",
            }],
            "count": 1,
        },
        "gmail.read": {"status": "marked_read"},
    })

    class _Result:
        returncode = 5

    def fake_run(argv, cwd=None, check=False):
        return _Result()

    with patch.object(gmail_cron, "_bridge_post", bridge), \
         patch.object(gmail_cron.subprocess, "run", fake_run):
        rc = gmail_cron.run(def_path)

    # We attempted once and it failed → exit 1
    assert rc == 1
    # gmail.read NOT called when tell failed
    assert _calls_for(bridge, "gmail.read") == []


def test_no_unread_returns_zero(monkeypatch, fake_home, tmp_path):
    monkeypatch.setenv("GAS_BRIDGE_URL", "https://example/exec")
    monkeypatch.setenv("GAS_BRIDGE_KEY", "TESTKEY")

    def_path = _write_def(tmp_path)
    _register_connector(fake_home, def_path)

    bridge = _BridgeStub({
        "gmail.search": {"messages": [], "count": 0},
    })

    with patch.object(gmail_cron, "_bridge_post", bridge):
        rc = gmail_cron.run(def_path)
    assert rc == 0


def test_missing_env_vars_exit_2(monkeypatch, fake_home, tmp_path, capsys):
    monkeypatch.delenv("GAS_BRIDGE_URL", raising=False)
    monkeypatch.delenv("GAS_BRIDGE_KEY", raising=False)
    def_path = _write_def(tmp_path)
    _register_connector(fake_home, def_path)
    rc = gmail_cron.run(def_path)
    assert rc == 2
    err = capsys.readouterr().err
    assert "GAS_BRIDGE" in err


def test_definition_without_to_errors(monkeypatch, fake_home, tmp_path):
    monkeypatch.setenv("GAS_BRIDGE_URL", "https://example/exec")
    monkeypatch.setenv("GAS_BRIDGE_KEY", "TESTKEY")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "description": "missing --to",
        "invoke": ["python3", "/some/script.py", "--subject", "$SENDER"],
    }))
    with pytest.raises(SystemExit) as ei:
        gmail_cron.run(bad)
    assert "no '--to'" in str(ei.value)


def test_unregistered_definition_exit_2(monkeypatch, fake_home, tmp_path, capsys):
    monkeypatch.setenv("GAS_BRIDGE_URL", "https://example/exec")
    monkeypatch.setenv("GAS_BRIDGE_KEY", "TESTKEY")
    def_path = _write_def(tmp_path)
    # Note: NOT registered with a8s.
    rc = gmail_cron.run(def_path)
    assert rc == 2
    err = capsys.readouterr().err
    assert "no registered agent" in err


def test_force_stamp_via_cwd(monkeypatch, fake_home, tmp_path):
    """The cwd used for the tell call must be the registered connector
    root — that is what makes a8s force-stamp `from` to the connector's
    participant name (e.g. `neil`). This is the security-critical
    invariant for the inbound side."""
    monkeypatch.setenv("GAS_BRIDGE_URL", "https://example/exec")
    monkeypatch.setenv("GAS_BRIDGE_KEY", "TESTKEY")

    def_path = _write_def(tmp_path, to_addr="human@example.com")
    custom_root = tmp_path / "weird/place/connector"
    root = _register_connector(fake_home, def_path, root=custom_root)

    bridge = _BridgeStub({
        "gmail.search": {"messages": [{"id": "T4"}], "count": 1},
        "gmail.get": {
            "messages": [{"subject": "Re: NEIL", "plain": "hi"}],
        },
        "gmail.read": {"status": "marked_read"},
    })

    captured_cwd: list = []

    class _Result:
        returncode = 0

    def fake_run(argv, cwd=None, check=False):
        captured_cwd.append(cwd)
        return _Result()

    with patch.object(gmail_cron, "_bridge_post", bridge), \
         patch.object(gmail_cron.subprocess, "run", fake_run):
        rc = gmail_cron.run(def_path)

    assert rc == 0
    assert len(captured_cwd) == 1
    assert Path(captured_cwd[0]).resolve() == root.resolve()
