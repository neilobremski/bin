"""Roster parsing — in-repo ROSTER.md describing team members.

Format: `### <Name>` blocks with bullet fields:

    ### Phil
    - **Status:** AI
    - **Rig:** junior-dev
    - **Role:** Lead Backend Engineer
    - **Leader:** yes
    Free persona prose lives anywhere in the block.

Humans (`- **Status:** Human`) are never dispatched; an optional
`- **Address:** <a8s-name>` tells teammates how to reach them. The Rig
value is a SYMBOLIC rig name resolved against the out-of-repo rig
config — never a command. Parsing is defensive: a malformed block disables
that one member (Member.error set) without crashing dispatch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROSTER_NAME = "ROSTER.md"

HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")
STOP_RE = re.compile(r"^#{1,3}\s")
FIELD_RE = re.compile(r"^-\s+\*\*([A-Za-z]+):\*\*\s*(.*?)\s*$")
RIG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class RosterError(Exception):
    pass


@dataclass
class Member:
    name: str
    status: str = "AI"
    rig: str | None = None
    role: str = ""
    leader: bool = False
    address: str | None = None
    persona: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def is_human(self) -> bool:
        return self.status.lower() == "human"

    @property
    def error(self) -> str | None:
        return "; ".join(self.errors) if self.errors else None


@dataclass
class Roster:
    path: Path
    members: list[Member] = field(default_factory=list)

    def find(self, name: str) -> Member | None:
        key = name.strip().lower()
        for m in self.members:
            if m.name.lower() == key:
                return m
        return None

    def leader(self) -> Member | None:
        for m in self.members:
            if m.leader and not m.is_human:
                return m
        return None

    def names(self) -> list[str]:
        return [m.name for m in self.members]


def resolve_roster_path(root: Path, raw: str | None) -> Path:
    if not raw:
        return root / DEFAULT_ROSTER_NAME
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return root / p


def _clean(value: str) -> str:
    return value.strip().strip("`").strip("*").strip()


def _is_true(value: str) -> bool:
    return value.strip().lower() in ("yes", "true", "y", "1")


def _member_from_block(name: str, lines: list[str]) -> Member:
    m = Member(name=name)
    m.persona = "\n".join([f"### {name}"] + lines).rstrip()
    fields: dict[str, str] = {}
    for line in lines:
        match = FIELD_RE.match(line)
        if not match:
            continue
        key = match.group(1).lower()
        if key not in fields:
            fields[key] = _clean(match.group(2))

    status = fields.get("status", "AI")
    if status.lower() not in ("ai", "human"):
        m.errors.append(f"Status must be Human or AI (got {status!r})")
    else:
        m.status = "Human" if status.lower() == "human" else "AI"

    m.role = fields.get("role", fields.get("mandate", ""))
    m.leader = _is_true(fields.get("leader", ""))
    m.address = fields.get("address") or None

    rig = fields.get("rig", "")
    if rig:
        if RIG_RE.match(rig):
            m.rig = rig.lower()
        else:
            m.errors.append(
                f"Rig must be a symbolic rig name, not a command (got {rig!r})"
            )
    elif not m.is_human:
        m.errors.append("missing Rig line")
    return m


def parse_roster(text: str, path: Path) -> Roster:
    members: list[Member] = []
    current_name: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_name, current_lines
        if current_name is not None:
            members.append(_member_from_block(current_name, current_lines))
        current_name = None
        current_lines = []

    for line in text.splitlines():
        head = HEADING_RE.match(line)
        if head:
            flush()
            current_name = head.group(1)
            continue
        if STOP_RE.match(line):
            flush()
            continue
        if current_name is not None:
            current_lines.append(line)
    flush()

    by_key: dict[str, list[Member]] = {}
    for m in members:
        by_key.setdefault(m.name.lower(), []).append(m)
    for dupes in by_key.values():
        if len(dupes) > 1:
            for m in dupes:
                m.errors.append("duplicate roster entry")

    return Roster(path=path, members=members)


def load_roster(path: Path) -> Roster:
    if not path.is_file():
        raise RosterError(f"roster not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RosterError(f"cannot read roster {path}: {e}") from e
    return parse_roster(text, path)
