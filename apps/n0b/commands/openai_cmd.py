"""OpenAI deep research."""
from __future__ import annotations

import sys

from research import run_research


def cmd_research(args: list[str]) -> int:
    if not args:
        print("Usage: n0b openai research <prompt...>", file=sys.stderr)
        return 2
    return run_research(args)
