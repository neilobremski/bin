"""End-to-end remote-routing test (issue #63).

Spawns a real `mosquitto` broker, configures one cluster to send and
another to receive, and verifies a `tell` reaches the receiver's inbox via
the broker. The send side runs through `attached_loop` to exercise the
daemon's wiring of `publish_remotes`. The receive side uses `start_remotes`
directly so we can wait deterministically for the network thread to
deliver before tearing down — it would still go through `receive_envelope`
the same way `attached_loop` does, just without the timing complexity of
two daemons swapping HOMEs in one process.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytest.importorskip("paho.mqtt.client")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mqtt_broker(tmp_path):
    if shutil.which("mosquitto") is None:
        pytest.skip("mosquitto binary not on PATH")
    port = _free_port()
    conf = tmp_path / "mosquitto.conf"
    conf.write_text(
        f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n"
    )
    proc = subprocess.Popen(
        ["mosquitto", "-c", str(conf)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        proc.terminate()
        pytest.fail("mosquitto failed to start within 5s")
    yield port
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _write_network_json(home: Path, port: int, topic: str, client_id: str) -> None:
    a8s_dir = home / ".a8s"
    a8s_dir.mkdir(parents=True, exist_ok=True)
    (a8s_dir / "network.json").write_text(json.dumps({
        "remotes": {
            "hub": {
                "transport": "mqtt",
                "broker": f"mqtt://127.0.0.1:{port}",
                "topic": topic,
                "client_id": client_id,
            }
        }
    }))


def test_ask_reply_round_trip(tmp_path, mqtt_broker, monkeypatch):
    """An ask reply (from recipient cluster's handler back to the asker's
    transient name) rides MQTT just like any other envelope: the asker's
    cluster has a subscriber registered for the topic and `receive_envelope`
    delivers into the live transient's inbox when `to` matches."""
    topic = f"a8s/test-ask-{os.getpid()}-{int(time.time() * 1000)}"
    cluster_a_home = tmp_path / "askA"
    cluster_a_home.mkdir()
    cluster_b_home = tmp_path / "respB"
    cluster_b_home.mkdir()
    _write_network_json(cluster_a_home, mqtt_broker, topic, "a8s-test-askA")
    _write_network_json(cluster_b_home, mqtt_broker, topic, "a8s-test-respB")

    import core
    core.PRINT_LOCK = None

    # Cluster A registers its transient ASK_<ulid> first so the subscriber
    # we spin up will accept envelopes addressed to it. Pre-warm the
    # persistent session so the broker holds messages while B publishes.
    monkeypatch.setenv("HOME", str(cluster_a_home))
    monkeypatch.delenv("USERPROFILE", raising=False)
    import transient as transient_dirs
    from core import transient_inbox_dir
    from network import load_remotes, start_remotes, stop_remotes
    transient_name = "ASK_E2ETESTONLY"
    transient_dirs.register(transient_name)
    warmup = start_remotes(load_remotes(), lambda: [])
    stop_remotes(warmup)

    # Cluster B publishes the ask reply envelope through the supervisor
    # publish helper (same path the daemon's _deliver_ask_response uses for
    # remote askers).
    monkeypatch.setenv("HOME", str(cluster_b_home))
    core.PRINT_LOCK = None
    from network import publish_once_to_remotes
    from ulid import new as new_ulid
    reply = {
        "id": new_ulid(),
        "date": "2026-04-28T00:00:00Z",
        "from": "TARGET",
        "to": transient_name,
        "content": "the answer is 42",
        "files": [],
    }
    succeeded, failed = publish_once_to_remotes(reply)
    assert succeeded, f"publish failed: {failed}"

    # Cluster A reconnects its subscriber; broker replays the held message.
    monkeypatch.setenv("HOME", str(cluster_a_home))
    core.PRINT_LOCK = None
    rx_remotes = start_remotes(load_remotes(), lambda: [])
    try:
        deadline = time.time() + 5.0
        files: list[Path] = []
        while time.time() < deadline:
            inbox = transient_inbox_dir(transient_name)
            if inbox.is_dir():
                files = list(inbox.iterdir())
            if files:
                break
            time.sleep(0.1)
        assert files, "ask reply did not arrive at the transient inbox"
        body = json.loads(files[0].read_text())
        assert body["from"] == "TARGET"
        assert body["to"] == transient_name
        assert body["content"] == "the answer is 42"
    finally:
        stop_remotes(rx_remotes)
        transient_dirs.cleanup(transient_name)


def test_remote_round_trip(tmp_path, mqtt_broker, monkeypatch):
    """Sender publishes via attached_loop's remote-routing wiring; a
    receiver running start_remotes on the same broker writes the envelope
    into a local agent's inbox."""
    topic = f"a8s/test-{os.getpid()}-{int(time.time() * 1000)}"
    cluster_a_home = tmp_path / "clusterA"
    cluster_a_home.mkdir()
    cluster_b_home = tmp_path / "clusterB"
    cluster_b_home.mkdir()
    _write_network_json(cluster_a_home, mqtt_broker, topic, "a8s-test-clusterA")
    _write_network_json(cluster_b_home, mqtt_broker, topic, "a8s-test-clusterB")

    import core
    core.PRINT_LOCK = None

    # The two clusters run in one process here, so HOME has to flip between
    # them. We can't have a live receive loop while HOME is set to the
    # sender's value (resolve_name in the receive callback reads HOME's
    # registry). The fix is to pre-register cluster B's persistent session
    # (clean_session=False + QoS 1 → broker holds messages for that
    # client_id), publish from A while B's subscriber is offline, then
    # bring B's subscriber back up and let the broker replay.

    monkeypatch.setenv("HOME", str(cluster_b_home))
    monkeypatch.delenv("USERPROFILE", raising=False)
    target_root = cluster_b_home / "target"
    target_root.mkdir()
    from registry import save_registry
    from mailbox import ensure_mailboxes
    from core import Participant, inbox_dir
    save_registry({"TARGET": {"root": str(target_root)}})
    target_p = Participant("TARGET", target_root)
    ensure_mailboxes(target_p)

    # Step 1: warm-up — connect, register the persistent session, disconnect.
    from network import load_remotes, start_remotes, stop_remotes
    warmup = start_remotes(load_remotes(), lambda: [target_p])
    stop_remotes(warmup)

    # Step 2: cluster A publishes via attached_loop.
    monkeypatch.setenv("HOME", str(cluster_a_home))
    core.PRINT_LOCK = None
    sender_root = cluster_a_home / "sender"
    sender_root.mkdir()
    save_registry({"SENDER": {"root": str(sender_root)}})
    sender_p = Participant("SENDER", sender_root)
    ensure_mailboxes(sender_p)
    from mailbox import _write_outbox
    _write_outbox("SENDER", sender_root, "TARGET", "ping from A", [])
    from daemon import attached_loop
    rc = attached_loop(["SENDER"], 0.2, single_pass=True)
    assert rc == 0

    # Step 3: cluster B reconnects with the same client_id; broker replays.
    monkeypatch.setenv("HOME", str(cluster_b_home))
    core.PRINT_LOCK = None
    rx_remotes = start_remotes(load_remotes(), lambda: [target_p])
    try:
        deadline = time.time() + 5.0
        files: list[Path] = []
        while time.time() < deadline:
            files = list(inbox_dir("TARGET").iterdir())
            if files:
                break
            time.sleep(0.1)
        assert files, "envelope did not arrive at TARGET via the remote"
        body = json.loads(files[0].read_text())
        assert body["from"] == "SENDER"
        assert body["content"] == "ping from A"
        assert body["to"] == "TARGET"
    finally:
        stop_remotes(rx_remotes)
