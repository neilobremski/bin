"""MQTT publish/subscribe via mosquitto clients."""
from __future__ import annotations

import os
import subprocess
import sys


def _mqtt_base_args() -> list[str]:
    host = os.environ.get("MQTT_HOST", "localhost")
    port = os.environ.get("MQTT_PORT", "1883")
    args = ["-h", host, "-p", port]
    user = os.environ.get("MQTT_USER", "")
    password = os.environ.get("MQTT_PASS", "")
    if user:
        args.extend(["-u", user])
    if password:
        args.extend(["-P", password])
    if port == "8883":
        args.extend(["--capath", "/etc/ssl/certs"])
    return args


def cmd_pub(args: list[str]) -> int:
    import shutil

    if not shutil.which("mosquitto_pub"):
        print("mosquitto_pub not found on PATH", file=sys.stderr)
        return 1
    rc = subprocess.run(["mosquitto_pub", *_mqtt_base_args(), *args]).returncode
    return rc if rc is not None else 1


def cmd_sub(args: list[str]) -> int:
    import shutil

    if not shutil.which("mosquitto_sub"):
        print("mosquitto_sub not found on PATH", file=sys.stderr)
        return 1
    rc = subprocess.run(["mosquitto_sub", *_mqtt_base_args(), *args]).returncode
    return rc if rc is not None else 1
