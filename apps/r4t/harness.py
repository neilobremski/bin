"""Harness config — the out-of-repo security boundary.

The roster names a SYMBOLIC tier (`leader`, `junior-dev`, ...). Only this
config, which lives outside the repo (default `~/.r4t/harnesses.json`,
overridable per-node with --harness-config), maps a tier to an actual argv.
A tier missing from the config fails closed: the member does not run.

Top-level keys are tier names, except these reserved keys:

- `"pins"` — agent name → tier, silently overriding the roster's Harness
  line (an in-repo roster edit can't upgrade a pinned agent).
- `"throttle"` — team-wide `max_concurrent` + `min_seconds_between_turn_starts`
  gates, enforced before any tier check.
- `"active_ttl_rotations"` — how many idle passes an agent stays on the
  active watch list after its last dispatch (default 3).

Keys starting with `_` anywhere are ignored so the shipped example can
carry comments.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from roster import Member
from state import r4t_home

PROMPT_PLACEHOLDER = "{prompt}"

DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_CONCURRENCY = 1
DEFAULT_MAX_TURNS_PER_TASK = 25
DEFAULT_HOP_LIMIT = 4
DEFAULT_MAX_SENDS_PER_TURN = 6
DEFAULT_ACTIVE_TTL_ROTATIONS = 3


class HarnessError(Exception):
    pass


@dataclass
class Throttle:
    """Team-wide gate applied before any tier check. `max_concurrent` caps
    live turns across ALL tiers (0 = unlimited); the cadence field spaces
    turn STARTS so a human can watch and intervene (0 = no gate)."""

    max_concurrent: int = 0
    min_seconds_between_turn_starts: float = 0.0


@dataclass
class Tier:
    name: str
    invoke: list = field(default_factory=list)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    concurrency: int = DEFAULT_CONCURRENCY
    max_turns_per_task: int = DEFAULT_MAX_TURNS_PER_TASK
    hop_limit: int = DEFAULT_HOP_LIMIT
    max_sends_per_turn: int = DEFAULT_MAX_SENDS_PER_TURN
    error: str | None = None

    def pool(self) -> list[list[str]]:
        """`invoke` is one argv (list of str) or a pool (list of argvs) —
        rotated round-robin per tier so e.g. local-model pools can back one
        tier while agents stay oblivious to what runs them."""
        if self.invoke and isinstance(self.invoke[0], list):
            return self.invoke
        return [self.invoke] if self.invoke else []

    @property
    def pool_size(self) -> int:
        return len(self.pool())

    def argv(self, prompt: str, index: int = 0) -> list[str]:
        pool = self.pool()
        chosen = pool[index % len(pool)]
        return [a.replace(PROMPT_PLACEHOLDER, prompt) for a in chosen]


@dataclass
class HarnessConfig:
    path: Path
    tiers: dict[str, Tier] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)
    throttle: Throttle = field(default_factory=Throttle)
    active_ttl_rotations: int = DEFAULT_ACTIVE_TTL_ROTATIONS
    missing: bool = False

    def tier_for(self, member: Member) -> tuple[Tier | None, str | None, bool]:
        """Resolve a member to a runnable tier. Returns (tier, error, pinned).
        Any failure fails closed with tier=None and a human-readable error."""
        pinned_tier = self.pins.get(member.name.lower())
        pinned = pinned_tier is not None
        tier_name = pinned_tier if pinned else (member.harness or "")
        if not tier_name:
            return None, f"{member.name} has no harness tier", pinned
        if self.missing:
            return (
                None,
                f"harness config not found at {self.path} — tier {tier_name!r} "
                f"cannot be resolved (fail closed)",
                pinned,
            )
        tier = self.tiers.get(tier_name.lower())
        if tier is None:
            return (
                None,
                f"tier {tier_name!r} not found in harness config {self.path} "
                "(fail closed)",
                pinned,
            )
        if tier.error:
            return None, f"tier {tier_name!r} is invalid: {tier.error}", pinned
        return tier, None, pinned


def default_config_path() -> Path:
    return r4t_home() / "harnesses.json"


def resolve_config_path(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    return default_config_path()


def _positive_number(raw: object, default: float) -> tuple[float, str | None]:
    if raw is None:
        return default, None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default, f"expected a number, got {raw!r}"
    if raw <= 0:
        return default, f"must be positive, got {raw!r}"
    return float(raw), None


def _normalize_invoke(invoke: object) -> tuple[list, str | None]:
    """Accept one argv (list of str) or a pool (list of argvs). Every argv
    must be non-empty strings with a {prompt} placeholder somewhere."""
    if not isinstance(invoke, list) or not invoke:
        return [], "invoke must be a non-empty list"
    if all(isinstance(a, str) for a in invoke):
        variants: list[list[str]] = [invoke]
        flat = True
    elif all(isinstance(a, list) for a in invoke):
        variants = invoke
        flat = False
    else:
        return [], "invoke must be one argv (strings) or a pool (list of argvs)"
    for i, argv in enumerate(variants):
        if not argv or not all(isinstance(a, str) for a in argv):
            return [], f"invoke variant {i} must be a non-empty list of strings"
        if not any(PROMPT_PLACEHOLDER in a for a in argv):
            return [], f"invoke variant {i} has no {{prompt}} placeholder"
    return (list(invoke) if flat else [list(v) for v in variants]), None


def _parse_tier(name: str, raw: object) -> Tier:
    tier = Tier(name=name.lower())
    if not isinstance(raw, dict):
        tier.error = "tier definition must be an object"
        return tier
    invoke, err = _normalize_invoke(raw.get("invoke"))
    if err:
        tier.error = err
        return tier
    tier.invoke = invoke

    problems: list[str] = []
    tier.timeout_seconds, err = _positive_number(
        raw.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS
    )
    if err:
        problems.append(f"timeout_seconds: {err}")
    concurrency, err = _positive_number(raw.get("concurrency"), DEFAULT_CONCURRENCY)
    if err:
        problems.append(f"concurrency: {err}")
    tier.concurrency = int(concurrency)
    max_turns, err = _positive_number(
        raw.get("max_turns_per_task"), DEFAULT_MAX_TURNS_PER_TASK
    )
    if err:
        problems.append(f"max_turns_per_task: {err}")
    tier.max_turns_per_task = int(max_turns)
    hop_limit, err = _positive_number(raw.get("hop_limit"), DEFAULT_HOP_LIMIT)
    if err:
        problems.append(f"hop_limit: {err}")
    tier.hop_limit = int(hop_limit)
    max_sends, err = _positive_number(
        raw.get("max_sends_per_turn"), DEFAULT_MAX_SENDS_PER_TURN
    )
    if err:
        problems.append(f"max_sends_per_turn: {err}")
    tier.max_sends_per_turn = int(max_sends)

    if problems:
        tier.error = "; ".join(problems)
    return tier


def _non_negative_number(raw: object, default: float, label: str) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        raise HarnessError(f"{label} must be a non-negative number, got {raw!r}")
    return float(raw)


def _parse_throttle(raw: object) -> Throttle:
    if not isinstance(raw, dict):
        raise HarnessError('"throttle" must be an object')
    return Throttle(
        max_concurrent=int(
            _non_negative_number(raw.get("max_concurrent"), 0, "throttle.max_concurrent")
        ),
        min_seconds_between_turn_starts=_non_negative_number(
            raw.get("min_seconds_between_turn_starts"),
            0.0,
            "throttle.min_seconds_between_turn_starts",
        ),
    )


def load_harness_config(path: Path) -> HarnessConfig:
    if not path.is_file():
        return HarnessConfig(path=path, missing=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HarnessError(f"cannot load harness config {path}: {e}") from e
    if not isinstance(data, dict):
        raise HarnessError(f"harness config {path} must be a JSON object")

    config = HarnessConfig(path=path)
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if key == "pins":
            if isinstance(value, dict):
                config.pins = {
                    str(agent).lower(): str(tier).strip().lower()
                    for agent, tier in value.items()
                    if not str(agent).startswith("_")
                }
            continue
        if key == "throttle":
            config.throttle = _parse_throttle(value)
            continue
        if key == "active_ttl_rotations":
            ttl = _non_negative_number(value, DEFAULT_ACTIVE_TTL_ROTATIONS, key)
            if ttl <= 0:
                raise HarnessError(f"{key} must be positive, got {value!r}")
            config.active_ttl_rotations = int(ttl)
            continue
        config.tiers[key.lower()] = _parse_tier(key, value)
    return config
