"""Rig config — the out-of-repo security boundary.

The roster names a SYMBOLIC rig (`leader`, `junior-dev`, ...). Only this
config, which lives outside the repo (default `~/.config/r4t/rigs.json`,
overridable per-node with --rig-config), maps a rig to an actual argv.
A rig missing from the config fails closed: the member does not run.

Top-level keys are rig names, except these reserved governance keys (all
optional — every knob has a sane default; see README.md for the table):

- `"pins"` — agent name → rig, silently overriding the roster's Rig
  line (an in-repo roster edit can't upgrade a pinned agent).
- `"throttle"` — team-wide `max_concurrent` + `min_seconds_between_turn_starts`
  gates, enforced before any rig check.
- `"active_ttl_rotations"` — idle passes an agent stays on the crash-recovery
  watch list after its last dispatch.
- `"suppression_window_seconds"` — content-keyed pair suppression window.
- `"bucket_max"` / `"bucket_earn_ratio"` — reply-privilege token bucket.
- `"nudge_cap"` — idle nudges per agent per task before forced synthesis.
- `"breaker_cap"` / `"breaker_cooldown_seconds"` — per-agent failure breaker:
  consecutive failed turns (nonzero exit or timeout) that trip it, and how
  long turns stay paused per failure before one probe turn is let through.
- `"rebroadcast_senders"` — sender names whose inbound traffic is classed
  bulk (h4l rooms etc.).

Keys starting with `_` anywhere are ignored so shipped examples can carry
notes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from roster import Member
from state import atomic_write_json, r4t_home

PROMPT_PLACEHOLDER = "{prompt}"

RESERVED_CONFIG_KEYS = frozenset({
    "pins",
    "throttle",
    "active_ttl_rotations",
    "suppression_window_seconds",
    "bucket_max",
    "bucket_earn_ratio",
    "nudge_cap",
    "breaker_cap",
    "breaker_cooldown_seconds",
    "quiet_task_seconds",
    "rebroadcast_senders",
})

HARNESS_PRESETS: dict[str, dict] = {
    "claude": {
        "description": "Claude Code — matches apps/a8s/definitions/claude.json",
        "a8s_definition": "claude.json",
        "headless": "-p",
        "invoke": [
            "claude",
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            "Bash(tell:*) Read Edit Write Glob Grep WebFetch WebSearch TodoWrite",
            "-p",
            "{prompt}",
        ],
    },
    "codex": {
        "description": "OpenAI Codex CLI — matches apps/a8s/definitions/codex.json",
        "a8s_definition": "codex.json",
        "headless": "exec (positional prompt)",
        "invoke": [
            "codex",
            "exec",
            "--full-auto",
            "--skip-git-repo-check",
            "{prompt}",
        ],
    },
    "cursor": {
        "description": "Cursor Agent CLI (`agent`) — matches apps/a8s/definitions/cursor.json",
        "a8s_definition": "cursor.json",
        "headless": "-p",
        "invoke": [
            "agent",
            "-p",
            "--trust",
            "--force",
            "--approve-mcps",
            "{prompt}",
        ],
    },
    "opencode": {
        "description": (
            "OpenCode 1.17+ — `run` (not `-i`) with --auto for headless repo tools"
        ),
        "a8s_definition": "opencode.json",
        "headless": "run --auto (positional prompt)",
        "invoke": [
            "opencode",
            "run",
            "--auto",
            "--dir",
            ".",
            "{prompt}",
        ],
    },
    "opencode-ollama": {
        "description": (
            "OpenCode via `ollama launch` — local models, no cloud quota; "
            "requires --model"
        ),
        "a8s_definition": "opencode.json",
        "headless": "ollama launch opencode --model MODEL -- run --auto",
        "invoke": [
            "ollama",
            "launch",
            "opencode",
            "--model",
            "{model}",
            "--",
            "run",
            "--auto",
            "--dir",
            ".",
            "{prompt}",
        ],
    },
    "agy": {
        "description": (
            "Antigravity 1.1+ — --print for headless turns; --sandbox + "
            "--mode accept-edits for repo writes"
        ),
        "a8s_definition": "agy.json",
        "headless": "--print",
        "invoke": [
            "agy",
            "--sandbox",
            "--mode",
            "accept-edits",
            "--print",
            "{prompt}",
        ],
    },
    "copilot": {
        "description": "GitHub Copilot CLI — matches apps/a8s/definitions/copilot.json",
        "a8s_definition": "copilot.json",
        "headless": "-p",
        "invoke": [
            "copilot",
            "--allow-all-tools",
            "-p",
            "{prompt}",
        ],
    },
}

DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_CONCURRENCY = 1
DEFAULT_MAX_TURNS_PER_TASK = 25
DEFAULT_HOP_LIMIT = 4
DEFAULT_MAX_SENDS_PER_TURN = 6
DEFAULT_ACTIVE_TTL_ROTATIONS = 3
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_MIN_SECONDS_BETWEEN_TURN_STARTS = 15.0
DEFAULT_SUPPRESSION_WINDOW_SECONDS = 600.0
DEFAULT_BUCKET_MAX = 8.0
DEFAULT_BUCKET_EARN_RATIO = 0.1
DEFAULT_NUDGE_CAP = 2
DEFAULT_BREAKER_CAP = 5
DEFAULT_BREAKER_COOLDOWN_SECONDS = 600.0
DEFAULT_QUIET_TASK_SECONDS = 1800.0
DEFAULT_REBROADCAST_SENDERS = ("chatroom",)


class RigError(Exception):
    pass


@dataclass
class Throttle:
    """Team-wide gate applied before any rig check. `max_concurrent` caps
    live turns across ALL rigs (0 = unlimited); the cadence field spaces
    turn STARTS so a human can watch and intervene (0 = no gate)."""

    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    min_seconds_between_turn_starts: float = DEFAULT_MIN_SECONDS_BETWEEN_TURN_STARTS


@dataclass
class Rig:
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
        rotated round-robin per rig so e.g. local-model pools can back one
        rig while agents stay oblivious to what runs them."""
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
class RigConfig:
    path: Path
    rigs: dict[str, Rig] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)
    throttle: Throttle = field(default_factory=Throttle)
    active_ttl_rotations: int = DEFAULT_ACTIVE_TTL_ROTATIONS
    suppression_window_seconds: float = DEFAULT_SUPPRESSION_WINDOW_SECONDS
    bucket_max: float = DEFAULT_BUCKET_MAX
    bucket_earn_ratio: float = DEFAULT_BUCKET_EARN_RATIO
    nudge_cap: int = DEFAULT_NUDGE_CAP
    breaker_cap: int = DEFAULT_BREAKER_CAP
    breaker_cooldown_seconds: float = DEFAULT_BREAKER_COOLDOWN_SECONDS
    quiet_task_seconds: float = DEFAULT_QUIET_TASK_SECONDS
    rebroadcast_senders: tuple[str, ...] = DEFAULT_REBROADCAST_SENDERS
    missing: bool = False

    def rig_for(self, member: Member) -> tuple[Rig | None, str | None, bool]:
        """Resolve a member to a runnable rig. Returns (rig, error, pinned).
        Any failure fails closed with rig=None and a human-readable error."""
        pinned_rig = self.pins.get(member.name.lower())
        pinned = pinned_rig is not None
        rig_name = pinned_rig if pinned else (member.rig or "")
        if not rig_name:
            return None, f"{member.name} has no Rig line in the roster", pinned
        if self.missing:
            return (
                None,
                f"rig {rig_name!r} not found (fail closed) — "
                f"try: r4t rig add {rig_name} <preset>",
                pinned,
            )
        rig = self.rigs.get(rig_name.lower())
        if rig is None:
            return (
                None,
                f"rig {rig_name!r} not found in {self.path} (fail closed) — "
                f"try: r4t rig add {rig_name} <preset>",
                pinned,
            )
        if rig.error:
            return None, f"rig {rig_name!r} is invalid: {rig.error}", pinned
        return rig, None, pinned


def default_config_path() -> Path:
    return r4t_home() / "rigs.json"


def preset_names() -> list[str]:
    return sorted(HARNESS_PRESETS)


def format_preset_invoke(preset: str) -> str:
    entry = HARNESS_PRESETS[preset]
    return " ".join(entry["invoke"])


def build_preset_invoke(preset: str, *, model: str | None = None) -> list[str]:
    """Materialize a preset argv, substituting {model} when the preset needs it."""
    preset_key = preset.strip().lower()
    if preset_key not in HARNESS_PRESETS:
        known = ", ".join(preset_names())
        raise RigError(f"unknown preset {preset!r}; choose one of: {known}")
    needs_model = any("{model}" in arg for arg in HARNESS_PRESETS[preset_key]["invoke"])
    if needs_model and not (model or "").strip():
        raise RigError(f"preset {preset_key!r} requires --model")
    model_value = (model or "").strip()
    argv: list[str] = []
    for arg in HARNESS_PRESETS[preset_key]["invoke"]:
        if arg == "{model}":
            argv.append(model_value)
        else:
            argv.append(arg)
    return argv


def _validate_rig_name(name: str) -> str:
    key = name.strip().lower()
    if not key:
        raise RigError("rig name is required")
    if key in RESERVED_CONFIG_KEYS:
        raise RigError(f"{key!r} is a reserved rig config key, not a rig name")
    return key


def _load_config_payload(path: Path) -> dict:
    """A missing file is an EMPTY config, not the `r4t init` starter payload —
    seeding starter rigs here made a fresh `rig add leader ...` collide
    with a phantom 'leader' the user never created."""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RigError(f"cannot load rig config {path}: {e}") from e
        if not isinstance(data, dict):
            raise RigError(f"rig config {path} must be a JSON object")
        return data
    return {
        "_notes": (
            "Created by `r4t rig add`. Rig names are SYMBOLIC — ROSTER.md "
            "Rig lines reference them. See `r4t rig presets` and "
            "apps/r4t/README.md."
        ),
    }


def add_preset_rig(
    path: Path,
    rig_name: str,
    preset: str,
    *,
    model: str | None = None,
    force: bool = False,
) -> str:
    """Add or replace a symbolic rig from a named CLI preset. Returns rig key."""
    rig_key = _validate_rig_name(rig_name)
    preset_key = preset.strip().lower()
    if preset_key not in HARNESS_PRESETS:
        known = ", ".join(preset_names())
        raise RigError(f"unknown preset {preset!r}; choose one of: {known}")
    payload = _load_config_payload(path)
    if rig_key in payload and not rig_key.startswith("_") and not force:
        raise RigError(
            f"rig {rig_key!r} already exists in {path} (pass --force to replace)"
        )
    entry = HARNESS_PRESETS[preset_key]
    invoke = build_preset_invoke(preset_key, model=model)
    note = (
        f"Added by `r4t rig add` from preset {preset_key!r} "
        f"({entry['description']})."
    )
    if model:
        note += f" model={model.strip()}."
    payload[rig_key] = {
        "_notes": note,
        "invoke": invoke,
    }
    atomic_write_json(path, payload)
    return rig_key


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


def _parse_rig(name: str, raw: object) -> Rig:
    rig = Rig(name=name.lower())
    if not isinstance(raw, dict):
        rig.error = "rig definition must be an object"
        return rig
    invoke, err = _normalize_invoke(raw.get("invoke"))
    if err:
        rig.error = err
        return rig
    rig.invoke = invoke

    problems: list[str] = []
    rig.timeout_seconds, err = _positive_number(
        raw.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS
    )
    if err:
        problems.append(f"timeout_seconds: {err}")
    concurrency, err = _positive_number(raw.get("concurrency"), DEFAULT_CONCURRENCY)
    if err:
        problems.append(f"concurrency: {err}")
    rig.concurrency = int(concurrency)
    max_turns, err = _positive_number(
        raw.get("max_turns_per_task"), DEFAULT_MAX_TURNS_PER_TASK
    )
    if err:
        problems.append(f"max_turns_per_task: {err}")
    rig.max_turns_per_task = int(max_turns)
    hop_limit, err = _positive_number(raw.get("hop_limit"), DEFAULT_HOP_LIMIT)
    if err:
        problems.append(f"hop_limit: {err}")
    rig.hop_limit = int(hop_limit)
    max_sends, err = _positive_number(
        raw.get("max_sends_per_turn"), DEFAULT_MAX_SENDS_PER_TURN
    )
    if err:
        problems.append(f"max_sends_per_turn: {err}")
    rig.max_sends_per_turn = int(max_sends)

    if problems:
        rig.error = "; ".join(problems)
    return rig


def _non_negative_number(raw: object, default: float, label: str) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        raise RigError(f"{label} must be a non-negative number, got {raw!r}")
    return float(raw)


def _parse_throttle(raw: object) -> Throttle:
    if not isinstance(raw, dict):
        raise RigError('"throttle" must be an object')
    return Throttle(
        max_concurrent=int(
            _non_negative_number(
                raw.get("max_concurrent"),
                DEFAULT_MAX_CONCURRENT,
                "throttle.max_concurrent",
            )
        ),
        min_seconds_between_turn_starts=_non_negative_number(
            raw.get("min_seconds_between_turn_starts"),
            DEFAULT_MIN_SECONDS_BETWEEN_TURN_STARTS,
            "throttle.min_seconds_between_turn_starts",
        ),
    )


def load_rig_config(path: Path) -> RigConfig:
    if not path.is_file():
        return RigConfig(path=path, missing=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RigError(f"cannot load rig config {path}: {e}") from e
    if not isinstance(data, dict):
        raise RigError(f"rig config {path} must be a JSON object")

    config = RigConfig(path=path)
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if key == "pins":
            if isinstance(value, dict):
                config.pins = {
                    str(agent).lower(): str(rig).strip().lower()
                    for agent, rig in value.items()
                    if not str(agent).startswith("_")
                }
            continue
        if key == "throttle":
            config.throttle = _parse_throttle(value)
            continue
        if key in ("active_ttl_rotations", "nudge_cap", "breaker_cap"):
            n = _non_negative_number(value, 0, key)
            if n <= 0:
                raise RigError(f"{key} must be positive, got {value!r}")
            setattr(config, key, int(n))
            continue
        if key in (
            "suppression_window_seconds",
            "bucket_max",
            "bucket_earn_ratio",
            "breaker_cooldown_seconds",
            "quiet_task_seconds",
        ):
            n = _non_negative_number(value, 0, key)
            if n <= 0:
                raise RigError(f"{key} must be positive, got {value!r}")
            setattr(config, key, n)
            continue
        if key == "rebroadcast_senders":
            if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
                raise RigError(f"{key} must be a list of sender names")
            config.rebroadcast_senders = tuple(s.strip().lower() for s in value if s.strip())
            continue
        config.rigs[key.lower()] = _parse_rig(key, value)
    return config


def default_config_payload() -> dict:
    """The `r4t init` starter config: two symbolic rigs on the cheapest
    common harness CLI, plus notes for swapping in other CLIs. Every governance
    knob is left to its default."""
    return {
        "_notes": [
            "Generated by `r4t init`. Rig names are SYMBOLIC — the roster's",
            "Rig lines reference them; only this out-of-repo file says what",
            "actually runs. Swap invoke for your CLI, or run:",
            "  r4t rig presets",
            "  r4t rig add <rig> <preset>",
            "Presets mirror apps/a8s/definitions/ (claude, codex, cursor, ...).",
            "invoke may also be a LIST of argvs (a pool, rotated round-robin).",
            "All governance knobs default sanely; see apps/r4t/README.md.",
        ],
        "leader": {
            "invoke": ["opencode", "run", "--auto", "--dir", ".", "{prompt}"],
        },
        "member": {
            "invoke": ["opencode", "run", "--auto", "--dir", ".", "{prompt}"],
        },
    }
