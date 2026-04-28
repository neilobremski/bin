"""paho-mqtt Transport implementation.

Uses MQTT 3.1.1 with `clean_session=False` and QoS 1 so the broker holds
messages for an offline subscriber until reconnect — this is the persistent-
session shape decided in the #63 plan. The client_id needs to be stable
across runs (same machine, same a8s install) for the broker to recognize the
session and replay; we default to `a8s-<machine-hash>-<remote_id>`.

This module is import-soft: importing it requires `paho-mqtt`, but
`network.py` only imports it lazily so a8s with no remotes runs fine without
paho-mqtt installed. The mini-MQTT fallback (deferred to a follow-up PR)
will plug into the same `Transport` ABC.
"""
from __future__ import annotations

import hashlib
import socket
import threading
from typing import Optional
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

from transports import OnMessage, Transport, TransportError


def _default_client_id(remote_id: str) -> str:
    """Stable per-(host, remote) id. The hash is deterministic so the broker
    re-attaches us to the same persistent session on every restart."""
    h = hashlib.sha256(f"{socket.gethostname()}::{remote_id}".encode()).hexdigest()[:16]
    return f"a8s-{h}"


class PahoMqttTransport(Transport):
    """One configured MQTT remote.

    Args:
        remote_id: stable name from `network.json` (used for dedup keying).
        broker_url: e.g. `mqtt://broker.example:1883` or `mqtts://...:8883`.
        topic: the broadcast topic both publish and subscribe target.
        username/password: optional broker credentials.
        client_id: optional override (defaults to a stable hash of host + id).
        keepalive: MQTT keepalive seconds; default 60.
        connect_timeout_s: how long `start()` waits for the initial CONNACK
            before giving up. Setting this low (a few seconds) keeps a8s
            startup snappy when a broker is unreachable; the loop then
            keeps trying to reconnect in the background.
    """

    def __init__(
        self,
        remote_id: str,
        broker_url: str,
        topic: str,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: Optional[str] = None,
        keepalive: int = 60,
        connect_timeout_s: float = 5.0,
    ) -> None:
        self._remote_id = remote_id
        self._topic = topic
        self._keepalive = keepalive
        self._connect_timeout_s = connect_timeout_s
        parsed = urlparse(broker_url)
        if parsed.scheme not in ("mqtt", "mqtts"):
            raise ValueError(f"unsupported scheme {parsed.scheme!r} (expected mqtt or mqtts)")
        self._host = parsed.hostname or "localhost"
        self._port = parsed.port or (8883 if parsed.scheme == "mqtts" else 1883)
        self._tls = parsed.scheme == "mqtts"
        self._client_id = client_id or _default_client_id(remote_id)
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            clean_session=False,
        )
        if username is not None:
            self._client.username_pw_set(username, password)
        if self._tls:
            self._client.tls_set()
        self._connected = threading.Event()
        self._on_message: Optional[OnMessage] = None
        self._started = False

    @property
    def id(self) -> str:
        return self._remote_id

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0 or (hasattr(reason_code, "is_failure") and not reason_code.is_failure):
            client.subscribe(self._topic, qos=1)
            self._connected.set()

    def _on_message_cb(self, client, userdata, msg):
        cb = self._on_message
        if cb is not None:
            try:
                cb(msg.payload)
            except Exception:
                # The user's on_message must not bubble exceptions back into
                # paho's network thread (it would tear the loop down). Log via
                # core if needed, but never re-raise here.
                pass

    def start(self, on_message: OnMessage) -> None:
        if self._started:
            raise TransportError(f"{self._remote_id}: already started")
        self._on_message = on_message
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message_cb
        try:
            self._client.connect_async(self._host, self._port, keepalive=self._keepalive)
        except OSError as e:
            raise TransportError(f"{self._remote_id}: connect_async failed: {e}") from e
        self._client.loop_start()
        self._started = True
        # Wait for initial CONNACK so a fast-failing broker surfaces here
        # rather than silently buffering publishes. After this, paho's loop
        # auto-reconnects on disconnect.
        if not self._connected.wait(timeout=self._connect_timeout_s):
            # Don't raise — let the background loop keep retrying. publish()
            # below will fail until the connection comes up, which is the
            # right signal for the routing pass to log a warning and retry
            # via the per-message backoff.
            pass

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._client.disconnect()
        except OSError:
            pass
        self._client.loop_stop()
        self._started = False

    def publish(self, envelope: bytes) -> None:
        if not self._started:
            raise TransportError(f"{self._remote_id}: publish before start")
        if not self._client.is_connected():
            raise TransportError(f"{self._remote_id}: broker not connected")
        info = self._client.publish(self._topic, payload=envelope, qos=1)
        rc = info.rc
        if rc != mqtt.MQTT_ERR_SUCCESS:
            raise TransportError(f"{self._remote_id}: publish rc={rc}")
        try:
            info.wait_for_publish(timeout=self._connect_timeout_s)
        except (RuntimeError, ValueError) as e:
            raise TransportError(f"{self._remote_id}: wait_for_publish: {e}") from e
        if not info.is_published():
            raise TransportError(f"{self._remote_id}: publish not acknowledged")
