#!/usr/bin/env python3
"""Thin wrapper — implementation lives in apps/n0b/research.py."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "apps" / "n0b"))

from research import run_research  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_research(sys.argv[1:]))
