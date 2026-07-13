"""Portable org resolution — where the team's documents live vs. where it works.

By default a team lives IN its repo: ROSTER.md and MISSION.md sit at the repo
root, which is also where turns run and commits land. This is the slow-furnace
default — a proven structure graduates INTO the repo.

A portable org splits the two: an org directory holds ROSTER.md + MISSION.md +
a small `r4t-org.json` naming the workplace repo. That lets two org dirs — same
MISSION, different ROSTERs — point at two clones of one project (the A/B case:
a novella-writing experiment with identical intent and different casts). Team
state never collides because it is keyed per a8s node, not per repo.

Precedence, decided once, here:

- If `<root>/r4t-org.json` exists and names a `repo`, `<root>` is the ORG DIR:
  ROSTER.md and MISSION.md are read from `<root>`, and turns run in `repo`
  (relative `repo` paths resolve against the org dir).
- Otherwise `<root>` is both org dir and workplace — the in-repo default.

Graduation is trivial: copy ROSTER.md + MISSION.md into the repo and delete
r4t-org.json. Resolution falls back to the in-repo default with no other change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ORG_CONFIG_NAME = "r4t-org.json"


class OrgError(Exception):
    pass


@dataclass
class Org:
    dir: Path
    workplace: Path

    @property
    def is_portable(self) -> bool:
        return self.dir != self.workplace


def org_config_path(root: Path) -> Path:
    return root / ORG_CONFIG_NAME


def _read_repo(root: Path) -> Path | None:
    cfg = org_config_path(root)
    if not cfg.is_file():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise OrgError(f"cannot read org config {cfg}: {e}") from e
    if not isinstance(data, dict):
        raise OrgError(f"org config {cfg} must be a JSON object")
    raw = str(data.get("repo", "")).strip()
    if not raw:
        raise OrgError(f'org config {cfg} must set "repo" (the workplace repo path)')
    repo = Path(raw).expanduser()
    if not repo.is_absolute():
        repo = (root / repo).resolve()
    return repo


def load_org(root: Path) -> Org:
    """Resolve `root` to (org dir, workplace) for path building. Never raises —
    a malformed org config degrades to the in-repo default; `check_org` is the
    boundary that reports it."""
    try:
        repo = _read_repo(root)
    except OrgError:
        repo = None
    return Org(dir=root, workplace=repo or root)


def check_org(root: Path) -> list[str]:
    """Validation for `roster check`: a malformed org config, or a workplace
    repo that does not exist. Empty when there is no org config (in-repo)."""
    try:
        repo = _read_repo(root)
    except OrgError as e:
        return [str(e)]
    if repo is not None and not repo.is_dir():
        return [f"org workplace {repo} does not exist (create it or fix {ORG_CONFIG_NAME})"]
    return []
