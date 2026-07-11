"""End-to-end fake-sandbox run. Live mode is never run from pytest."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

R4T_PY = Path(__file__).resolve().parent.parent / "r4t.py"


def test_fake_sandbox_end_to_end():
    result = subprocess.run(
        [
            sys.executable,
            str(R4T_PY),
            "sandbox",
            "--fake",
            "--timeout",
            "240",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = result.stdout
    assert "sandbox:" in result.stderr

    mechanical = report.split("## Mechanical checks", 1)[1].split("## Run", 1)[0]
    for check in (
        "Program file(s) created",
        "Program runs and exits 0",
        "Leader answered the originator",
        "Turn count within budget",
        "Zero orphan processes",
        "Dead letters",
        "Hop cuts",
    ):
        assert check in mechanical
    assert "| FAIL |" not in mechanical
    assert mechanical.count("| PASS |") >= 5

    assert "battleship.py" in report
    assert "SHIPS" in report  # produced source is inlined
    assert re.search(r"\| \S+ \| lead \| leader \|", report)  # velocity table rows
    assert "crew:lead" in report  # conversation section
    assert "human" in report


def test_fake_sandbox_breaker_trips_and_task_still_closes():
    result = subprocess.run(
        [
            sys.executable,
            str(R4T_PY),
            "sandbox",
            "--fake",
            "--break",
            "dev",
            "--timeout",
            "240",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = result.stdout

    mechanical = report.split("## Mechanical checks", 1)[1].split("## Run", 1)[0]
    for check in (
        "Breaker tripped",
        "Breaker blocked message(s)",
        "Leader answered the originator",
        "Zero orphan processes",
    ):
        assert check in mechanical
    assert "| FAIL |" not in mechanical
    assert "BREAKER dev tripped" in report  # governance events section
    assert "breaker-open" in report
