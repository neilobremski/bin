"""n0b ai research — OpenAI deep research."""
from __future__ import annotations

import sys

from research import run_research, run_research_fanout


def cmd_research(
    args: list[str],
    fanout: int | None = None,
    *,
    plan_only: bool = False,
) -> int:
    if not args:
        print(
            "Usage: n0b ai research [--fanout [N]] [--plan-only] <prompt...>",
            file=sys.stderr,
        )
        return 2
    # --fanout absent or 1 → single-shot path unchanged.
    if fanout is None or fanout == 1:
        if plan_only:
            print('{"error": "--plan-only requires --fanout N with N>=2"}')
            return 1
        return run_research(args)
    return run_research_fanout(args, fanout, plan_only=plan_only)
