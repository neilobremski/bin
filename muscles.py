"""muscles — Python bindings for the life system's CLI tools.

Wraps subprocess calls so organs can use native Python instead of shelling out.
Stdlib only. Fails gracefully (returns None/empty, never raises).

Usage:
    import muscles

    muscles.stimulus.send("tail", "swim now")
    ref = muscles.circ.put("payload data")
    result = muscles.gas("sheets.read", name="Tadpole")
    response = muscles.llm("What is 2+2?")
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Override to force a specific directory for CLI lookups.
# If set, tools are resolved as BIN_DIR/<tool> instead of PATH lookup.
BIN_DIR = None

# Default subprocess timeout in seconds.
DEFAULT_TIMEOUT = 30


def _find(tool):
    """Locate a CLI tool. Returns full path or None."""
    if BIN_DIR:
        candidate = os.path.join(BIN_DIR, tool)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(tool)


def _run(tool, args, timeout=None, stdin_data=None):
    """Run a CLI tool and return (stdout, returncode). Never raises."""
    path = _find(tool)
    if path is None:
        return None, -1
    timeout = timeout or DEFAULT_TIMEOUT
    try:
        result = subprocess.run(
            [path] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin_data,
        )
        return result.stdout, result.returncode
    except (subprocess.TimeoutExpired, OSError):
        return None, -1


def _run_bytes(tool, args, timeout=None, stdin_bytes=None):
    """Run a CLI tool and return (stdout_bytes, returncode). Never raises."""
    path = _find(tool)
    if path is None:
        return None, -1
    timeout = timeout or DEFAULT_TIMEOUT
    try:
        result = subprocess.run(
            [path] + list(args),
            capture_output=True,
            timeout=timeout,
            input=stdin_bytes,
        )
        return result.stdout, result.returncode
    except (subprocess.TimeoutExpired, OSError):
        return None, -1


# ---- Stimulus (nervous system) ----

class _Stimulus:
    """Wrapper for the `stimulus` CLI."""

    def send(self, target, message, timeout=None):
        """Send a stimulus signal. Returns True on success."""
        _, rc = _run("stimulus", ["send", str(target), str(message)], timeout=timeout)
        return rc == 0

    def consume(self, directory=None, timeout=None):
        """Consume stimulus.txt from a directory. Returns content string or empty."""
        args = ["consume"]
        if directory:
            args.append(str(directory))
        out, rc = _run("stimulus", args, timeout=timeout)
        if rc == 0 and out:
            return out
        return ""

    def query(self, organ_type=None, timeout=None):
        """Query the organ registry. Returns list of dicts."""
        args = ["query"]
        if organ_type:
            args.append(str(organ_type))
        out, rc = _run("stimulus", args, timeout=timeout)
        if rc != 0 or not out:
            return []
        results = []
        fields = ["type", "id", "body_part", "health_status", "health_text", "last_seen"]
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= len(fields):
                results.append(dict(zip(fields, parts[:len(fields)])))
            elif parts:
                # Partial row — pad with empty strings
                row = dict(zip(fields, parts + [""] * (len(fields) - len(parts))))
                results.append(row)
        return results


stimulus = _Stimulus()


# ---- Memory (hippocampus) ----

class _Memories:
    """Wrapper for the `memories` CLI."""

    def store(self, content, importance=5, category="general", timeout=None):
        """Store a memory. Returns True on success."""
        args = ["store", str(content), "-i", str(importance), "-c", str(category)]
        _, rc = _run("memories", args, timeout=timeout)
        return rc == 0

    def search(self, query, limit=10, timeout=None):
        """Search memories. Returns list of dicts."""
        args = ["search", str(query)]
        return self._parse_json("memories", args, timeout)

    def recent(self, count=10, timeout=None):
        """Get recent memories. Returns list of dicts."""
        args = ["recent", str(count)]
        return self._parse_json("memories", args, timeout)

    def important(self, count=10, timeout=None):
        """Get important memories. Returns list of dicts."""
        args = ["important", str(count)]
        return self._parse_json("memories", args, timeout)

    def stats(self, timeout=None):
        """Get memory stats. Returns dict or None."""
        out, rc = _run("memories", ["stats"], timeout=timeout)
        if rc != 0 or not out:
            return None
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return {"raw": out.strip()}

    def _parse_json(self, tool, args, timeout):
        out, rc = _run(tool, args, timeout=timeout)
        if rc != 0 or not out:
            return []
        try:
            data = json.loads(out)
            return data if isinstance(data, list) else [data]
        except (json.JSONDecodeError, ValueError):
            return []


memories = _Memories()


# ---- Circulatory system ----

class _Circ:
    """Wrapper for circ-put / circ-get."""

    def put(self, data, timeout=None):
        """Store data in the circulatory system. Returns hash ref or None.

        `data` can be a string, bytes, or a file path.
        """
        if isinstance(data, bytes):
            out, rc = _run_bytes("circ-put", ["-"], timeout=timeout, stdin_bytes=data)
            if rc == 0 and out:
                return out.decode().strip()
            return None

        # If it looks like a file path and exists, pass it directly
        if os.path.isfile(data):
            out, rc = _run("circ-put", [data], timeout=timeout)
            if rc == 0 and out:
                return out.strip()
            return None

        # Otherwise treat as string content piped via stdin
        out, rc = _run("circ-put", ["-"], timeout=timeout, stdin_data=data)
        if rc == 0 and out:
            return out.strip()
        return None

    def get(self, ref, timeout=None):
        """Retrieve data by hash ref. Returns content string or None."""
        out, rc = _run("circ-get", [str(ref)], timeout=timeout)
        if rc == 0 and out is not None:
            return out
        return None


circ = _Circ()


# ---- GAS bridge ----

def gas(action, *positional, timeout=None, **kwargs):
    """Call the GAS bridge CLI. Returns parsed JSON dict or None.

    Supports both styles:
        muscles.gas("sheets.read", name="Tadpole")           # kwargs
        muscles.gas("sheets.read", "name=Tadpole", "count=5")  # positional strings
    """
    args = [str(action)]
    # Positional string args (already in "key=value" form)
    for p in positional:
        args.append(str(p))
    # Keyword args converted to "key=value"
    for k, v in kwargs.items():
        if isinstance(v, (dict, list)):
            args.append(f"{k}={json.dumps(v)}")
        elif isinstance(v, bool):
            args.append(f"{k}={'true' if v else 'false'}")
        else:
            args.append(f"{k}={v}")
    out, rc = _run("gas", args, timeout=timeout)
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return None


# ---- Small LLM ----

def llm(prompt, system=None, timeout=60):
    """Run a lightweight LLM inference via small-llm CLI.

    Returns response string or None.
    """
    args = [str(prompt)]
    if system:
        args = ["--system", str(system)] + args
    out, rc = _run("small-llm", args, timeout=timeout)
    if rc == 0 and out:
        return out.strip()
    return None


# ---- MQTT ----

class _MQTT:
    """Wrapper for mqtt-pub / mqtt-sub."""

    def pub(self, topic, message, qos=0, retain=False, timeout=None):
        """Publish a message. Returns True on success."""
        args = ["-t", str(topic), "-m", str(message)]
        if qos:
            args += ["-q", str(qos)]
        if retain:
            args.append("-r")
        _, rc = _run("mqtt-pub", args, timeout=timeout or 10)
        return rc == 0

    def sub(self, topic, wait=2, count=1, verbose=False, timeout=None):
        """Subscribe and collect messages. Returns list of strings.

        This is for short-lived subscriptions (fire-and-collect).
        For persistent subscriptions, use subprocess directly.
        """
        args = ["-t", str(topic), "-W", str(wait), "-C", str(count)]
        if verbose:
            args.append("-v")
        out, rc = _run("mqtt-sub", args, timeout=timeout or (wait + 10))
        if rc in (0, 27) and out:  # 27 = mosquitto_sub timeout exit
            return [line for line in out.strip().splitlines() if line]
        return []


mqtt = _MQTT()
