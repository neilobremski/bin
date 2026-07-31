"""n0b ai research — OpenAI deep research."""
from __future__ import annotations

import sys

from research import run_research, run_research_fanout


def cmd_research(args: list[str], fanout: int | None = None) -> int:
    if not args:
        print(
            "Usage: n0b ai research [--fanout N] <prompt...>",
            file=sys.stderr,
        )
        return 2
    if fanout is not None:
        return run_research_fanout(args, fanout)
    return run_research(args)
