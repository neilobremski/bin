"""Azure CLI helpers."""
from __future__ import annotations

import subprocess
import sys

_TAIL_TARGETS: dict[str, tuple[str, str]] = {
    "dev": ("app-developer-payi-dev2-westus3", "rg-payi-dev2-westus3"),
    "dev2": ("app-developer-payi-dev2-westus3", "rg-payi-dev2-westus3"),
    "qa": ("app-developer-payi-qa2-westus3", "rg-payi-qa2-westus3"),
    "qa2": ("app-developer-payi-qa2-westus3", "rg-payi-qa2-westus3"),
    "staging": ("app-developer-payi-stg-centralus", "rg-payi-stg-centralus"),
    "stg": ("app-developer-payi-stg-centralus", "rg-payi-stg-centralus"),
    "staging2": ("app-developer-payi-stg-centralus", "rg-payi-stg-centralus"),
    "prod": ("app-developer-payi-prod-centralus", "rg-payi-prod-centralus"),
}


def cmd_tail(env: str) -> int:
    key = env.lower()
    target = _TAIL_TARGETS.get(key)
    if target is None:
        print(f"Unknown environment: {env}", file=sys.stderr)
        print("Usage: n0b az tail <env>", file=sys.stderr)
        print("Available: dev, qa, staging, prod", file=sys.stderr)
        return 1
    app_name, resource_group = target
    rc = subprocess.run(
        [
            "az",
            "webapp",
            "log",
            "tail",
            "--name",
            app_name,
            "--resource-group",
            resource_group,
        ]
    ).returncode
    return rc if rc is not None else 1
