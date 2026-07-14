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
- `"cell_budget_max"` / `"cell_budget_earn_per_hour"` — the shared cell spend
  bucket. A turn costs 1 cell unit; when it is empty no member runs.
- `"quiet_task_seconds"` — a thread quiet this long with its originator still
  unanswered wakes the leader with a nudge to reply with current state.
- `"breaker_cap"` / `"breaker_cooldown_seconds"` — per-member failure breaker:
  consecutive failed turns (nonzero exit or timeout) that trip it, and how
  long turns stay paused per failure before one probe turn is let through.

Per-rig keys (defaults for every member on that rig; per-member override
later): `budget_max` / `budget_earn_per_hour` — the member spend bucket. A
turn costs 1 member unit; when it is empty the member is resting.
`rig_budget_max` / `rig_budget_earn_per_hour` — the MACHINE-GLOBAL rig spend
bucket (absent = no rig gate). A rig maps to a real subscription, so this
ceiling binds every team on the machine that shares the rig; a turn also costs
1 rig unit and an empty rig bucket rests every member on it, on every team. If
`rig_budget_max` is set, `rig_budget_earn_per_hour` must be set too — a real
plan always declares a refill rate.

`preset` — the harness preset the rig was created from (`r4t rig add`/`swap`
record it). It defaults the text knobs (`history_max_bytes` /
`history_body_max` / `prompt_body_max`) by the preset's text tier (TEXT_TIERS);
explicit values always win, and a rig with no preset gets the small tier.

Keys starting with `_` anywhere are ignored so shipped examples can carry
notes.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from roster import Member
from state import atomic_write_json, r4t_home

PROMPT_PLACEHOLDER = "{prompt}"

RESERVED_CONFIG_KEYS = frozenset({
    "pins",
    "throttle",
    "cell_budget_max",
    "cell_budget_earn_per_hour",
    "breaker_cap",
    "breaker_cooldown_seconds",
    "quiet_task_seconds",
})

HARNESS_PRESETS: dict[str, dict] = {
    "claude": {
        "text_tier": "big",
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
        "model_argv": ["--model", "{model}"],
    },
    "codex": {
        "text_tier": "big",
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
        "model_argv": ["-m", "{model}"],
        "model_anchor": "exec",
    },
    "cursor": {
        "text_tier": "moderate",
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
        "model_argv": ["--model", "{model}"],
    },
    "opencode": {
        "text_tier": "moderate",
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
        "model_argv": ["-m", "{model}"],
        "model_anchor": "run",
    },
    "opencode-ollama": {
        "text_tier": "small",
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
    "ollama": {
        "text_tier": "small",
        "description": (
            "Bare `ollama run` — tiny models with no tool use or big context; "
            "replies ride the stdout fallback; requires --model"
        ),
        "headless": "run MODEL PROMPT (positional)",
        "invoke": [
            "ollama",
            "run",
            "{model}",
            "{prompt}",
        ],
    },
    "agy": {
        "text_tier": "big",
        "description": (
            "Antigravity 1.1+ — --print for headless turns; --mode accept-edits "
            "so edits skip the review prompt. No --sandbox: it confines child "
            "writes to CWD, blocking tell's staging outbox (see docs/harness-agy.md)"
        ),
        "a8s_definition": "agy.json",
        "headless": "--print",
        "invoke": [
            "agy",
            "--mode",
            "accept-edits",
            "--print",
            "{prompt}",
        ],
        "model_argv": ["--model", "{model}"],
        "model_resolver": "agy-live",
    },
    "copilot": {
        "text_tier": "moderate",
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
DEFAULT_MAX_SENDS_PER_TURN = 6
DEFAULT_HISTORY_MAX_BYTES = 8192
DEFAULT_HISTORY_BODY_MAX = 2000
DEFAULT_PROMPT_BODY_MAX = 4000

# Text-budget tiers: how much history/prompt a preset's harness can usefully
# carry. `small` is the conservative floor and the fallback for rigs with no
# preset (custom/scripted CLIs — an unknown harness gets the safe values).
TEXT_TIERS: dict[str, dict[str, int]] = {
    "big": {
        "history_max_bytes": 50_000,
        "history_body_max": 12_000,
        "prompt_body_max": 24_000,
    },
    "moderate": {
        "history_max_bytes": 25_000,
        "history_body_max": 6_000,
        "prompt_body_max": 12_000,
    },
    "small": {
        "history_max_bytes": DEFAULT_HISTORY_MAX_BYTES,
        "history_body_max": DEFAULT_HISTORY_BODY_MAX,
        "prompt_body_max": DEFAULT_PROMPT_BODY_MAX,
    },
}


def text_defaults(preset: str | None) -> dict[str, int]:
    """The text-knob defaults for `preset` (small when absent/unknown)."""
    tier = HARNESS_PRESETS.get((preset or "").strip().lower(), {}).get("text_tier")
    return TEXT_TIERS.get(tier or "small", TEXT_TIERS["small"])
DEFAULT_BUDGET_MAX = 8.0
DEFAULT_BUDGET_EARN_PER_HOUR = 4.0
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_MIN_SECONDS_BETWEEN_TURN_STARTS = 15.0
DEFAULT_CELL_BUDGET_MAX = 16.0
DEFAULT_CELL_BUDGET_EARN_PER_HOUR = 8.0
DEFAULT_BREAKER_CAP = 5
DEFAULT_BREAKER_COOLDOWN_SECONDS = 600.0
DEFAULT_QUIET_TASK_SECONDS = 1800.0


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
    max_sends_per_turn: int = DEFAULT_MAX_SENDS_PER_TURN
    budget_max: float = DEFAULT_BUDGET_MAX
    budget_earn_per_hour: float = DEFAULT_BUDGET_EARN_PER_HOUR
    rig_budget_max: float | None = None
    rig_budget_earn_per_hour: float | None = None
    history_max_bytes: int = DEFAULT_HISTORY_MAX_BYTES
    history_body_max: int = DEFAULT_HISTORY_BODY_MAX
    prompt_body_max: int = DEFAULT_PROMPT_BODY_MAX
    model: str | None = None
    model_resolver: str | None = None
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
    cell_budget_max: float = DEFAULT_CELL_BUDGET_MAX
    cell_budget_earn_per_hour: float = DEFAULT_CELL_BUDGET_EARN_PER_HOUR
    breaker_cap: int = DEFAULT_BREAKER_CAP
    breaker_cooldown_seconds: float = DEFAULT_BREAKER_COOLDOWN_SECONDS
    quiet_task_seconds: float = DEFAULT_QUIET_TASK_SECONDS
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
    """Materialize a preset argv for a given --model.

    Three shapes, keyed off the preset's metadata:

    - Inline `{model}` presets (ollama, opencode-ollama): --model is REQUIRED
      and substituted straight into the placeholder — the CLI has no default.
    - `model_argv` presets with a live resolver (agy): splice the flag pair but
      keep the `{model}` placeholder so dispatch re-resolves the friendly string
      against `agy models` before every turn (the display names drift as agy
      ships new versions, so a value baked in at add-time would go stale).
    - `model_argv` presets without a resolver (claude/codex/cursor/opencode):
      splice the flag pair with the resolved value now. --model is OPTIONAL —
      absent, the base argv is returned and the CLI's own default applies.
    """
    preset_key = preset.strip().lower()
    if preset_key not in HARNESS_PRESETS:
        known = ", ".join(preset_names())
        raise RigError(f"unknown preset {preset!r}; choose one of: {known}")
    entry = HARNESS_PRESETS[preset_key]
    model_value = (model or "").strip()

    if any("{model}" in arg for arg in entry["invoke"]):
        if not model_value:
            raise RigError(f"preset {preset_key!r} requires --model")
        return [model_value if arg == "{model}" else arg for arg in entry["invoke"]]

    argv = list(entry["invoke"])
    if not model_value:
        return argv

    model_argv = entry.get("model_argv")
    if not model_argv:
        raise RigError(f"preset {preset_key!r} does not support --model")
    spliced = "{model}" if entry.get("model_resolver") else model_value
    flag_pair = [spliced if arg == "{model}" else arg for arg in model_argv]
    anchor = entry.get("model_anchor")
    insert_at = argv.index(anchor) + 1 if anchor else 1
    argv[insert_at:insert_at] = flag_pair
    return argv


AGY_MODELS_TIMEOUT_SECONDS = 10

# Effort/thinking suffix ranking used to break ties when a friendly string
# matches several display names (e.g. `flash` hits Flash Low/Medium/High).
_EFFORT_RANK = {"thinking": 4, "high": 3, "medium": 2, "low": 1}


def _model_tokens(text: str) -> list[str]:
    """Lowercase and split on runs of dashes/whitespace, dropping any wrapping
    parens so `-` and ` ` are interchangeable: `gemini-3.5-flash` and
    `Gemini 3.5 Flash` tokenize identically."""
    return [t.strip("()") for t in re.split(r"[-\s]+", text.strip().lower()) if t.strip("()")]


def _effort_rank(tokens: list[str]) -> int:
    return max((_EFFORT_RANK.get(t, 0) for t in tokens), default=0)


def fuzzy_match_model(query: str, names: list[str]) -> str:
    """Resolve a friendly --model string to one exact display name.

    A name matches when every query token is a substring of some name token,
    after both sides are normalized by `_model_tokens` (case-insensitive, dashes
    and spaces treated as the same separator, parens stripped). So `sonnet`,
    `claude-sonnet`, and the exact `Claude Sonnet 4.6 (Thinking)` all resolve;
    `gpt-oss-120b` matches `GPT-OSS 120B (Medium)`.

    Tie-break when several names match, in order: fewest extra tokens (tightest
    match) → highest effort/thinking suffix (thinking > high > medium > low) →
    alphabetical. A miss raises RigError listing the available names — agy
    silently ignores unknown --model strings, so an unresolved value must never
    be passed through.
    """
    q = _model_tokens(query)
    if not q:
        raise RigError("empty --model value; nothing to match")
    scored: list[tuple[int, int, str]] = []
    for name in names:
        name_tokens = _model_tokens(name)
        if all(any(tok in cand for cand in name_tokens) for tok in q):
            scored.append((len(name_tokens) - len(q), -_effort_rank(name_tokens), name))
    if not scored:
        listing = "\n".join(f"  {n}" for n in names)
        raise RigError(
            f"--model {query!r} matched no agy model. Available:\n{listing}\n"
            f"(try: r4t rig swap <rig> agy --model <one of the above>)"
        )
    scored.sort()
    return scored[0][2]


def agy_model_names(timeout: float = AGY_MODELS_TIMEOUT_SECONDS) -> list[str]:
    """The current `agy models` display names, one per line. Errors loudly —
    never returns a partial or fabricated list."""
    try:
        proc = subprocess.run(
            ["agy", "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise RigError(f"could not run `agy models` to resolve --model: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise RigError(
            f"`agy models` timed out after {timeout:g}s while resolving --model"
        ) from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RigError(f"`agy models` failed (exit {proc.returncode}): {detail}")
    names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not names:
        raise RigError("`agy models` returned no models to match against")
    return names


def resolve_agy_model(
    query: str, *, timeout: float = AGY_MODELS_TIMEOUT_SECONDS, names: list[str] | None = None
) -> str:
    """Fuzzy-match `query` against the live `agy models` list. Nothing is cached:
    the list is re-fetched per call so it stays current as agy ships versions."""
    if names is None:
        names = agy_model_names(timeout)
    return fuzzy_match_model(query, names)


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
    rig_entry: dict = {
        "_notes": note,
        "preset": preset_key,
        "invoke": invoke,
    }
    if model and entry.get("model_resolver"):
        rig_entry["model"] = model.strip()
        rig_entry["model_resolver"] = entry["model_resolver"]
    payload[rig_key] = rig_entry
    atomic_write_json(path, payload)
    return rig_key


def swap_preset_rig(
    path: Path,
    rig_name: str,
    preset: str,
    *,
    model: str | None = None,
) -> str:
    """Switch an existing rig's invoke to a preset's, preserving every other
    key (concurrency, timeout_seconds, ...). Returns rig key."""
    rig_key = _validate_rig_name(rig_name)
    preset_key = preset.strip().lower()
    if preset_key not in HARNESS_PRESETS:
        known = ", ".join(preset_names())
        raise RigError(f"unknown preset {preset!r}; choose one of: {known}")
    payload = _load_config_payload(path)
    existing = payload.get(rig_key)
    if not isinstance(existing, dict):
        raise RigError(
            f"no rig {rig_key!r} to swap in {path} "
            f"(try: r4t rig add {rig_key} {preset_key})"
        )
    entry = HARNESS_PRESETS[preset_key]
    invoke = build_preset_invoke(preset_key, model=model)
    note = f"Swapped to preset {preset_key!r} by `r4t rig swap`."
    if model:
        note += f" model={model.strip()}."
    existing["_notes"] = note
    existing["preset"] = preset_key
    existing["invoke"] = invoke
    # A swap replaces the harness wholesale, so stale model resolution from the
    # previous preset must not linger. The updated `preset` re-resolves the
    # text-tier defaults; explicit knob values in the entry still win.
    existing.pop("model", None)
    existing.pop("model_resolver", None)
    if model and entry.get("model_resolver"):
        existing["model"] = model.strip()
        existing["model_resolver"] = entry["model_resolver"]
    atomic_write_json(path, payload)
    return rig_key


def remove_rig(path: Path, rig_name: str) -> str:
    """Delete a symbolic rig from the config. Returns the removed key.

    Fails loudly if the rig is absent — the same shape `swap` uses for an
    unknown rig. Usage checks (roster/pin references) live in the CLI layer,
    which can reach the roster."""
    rig_key = _validate_rig_name(rig_name)
    payload = _load_config_payload(path)
    if rig_key not in payload or rig_key.startswith("_"):
        raise RigError(
            f"no rig {rig_key!r} to remove in {path} (try: r4t rig list)"
        )
    del payload[rig_key]
    atomic_write_json(path, payload)
    return rig_key


CONFIGURABLE_INT_KEYS = (
    "concurrency",
    "history_max_bytes",
    "history_body_max",
    "prompt_body_max",
)
CONFIGURABLE_FLOAT_KEYS = ("rig_budget_max", "rig_budget_earn_per_hour")
CONFIGURABLE_RIG_KEYS = (
    "concurrency",
    "rig_budget_max",
    "rig_budget_earn_per_hour",
    "history_max_bytes",
    "history_body_max",
    "prompt_body_max",
    "model",
)


@dataclass
class RigSetting:
    """One effective rig setting and where it comes from. `explicit` is True
    only when the value is written in rigs.json; inherited tier defaults and
    built-in defaults are never materialized (so `rig swap` can re-resolve)."""

    key: str
    value: object
    source: str
    explicit: bool

    def display(self) -> str:
        if self.value is None:
            return "unset"
        if isinstance(self.value, float):
            return f"{self.value:g}"
        return str(self.value)


def resolve_config_path(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    return default_config_path()


def _unknown_setting_error(key: str) -> RigError:
    valid = ", ".join(CONFIGURABLE_RIG_KEYS)
    return RigError(
        f"unknown rig setting {key!r} "
        f"(try: r4t rig set <rig> <key> <value> with one of: {valid})"
    )


def _rig_entry(path: Path, rig_name: str) -> tuple[str, dict, dict]:
    """Return (rig_key, entry, payload) for a rig that must already exist."""
    rig_key = _validate_rig_name(rig_name)
    payload = _load_config_payload(path)
    entry = payload.get(rig_key)
    if not isinstance(entry, dict):
        raise RigError(
            f"no rig {rig_key!r} in {path} (try: r4t rig add {rig_key} <preset>)"
        )
    return rig_key, entry, payload


def _entry_preset(entry: dict) -> str | None:
    preset = entry.get("preset")
    if isinstance(preset, str) and preset.strip():
        return preset.strip().lower()
    return None


def _resolve_setting(entry: dict, key: str) -> RigSetting:
    preset = _entry_preset(entry)
    if key == "model":
        if entry.get("model"):
            return RigSetting("model", str(entry["model"]), "explicit", True)
        return RigSetting(
            "model", None, "preset default" if preset else "built-in default", False
        )
    if key == "concurrency":
        if "concurrency" in entry:
            return RigSetting(key, int(entry["concurrency"]), "explicit", True)
        return RigSetting(key, DEFAULT_CONCURRENCY, "built-in default", False)
    if key in CONFIGURABLE_FLOAT_KEYS:
        if key in entry:
            return RigSetting(key, float(entry[key]), "explicit", True)
        return RigSetting(key, None, "built-in default", False)
    if key in entry:
        return RigSetting(key, int(entry[key]), "explicit", True)
    if preset and HARNESS_PRESETS.get(preset, {}).get("text_tier"):
        return RigSetting(key, text_defaults(preset)[key], f"from preset {preset}", False)
    return RigSetting(key, text_defaults(None)[key], "built-in default", False)


def rig_settings(path: Path, rig_name: str) -> list[RigSetting]:
    """Every configurable setting for a rig, effective value + source."""
    _, entry, _ = _rig_entry(path, rig_name)
    return [_resolve_setting(entry, key) for key in CONFIGURABLE_RIG_KEYS]


def rig_setting(path: Path, rig_name: str, key: str) -> RigSetting:
    key = key.strip().lower()
    if key not in CONFIGURABLE_RIG_KEYS:
        raise _unknown_setting_error(key)
    _, entry, _ = _rig_entry(path, rig_name)
    return _resolve_setting(entry, key)


def _parse_setting_number(key: str, raw: object) -> int | float:
    text = str(raw).strip()
    try:
        number = float(text)
    except ValueError:
        raise RigError(
            f"{key} must be a number, got {raw!r} "
            f"(try: r4t rig set <rig> {key} <number>)"
        )
    if number <= 0:
        raise RigError(f"{key} must be a positive number, got {raw!r}")
    if key in CONFIGURABLE_INT_KEYS:
        if number != int(number):
            raise RigError(f"{key} must be a whole number, got {raw!r}")
        return int(number)
    return number


def set_rig_model(path: Path, rig_name: str, model: str) -> None:
    """Re-resolve a rig's invoke for a new --model through its recorded preset,
    exactly the way `rig add --model` does. agy keeps its live resolver; a rig
    with no recorded preset errors, because there is nothing to substitute into."""
    rig_key, entry, payload = _rig_entry(path, rig_name)
    preset = _entry_preset(entry)
    if preset is None:
        raise RigError(
            f"rig {rig_key!r} has no recorded preset to re-resolve model through "
            f"(try: r4t rig swap {rig_key} <preset> --model {model})"
        )
    entry["invoke"] = build_preset_invoke(preset, model=model)
    entry.pop("model", None)
    entry.pop("model_resolver", None)
    if HARNESS_PRESETS.get(preset, {}).get("model_resolver"):
        entry["model"] = model.strip()
        entry["model_resolver"] = HARNESS_PRESETS[preset]["model_resolver"]
    atomic_write_json(path, payload)


def set_rig_value(path: Path, rig_name: str, key: str, value: object) -> RigSetting:
    """Write one explicit rig setting. Numeric keys validate as numbers; `model`
    re-resolves the invoke through the preset. Returns the resulting setting."""
    key = key.strip().lower()
    if key not in CONFIGURABLE_RIG_KEYS:
        raise _unknown_setting_error(key)
    rig_key = _validate_rig_name(rig_name)
    if key == "model":
        model = str(value).strip()
        set_rig_model(path, rig_key, model)
        return RigSetting("model", model, "explicit", True)
    number = _parse_setting_number(key, value)
    _, entry, payload = _rig_entry(path, rig_name)
    entry[key] = number
    atomic_write_json(path, payload)
    return RigSetting(key, number, "explicit", True)


def unset_rig_value(path: Path, rig_name: str, key: str) -> bool:
    """Drop an explicit setting so it falls back to preset tier / built-in
    default. Returns True if something was removed, False if it was not
    explicitly set (a friendly no-op). `model` re-resolves the invoke to the
    preset's base."""
    key = key.strip().lower()
    if key not in CONFIGURABLE_RIG_KEYS:
        raise _unknown_setting_error(key)
    rig_key, entry, payload = _rig_entry(path, rig_name)
    if key == "model":
        preset = _entry_preset(entry)
        if preset is None:
            raise RigError(
                f"rig {rig_key!r} has no recorded preset (try: r4t rig list)"
            )
        base_invoke = build_preset_invoke(preset)
        had_model = "model" in entry or entry.get("invoke") != base_invoke
        entry["invoke"] = base_invoke
        entry.pop("model", None)
        entry.pop("model_resolver", None)
        if had_model:
            atomic_write_json(path, payload)
        return had_model
    if key not in entry:
        return False
    del entry[key]
    atomic_write_json(path, payload)
    return True


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

    resolver = raw.get("model_resolver")
    if resolver is not None:
        rig.model_resolver = str(resolver)
        rig.model = str(raw.get("model") or "").strip() or None

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
    max_sends, err = _positive_number(
        raw.get("max_sends_per_turn"), DEFAULT_MAX_SENDS_PER_TURN
    )
    if err:
        problems.append(f"max_sends_per_turn: {err}")
    rig.max_sends_per_turn = int(max_sends)
    rig.budget_max, err = _positive_number(raw.get("budget_max"), DEFAULT_BUDGET_MAX)
    if err:
        problems.append(f"budget_max: {err}")
    rig.budget_earn_per_hour, err = _positive_number(
        raw.get("budget_earn_per_hour"), DEFAULT_BUDGET_EARN_PER_HOUR
    )
    if err:
        problems.append(f"budget_earn_per_hour: {err}")

    # History/prompt sizing rides the rig, defaulted by the preset's text tier
    # — a 0.6B local member and an agy seat should not share a history budget.
    # Explicit values in rigs.json win; a rig with no `preset` (custom CLI)
    # gets the conservative small tier.
    defaults = text_defaults(raw.get("preset") if isinstance(raw.get("preset"), str) else None)
    for knob in ("history_max_bytes", "history_body_max", "prompt_body_max"):
        value, err = _positive_number(raw.get(knob), defaults[knob])
        if err:
            problems.append(f"{knob}: {err}")
        setattr(rig, knob, int(value))

    # The rig spend bucket is opt-in: absent leaves both None and the rig gate
    # off. If present, both knobs are required — a real subscription always
    # declares a refill rate, and a max without one would rest forever.
    raw_rig_max = raw.get("rig_budget_max")
    raw_rig_earn = raw.get("rig_budget_earn_per_hour")
    if raw_rig_max is not None:
        rig.rig_budget_max, err = _positive_number(raw_rig_max, 0.0)
        if err:
            problems.append(f"rig_budget_max: {err}")
        if raw_rig_earn is None:
            problems.append("rig_budget_max set but rig_budget_earn_per_hour missing")
        else:
            rig.rig_budget_earn_per_hour, err = _positive_number(raw_rig_earn, 0.0)
            if err:
                problems.append(f"rig_budget_earn_per_hour: {err}")
    elif raw_rig_earn is not None:
        problems.append("rig_budget_earn_per_hour set but rig_budget_max missing")

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
        if key == "breaker_cap":
            n = _non_negative_number(value, 0, key)
            if n <= 0:
                raise RigError(f"{key} must be positive, got {value!r}")
            setattr(config, key, int(n))
            continue
        if key in (
            "cell_budget_max",
            "cell_budget_earn_per_hour",
            "breaker_cooldown_seconds",
            "quiet_task_seconds",
        ):
            n = _non_negative_number(value, 0, key)
            if n <= 0:
                raise RigError(f"{key} must be positive, got {value!r}")
            setattr(config, key, n)
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
