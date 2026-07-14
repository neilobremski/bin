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
- Otherwise `<root>` is both org dir and workplace — the in-repo default. The
  config file may still be present WITHOUT a `repo` key: it then exists purely
  to carry org settings (below) that must travel with the org, not the machine.

Org settings (every "this-or-that" is a knob with a default and a declared
home — this file is the org-level home):

- `comms` (`open` | `closed`, default `open`): `open` delivers a tell to any
  valid roster member; `closed` reroutes non-tree-adjacent tells through the
  sender's lead (the military model). Info hiding stays at the prompt level in
  both modes — a learned address delivers even when the prompt does not list it.
- `leader_sees_lateral` (bool, default `false`): when on, a lateral (peer)
  delivery also lands a read-only `class=auto` copy on the lead — no turn is
  burned to notify.
- `egress` (bool, default `true`): when on, the topmost leader alone may
  originate external mail; when off, no member may, and external `to`s redirect
  to the top leader (the garden's single voice).
- `doorbell_check` (string, default absent): a shell command run before the org
  may ring an absent human's doorbell. Exit 0 lets the ring through; nonzero
  parks the message without ringing (the gate protects attention, not the
  mailbox). Absent or empty is today's behavior — no gate.

Graduation is trivial: copy ROSTER.md + MISSION.md into the repo and drop the
`repo` key (or the whole file, if it carries no settings). Resolution falls
back to the in-repo default with no other change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ORG_CONFIG_NAME = "r4t-org.json"

COMMS_OPEN = "open"
COMMS_CLOSED = "closed"
COMMS_MODES = (COMMS_OPEN, COMMS_CLOSED)


class OrgError(Exception):
    pass


@dataclass
class Org:
    dir: Path
    workplace: Path
    comms: str = COMMS_OPEN
    leader_sees_lateral: bool = False
    egress: bool = True
    doorbell_check: str | None = None

    @property
    def is_portable(self) -> bool:
        return self.dir != self.workplace


def org_config_path(root: Path) -> Path:
    return root / ORG_CONFIG_NAME


def _read_config(root: Path) -> dict:
    cfg = org_config_path(root)
    if not cfg.is_file():
        return {}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise OrgError(f"cannot read org config {cfg}: {e}") from e
    if not isinstance(data, dict):
        raise OrgError(f"org config {cfg} must be a JSON object")
    return data


def _resolve_repo(root: Path, data: dict) -> Path | None:
    raw = str(data.get("repo", "")).strip()
    if not raw:
        return None
    repo = Path(raw).expanduser()
    if not repo.is_absolute():
        repo = (root / repo).resolve()
    return repo


def _parse_bool(raw: object, default: bool, key: str) -> tuple[bool, str | None]:
    if raw is None:
        return default, None
    if not isinstance(raw, bool):
        return default, f'org config "{key}" must be true or false (got {raw!r})'
    return raw, None


def _parse_str(raw: object, key: str) -> tuple[str | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, f'org config "{key}" must be a string (got {raw!r})'
    return raw, None


def _parse_settings(data: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    comms = data.get("comms", COMMS_OPEN)
    if comms not in COMMS_MODES:
        errors.append(f'org config "comms" must be "open" or "closed" (got {comms!r})')
        comms = COMMS_OPEN
    lsl, err = _parse_bool(data.get("leader_sees_lateral"), False, "leader_sees_lateral")
    if err:
        errors.append(err)
    egress, err = _parse_bool(data.get("egress"), True, "egress")
    if err:
        errors.append(err)
    doorbell_check, err = _parse_str(data.get("doorbell_check"), "doorbell_check")
    if err:
        errors.append(err)
    return {
        "comms": comms,
        "leader_sees_lateral": lsl,
        "egress": egress,
        "doorbell_check": doorbell_check,
    }, errors


def load_org(root: Path) -> Org:
    """Resolve `root` to (org dir, workplace) and org settings for path building
    and dispatch. Never raises — a malformed org config degrades to the in-repo
    default with default settings; `check_org` is the boundary that reports it."""
    try:
        data = _read_config(root)
    except OrgError:
        data = {}
    try:
        repo = _resolve_repo(root, data)
    except (OSError, ValueError):
        repo = None
    settings, _errors = _parse_settings(data)
    return Org(dir=root, workplace=repo or root, **settings)


def check_org(root: Path) -> list[str]:
    """Validation for `roster check`: a malformed org config, a bad setting
    value, or a workplace repo that does not exist. Empty when there is no org
    config (in-repo) and no problems."""
    try:
        data = _read_config(root)
    except OrgError as e:
        return [str(e)]
    _settings, problems = _parse_settings(data)
    repo = _resolve_repo(root, data)
    if repo is not None and not repo.is_dir():
        problems.append(
            f"org workplace {repo} does not exist (create it or fix {ORG_CONFIG_NAME})"
        )
    return problems
