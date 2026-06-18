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
CDP_ATTACH_BASES = (
    f"http://127.0.0.1:{CDP_PORT}",
    f"http://localhost:{CDP_PORT}",
    f"http://[::1]:{CDP_PORT}",
)
CHROME_STARTUP_WAIT = 60  # 60 × 0.2s = 12s


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


def _chrome_cdp_base():
    """Return CDP base URL if Chrome is listening, else None."""
    import urllib.request
    for base in CDP_ATTACH_BASES:
        try:
            urllib.request.urlopen(f"{base}/json/version", timeout=2)
            return base
        except Exception:
            continue
    return None


def _chrome_running():
    """Check if Chrome is listening on CDP port."""
    return _chrome_cdp_base() is not None


def _profile_process_running():
    """True if Chrome is already using our profile (may be starting up)."""
    result = subprocess.run(
        ["pgrep", "-f", CHROME_PROFILE_DIR],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _wait_for_cdp():
    """Wait for CDP to become available. Returns base URL or None."""
    for _ in range(CHROME_STARTUP_WAIT):
        base = _chrome_cdp_base()
        if base:
            return base
        time.sleep(0.2)
    return None


def _launch_chrome():
    """Start Chrome with the b3t profile (first launch only)."""
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


def open_browser():
    """Launch Chrome directly and attach playwright-cli via CDP."""
    if is_running():
        print("Browser already running.", file=sys.stderr)
        return 0

    cdp_base = _chrome_cdp_base()
    if not cdp_base:
        if _profile_process_running():
            # Profile Chrome is up but CDP not ready yet — wait, do NOT relaunch
            # (relaunching with the same profile opens a new about:blank tab).
            cdp_base = _wait_for_cdp()
        else:
            _launch_chrome()
            cdp_base = _wait_for_cdp()

    if not cdp_base:
        print("ERROR: Chrome did not start in time.", file=sys.stderr)
        return 1

    # Attach playwright-cli to the running Chrome via CDP
    result = run("attach", f"--cdp={cdp_base}", timeout=15)
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
