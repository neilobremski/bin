"""Browser session management via playwright-cli.

Launches Chrome directly (no automation flags) and attaches playwright-cli via CDP.
"""
import os
import subprocess
import sys
import time

from constants import SESSION_NAME, CHROME_PROFILE_DIR, STATE_FILE

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT = 9222


def run(*args, timeout=30):
    """Run playwright-cli -s=b3t with given args. Returns CompletedProcess."""
    cmd = ["playwright-cli", f"-s={SESSION_NAME}"] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "Timeout")


def is_running():
    """Check if browser session is active."""
    result = run("eval", "() => true")
    return result.returncode == 0


def _chrome_running():
    """Check if Chrome is listening on CDP port."""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2)
        return True
    except Exception:
        return False


def open_browser():
    """Launch Chrome directly and attach playwright-cli via CDP."""
    if is_running():
        print("Browser already running.", file=sys.stderr)
        return 0

    if not _chrome_running():
        os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)
        cmd = [
            CHROME_PATH,
            f"--user-data-dir={CHROME_PROFILE_DIR}",
            f"--remote-debugging-port={CDP_PORT}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait for Chrome to start listening
        for _ in range(30):
            if _chrome_running():
                break
            time.sleep(0.2)
        else:
            print("ERROR: Chrome did not start in time.", file=sys.stderr)
            return 1

    # Attach playwright-cli to the running Chrome via CDP
    result = run("attach", f"--cdp=http://127.0.0.1:{CDP_PORT}", timeout=15)
    if result.returncode != 0:
        print(f"ERROR: Failed to attach: {result.stderr}", file=sys.stderr)
        return 1

    print("Browser opened.", file=sys.stderr)
    return 0


def close_browser():
    """Save state and close browser gracefully."""
    if not is_running():
        print("Browser not running.", file=sys.stderr)
        return 0

    state_path = _state_path()
    if state_path:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        run("state-save", state_path)
        print(f"State saved to {state_path}", file=sys.stderr)

    # Detach playwright from Chrome (doesn't kill the process)
    run("close")

    # Gracefully quit Chrome so it writes "clean exit" to profile
    import platform
    if platform.system() == "Darwin":
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to quit'],
            capture_output=True, timeout=5,
        )
    else:
        # Linux/Windows: find Chrome process using our profile and send SIGTERM
        import signal
        for proc_line in subprocess.run(
            ["pgrep", "-f", CHROME_PROFILE_DIR],
            capture_output=True, text=True,
        ).stdout.strip().split("\n"):
            if proc_line.strip():
                try:
                    os.kill(int(proc_line.strip()), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
    # Wait briefly for Chrome to finish writing profile
    for _ in range(20):
        if not _chrome_running():
            break
        time.sleep(0.25)

    print("Browser closed.", file=sys.stderr)
    return 0


def ensure_running():
    """Open browser if not already running."""
    if not is_running():
        return open_browser()
    return 0


def navigate(url):
    """Navigate to a URL."""
    ensure_running()
    result = run("goto", url)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        return 1
    return 0


def snapshot():
    """Take page accessibility snapshot, return text."""
    result = run("snapshot", timeout=10)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        return None
    return result.stdout


def _parse_eval_result(stdout):
    """Extract value from playwright-cli eval/run-code stdout."""
    import json
    import re

    if not stdout:
        return None

    # Prefer ### Result block when present
    m = re.search(r"### Result\s*\n\"((?:[^\"\\]|\\.)*)\"", stdout, re.DOTALL)
    if m:
        try:
            return json.loads(f'"{m.group(1)}"')
        except json.JSONDecodeError:
            pass

    for line in stdout.split("\n"):
        line = line.strip()
        if not line or line.startswith("###") or line.startswith("```"):
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, str):
                try:
                    return json.loads(parsed)
                except json.JSONDecodeError:
                    return parsed
            return parsed
        except json.JSONDecodeError:
            continue
    return stdout.strip() or None


def current_url():
    """Get current page URL."""
    result = run("eval", "() => window.location.href")
    if result.returncode == 0:
        url = _parse_eval_result(result.stdout)
        return url if isinstance(url, str) else None
    return None


def save_state():
    """Save current browser state."""
    state_path = _state_path()
    if state_path:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        run("state-save", state_path)


def _state_path():
    """Resolve state file path relative to project root (CWD)."""
    return os.path.join(os.getcwd(), STATE_FILE)
