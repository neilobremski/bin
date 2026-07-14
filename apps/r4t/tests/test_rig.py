from __future__ import annotations

import json
from pathlib import Path

import pytest

from rig import (
    DEFAULT_BUDGET_EARN_PER_HOUR,
    DEFAULT_BUDGET_MAX,
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_SENDS_PER_TURN,
    DEFAULT_MIN_SECONDS_BETWEEN_TURN_STARTS,
    DEFAULT_CELL_BUDGET_EARN_PER_HOUR,
    DEFAULT_CELL_BUDGET_MAX,
    DEFAULT_TIMEOUT_SECONDS,
    HARNESS_PRESETS,
    RigError,
    add_preset_rig,
    build_preset_invoke,
    default_config_payload,
    format_preset_invoke,
    fuzzy_match_model,
    load_rig_config,
    preset_names,
    remove_rig,
    resolve_agy_model,
    swap_preset_rig,
)
from roster import Member
from r4t import main as r4t_main


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "rigs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def member(name="Phil", rig="junior-dev") -> Member:
    return Member(name=name, rig=rig)


class TestLoading:
    def test_rigs_and_defaults(self, tmp_path):
        config = load_rig_config(
            write_config(tmp_path, {"fast": {"invoke": ["run", "{prompt}"]}})
        )
        rig = config.rigs["fast"]
        assert rig.invoke == ["run", "{prompt}"]
        assert rig.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert rig.concurrency == DEFAULT_CONCURRENCY
        assert rig.max_sends_per_turn == DEFAULT_MAX_SENDS_PER_TURN
        assert rig.budget_max == DEFAULT_BUDGET_MAX == 8.0
        assert rig.budget_earn_per_hour == DEFAULT_BUDGET_EARN_PER_HOUR == 4.0

    def test_zero_config_gets_full_protection(self, tmp_path):
        config = load_rig_config(
            write_config(tmp_path, {"t": {"invoke": ["x", "{prompt}"]}})
        )
        assert config.throttle.max_concurrent == DEFAULT_MAX_CONCURRENT == 1
        assert (
            config.throttle.min_seconds_between_turn_starts
            == DEFAULT_MIN_SECONDS_BETWEEN_TURN_STARTS
            == 15.0
        )
        assert config.cell_budget_max == DEFAULT_CELL_BUDGET_MAX == 16.0
        assert (
            config.cell_budget_earn_per_hour == DEFAULT_CELL_BUDGET_EARN_PER_HOUR == 8.0
        )
        assert config.breaker_cap == 5
        assert config.breaker_cooldown_seconds == 600.0
        assert config.quiet_task_seconds == 1800.0

    def test_explicit_limits(self, tmp_path):
        config = load_rig_config(
            write_config(
                tmp_path,
                {
                    "t": {
                        "invoke": ["x", "{prompt}"],
                        "timeout_seconds": 60,
                        "concurrency": 3,
                        "budget_max": 10,
                        "budget_earn_per_hour": 2,
                    }
                },
            )
        )
        rig = config.rigs["t"]
        assert (rig.timeout_seconds, rig.concurrency) == (60, 3)
        assert (rig.budget_max, rig.budget_earn_per_hour) == (10, 2)

    def test_explicit_governance_keys(self, tmp_path):
        config = load_rig_config(
            write_config(
                tmp_path,
                {
                    "t": {"invoke": ["x", "{prompt}"]},
                    "throttle": {"max_concurrent": 0, "min_seconds_between_turn_starts": 0},
                    "cell_budget_max": 4,
                    "cell_budget_earn_per_hour": 2,
                    "breaker_cap": 2,
                    "breaker_cooldown_seconds": 30,
                    "quiet_task_seconds": 60,
                },
            )
        )
        assert config.throttle.max_concurrent == 0
        assert config.throttle.min_seconds_between_turn_starts == 0
        assert config.cell_budget_max == 4
        assert config.cell_budget_earn_per_hour == 2
        assert config.breaker_cap == 2
        assert config.breaker_cooldown_seconds == 30
        assert config.quiet_task_seconds == 60

    def test_bad_governance_values_raise(self, tmp_path):
        for key, value in (
            ("cell_budget_max", 0),
            ("cell_budget_earn_per_hour", -1),
            ("breaker_cap", 0),
            ("breaker_cooldown_seconds", -5),
            ("quiet_task_seconds", 0),
        ):
            with pytest.raises(RigError):
                load_rig_config(
                    write_config(tmp_path, {"t": {"invoke": ["x", "{prompt}"]}, key: value})
                )

    def test_comment_keys_ignored(self, tmp_path):
        config = load_rig_config(
            write_config(
                tmp_path,
                {
                    "_comment": "hi",
                    "t": {"_comment": "x", "invoke": ["x", "{prompt}"]},
                    "pins": {"_comment": "x", "phil": "t"},
                },
            )
        )
        assert list(config.rigs) == ["t"]
        assert config.pins == {"phil": "t"}

    def test_rig_names_case_insensitive(self, tmp_path):
        config = load_rig_config(
            write_config(tmp_path, {"Leader": {"invoke": ["x", "{prompt}"]}})
        )
        rig, err, _ = config.rig_for(member(rig="leader"))
        assert err is None
        assert rig.name == "leader"

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "rigs.json"
        path.write_text("{nope", encoding="utf-8")
        with pytest.raises(RigError):
            load_rig_config(path)

    def test_non_object_raises(self, tmp_path):
        path = tmp_path / "rigs.json"
        path.write_text("[1,2]", encoding="utf-8")
        with pytest.raises(RigError):
            load_rig_config(path)


class TestFailClosed:
    def test_missing_config_file(self, tmp_path):
        config = load_rig_config(tmp_path / "absent.json")
        assert config.missing
        rig, err, _ = config.rig_for(member())
        assert rig is None
        assert "fail closed" in err

    def test_unknown_rig(self, tmp_path):
        config = load_rig_config(
            write_config(tmp_path, {"other": {"invoke": ["x", "{prompt}"]}})
        )
        rig, err, _ = config.rig_for(member(rig="junior-dev"))
        assert rig is None
        assert "junior-dev" in err and "not found" in err

    def test_invoke_without_prompt_placeholder(self, tmp_path):
        config = load_rig_config(write_config(tmp_path, {"t": {"invoke": ["x"]}}))
        rig, err, _ = config.rig_for(member(rig="t"))
        assert rig is None
        assert "{prompt}" in err

    def test_empty_invoke(self, tmp_path):
        config = load_rig_config(write_config(tmp_path, {"t": {"invoke": []}}))
        rig, err, _ = config.rig_for(member(rig="t"))
        assert rig is None

    def test_bad_limit_invalidates_rig(self, tmp_path):
        config = load_rig_config(
            write_config(
                tmp_path,
                {"t": {"invoke": ["x", "{prompt}"], "timeout_seconds": -5}},
            )
        )
        rig, err, _ = config.rig_for(member(rig="t"))
        assert rig is None
        assert "timeout_seconds" in err

    def test_member_without_rig(self, tmp_path):
        config = load_rig_config(
            write_config(tmp_path, {"t": {"invoke": ["x", "{prompt}"]}})
        )
        rig, err, _ = config.rig_for(member(rig=None))
        assert rig is None


class TestPins:
    def test_pin_overrides_roster(self, tmp_path):
        config = load_rig_config(
            write_config(
                tmp_path,
                {
                    "cheap": {"invoke": ["c", "{prompt}"]},
                    "fancy": {"invoke": ["f", "{prompt}"]},
                    "pins": {"phil": "cheap"},
                },
            )
        )
        rig, err, pinned = config.rig_for(member(name="Phil", rig="fancy"))
        assert err is None
        assert pinned
        assert rig.name == "cheap"

    def test_pin_is_case_insensitive(self, tmp_path):
        config = load_rig_config(
            write_config(
                tmp_path,
                {"cheap": {"invoke": ["c", "{prompt}"]}, "pins": {"PHIL": "Cheap"}},
            )
        )
        rig, err, pinned = config.rig_for(member(name="phil", rig=None))
        assert err is None and pinned and rig.name == "cheap"

    def test_pin_to_unknown_rig_fails_closed(self, tmp_path):
        config = load_rig_config(
            write_config(
                tmp_path,
                {"cheap": {"invoke": ["c", "{prompt}"]}, "pins": {"phil": "gone"}},
            )
        )
        rig, err, pinned = config.rig_for(member(name="Phil", rig="cheap"))
        assert rig is None and pinned


class TestArgv:
    def test_prompt_substitution_single_element(self, tmp_path):
        config = load_rig_config(
            write_config(tmp_path, {"t": {"invoke": ["run", "-p", "{prompt}"]}})
        )
        argv = config.rigs["t"].argv('hello "world"; rm -rf /')
        assert argv == ["run", "-p", 'hello "world"; rm -rf /']

    def test_embedded_placeholder(self, tmp_path):
        config = load_rig_config(
            write_config(tmp_path, {"t": {"invoke": ["run", "prompt={prompt}"]}})
        )
        assert config.rigs["t"].argv("X") == ["run", "prompt=X"]


class TestDefaultPayload:
    def test_init_payload_parses_with_both_rigs(self, tmp_path):
        config = load_rig_config(write_config(tmp_path, default_config_payload()))
        assert set(config.rigs) == {"leader", "member"}
        for rig in config.rigs.values():
            assert rig.error is None
            assert any("{prompt}" in a for a in rig.pool()[0])


class TestHarnessPresets:
    def test_preset_names_match_a8s_kinds(self):
        assert preset_names() == [
            "agy", "claude", "codex", "copilot", "cursor", "ollama", "opencode",
            "opencode-ollama",
        ]

    def test_every_preset_declares_a_known_text_tier(self):
        from rig import TEXT_TIERS

        tiers = {name: HARNESS_PRESETS[name]["text_tier"] for name in preset_names()}
        assert set(tiers.values()) <= set(TEXT_TIERS)
        assert tiers == {
            "agy": "big", "claude": "big", "codex": "big",
            "copilot": "moderate", "cursor": "moderate", "opencode": "moderate",
            "ollama": "small", "opencode-ollama": "small",
        }

    def test_text_tier_anchors(self):
        from rig import TEXT_TIERS

        assert TEXT_TIERS["big"]["history_max_bytes"] == 50_000
        assert TEXT_TIERS["moderate"]["history_max_bytes"] == 25_000
        assert TEXT_TIERS["small"] == {
            "history_max_bytes": 8192, "history_body_max": 2000,
            "prompt_body_max": 4000,
        }

    def test_no_preset_key_gets_small_defaults(self, tmp_path):
        config = load_rig_config(
            write_config(tmp_path, {"custom": {"invoke": ["my-cli", "{prompt}"]}})
        )
        rig = config.rigs["custom"]
        assert (rig.history_max_bytes, rig.history_body_max, rig.prompt_body_max) == (
            8192, 2000, 4000,
        )

    def test_unknown_preset_value_gets_small_defaults(self, tmp_path):
        config = load_rig_config(write_config(
            tmp_path,
            {"custom": {"invoke": ["x", "{prompt}"], "preset": "gemini"}},
        ))
        assert config.rigs["custom"].history_max_bytes == 8192

    def test_preset_tier_defaults_and_explicit_override(self, tmp_path):
        config = load_rig_config(write_config(tmp_path, {
            "big": {"invoke": ["codex", "{prompt}"], "preset": "codex"},
            "mid": {"invoke": ["copilot", "{prompt}"], "preset": "copilot"},
            "pinned": {
                "invoke": ["claude", "{prompt}"], "preset": "claude",
                "history_max_bytes": 999,
            },
        }))
        big, mid, pinned = (config.rigs[k] for k in ("big", "mid", "pinned"))
        assert (big.history_max_bytes, big.history_body_max, big.prompt_body_max) == (
            50_000, 12_000, 24_000,
        )
        assert (mid.history_max_bytes, mid.history_body_max, mid.prompt_body_max) == (
            25_000, 6_000, 12_000,
        )
        assert pinned.history_max_bytes == 999  # explicit wins over the tier
        assert pinned.history_body_max == 12_000  # untouched knobs stay tiered

    def test_every_preset_invoke_is_valid(self, tmp_path):
        for name in preset_names():
            config = load_rig_config(
                write_config(tmp_path, {name: {"invoke": HARNESS_PRESETS[name]["invoke"]}})
            )
            rig = config.rigs[name]
            assert rig.error is None
            assert "{prompt}" in format_preset_invoke(name)

    def test_add_preset_rig_writes_new_config(self, tmp_path):
        path = tmp_path / "rigs.json"
        rig_key = add_preset_rig(path, "worker", "claude")
        assert rig_key == "worker"
        config = load_rig_config(path)
        assert config.rigs["worker"].error is None
        assert config.rigs["worker"].argv("hi")[0] == "claude"

    def test_add_preset_rig_refuses_duplicate(self, tmp_path):
        path = write_config(tmp_path, {"worker": {"invoke": ["x", "{prompt}"]}})
        with pytest.raises(RigError, match="already exists"):
            add_preset_rig(path, "worker", "opencode")

    def test_add_preset_rig_force_replaces(self, tmp_path):
        path = write_config(tmp_path, {"worker": {"invoke": ["x", "{prompt}"]}})
        add_preset_rig(path, "worker", "opencode", force=True)
        config = load_rig_config(path)
        assert config.rigs["worker"].argv("hi")[0] == "opencode"

    def test_add_preset_rig_opencode_ollama_requires_model(self, tmp_path):
        path = tmp_path / "rigs.json"
        with pytest.raises(RigError, match="requires --model"):
            add_preset_rig(path, "worker", "opencode-ollama")

    def test_add_preset_rig_opencode_ollama_materializes_model(self, tmp_path):
        path = tmp_path / "rigs.json"
        rig_key = add_preset_rig(
            path, "worker", "opencode-ollama", model="qwen2.5-coder:7b"
        )
        assert rig_key == "worker"
        config = load_rig_config(path)
        argv = config.rigs["worker"].argv("hi")
        assert argv[4] == "qwen2.5-coder:7b"
        assert "{model}" not in argv

    def test_add_unknown_preset(self, tmp_path):
        path = tmp_path / "rigs.json"
        with pytest.raises(RigError, match="unknown preset"):
            add_preset_rig(path, "worker", "gemini")

    def test_swap_preset_rig_preserves_settings(self, tmp_path):
        path = write_config(tmp_path, {
            "worker": {
                "invoke": ["x", "{prompt}"],
                "concurrency": 3,
                "timeout_seconds": 120,
                "budget_max": 10,
                "budget_earn_per_hour": 2,
            },
        })
        rig_key = swap_preset_rig(path, "worker", "agy")
        assert rig_key == "worker"
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["worker"]["concurrency"] == 3
        assert raw["worker"]["timeout_seconds"] == 120
        assert raw["worker"]["budget_max"] == 10
        assert raw["worker"]["budget_earn_per_hour"] == 2
        assert "swap" in raw["worker"]["_notes"].lower()
        config = load_rig_config(path)
        assert config.rigs["worker"].argv("hi")[0] == "agy"
        assert config.rigs["worker"].concurrency == 3

    def test_add_records_preset_and_tier_defaults_apply(self, tmp_path):
        path = tmp_path / "rigs.json"
        add_preset_rig(path, "brain", "agy", model="sonnet")
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["brain"]["preset"] == "agy"
        rig = load_rig_config(path).rigs["brain"]
        assert rig.history_max_bytes == 50_000
        assert rig.history_body_max == 12_000
        assert rig.prompt_body_max == 24_000

    def test_swap_reresolves_tier_but_explicit_knob_wins(self, tmp_path):
        path = tmp_path / "rigs.json"
        add_preset_rig(path, "worker", "ollama", model="qwen3:0.6b")
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["worker"]["history_body_max"] = 1234  # operator's explicit value
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert load_rig_config(path).rigs["worker"].history_max_bytes == 8192

        swap_preset_rig(path, "worker", "claude")
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["worker"]["preset"] == "claude"
        rig = load_rig_config(path).rigs["worker"]
        assert rig.history_max_bytes == 50_000  # re-resolved to the big tier
        assert rig.prompt_body_max == 24_000
        assert rig.history_body_max == 1234  # explicit value survives the swap

    def test_swap_preset_rig_missing_rig(self, tmp_path):
        path = write_config(tmp_path, {"other": {"invoke": ["x", "{prompt}"]}})
        with pytest.raises(RigError, match="no rig 'worker' to swap"):
            swap_preset_rig(path, "worker", "claude")

    def test_swap_preset_rig_missing_config(self, tmp_path):
        with pytest.raises(RigError, match="no rig"):
            swap_preset_rig(tmp_path / "rigs.json", "worker", "claude")

    def test_swap_unknown_preset(self, tmp_path):
        path = write_config(tmp_path, {"worker": {"invoke": ["x", "{prompt}"]}})
        with pytest.raises(RigError, match="unknown preset"):
            swap_preset_rig(path, "worker", "gemini")

    def test_swap_preset_rig_requires_model(self, tmp_path):
        path = write_config(tmp_path, {"worker": {"invoke": ["x", "{prompt}"]}})
        with pytest.raises(RigError, match="requires --model"):
            swap_preset_rig(path, "worker", "opencode-ollama")

    def test_swap_preset_rig_materializes_model(self, tmp_path):
        path = write_config(tmp_path, {
            "worker": {"invoke": ["x", "{prompt}"], "max_sends_per_turn": 4},
        })
        swap_preset_rig(path, "worker", "opencode-ollama", model="qwen2.5-coder:7b")
        config = load_rig_config(path)
        argv = config.rigs["worker"].argv("hi")
        assert argv[4] == "qwen2.5-coder:7b"
        assert "{model}" not in argv
        assert config.rigs["worker"].max_sends_per_turn == 4
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "qwen2.5-coder:7b" in raw["worker"]["_notes"]

    def test_opencode_and_agy_presets_avoid_skip_permissions(self):
        opencode = " ".join(HARNESS_PRESETS["opencode"]["invoke"])
        agy = " ".join(HARNESS_PRESETS["agy"]["invoke"])
        assert "dangerously-skip-permissions" not in opencode
        assert "dangerously-skip-permissions" not in agy
        assert "--auto" in opencode
        assert "--mode" in agy and "accept-edits" in agy
        assert "--print" in agy
        assert "-i" not in opencode

    def test_build_preset_invoke_opencode(self):
        argv = build_preset_invoke("opencode")
        assert argv[0] == "opencode"
        assert "{prompt}" in argv

    def test_build_preset_invoke_opencode_ollama_requires_model(self):
        with pytest.raises(RigError, match="requires --model"):
            build_preset_invoke("opencode-ollama")
        argv = build_preset_invoke("opencode-ollama", model="qwen2.5-coder:7b")
        assert argv[:4] == ["ollama", "launch", "opencode", "--model"]
        assert argv[4] == "qwen2.5-coder:7b"
        assert argv[5:8] == ["--", "run", "--auto"]
        assert "{prompt}" in argv

    def test_build_preset_invoke_unknown(self):
        with pytest.raises(RigError, match="unknown preset"):
            build_preset_invoke("gemini")


AGY_MODEL_LIST = [
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
    "Gemini 3.5 Flash (Low)",
    "Gemini 3.1 Pro (Low)",
    "Gemini 3.1 Pro (High)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
]


class TestModelSplice:
    """--model splices at the right position for each preset (see the ruling on
    issue #186: static presets splice now, agy keeps a placeholder for live
    resolution)."""

    def test_optional_when_absent_returns_base(self):
        for preset in ("claude", "codex", "cursor", "opencode", "agy"):
            assert build_preset_invoke(preset) == HARNESS_PRESETS[preset]["invoke"]

    def test_claude_splices_after_executable(self):
        argv = build_preset_invoke("claude", model="sonnet")
        assert argv[:3] == ["claude", "--model", "sonnet"]
        assert argv[-2:] == ["-p", "{prompt}"]

    def test_codex_splices_after_exec(self):
        argv = build_preset_invoke("codex", model="gpt-5.6-sol")
        assert argv[:4] == ["codex", "exec", "-m", "gpt-5.6-sol"]
        assert argv[-1] == "{prompt}"

    def test_cursor_splices_after_executable(self):
        argv = build_preset_invoke("cursor", model="sonnet-4-thinking")
        assert argv[:3] == ["agent", "--model", "sonnet-4-thinking"]

    def test_opencode_splices_after_run(self):
        argv = build_preset_invoke("opencode", model="anthropic/claude-sonnet-4-5")
        assert argv[:4] == ["opencode", "run", "-m", "anthropic/claude-sonnet-4-5"]
        assert "--auto" in argv

    def test_agy_keeps_placeholder_for_live_resolution(self):
        argv = build_preset_invoke("agy", model="sonnet")
        assert argv[:3] == ["agy", "--model", "{model}"]
        assert "sonnet" not in argv

    def test_ollama_requires_model_and_substitutes(self):
        with pytest.raises(RigError, match="requires --model"):
            build_preset_invoke("ollama")
        argv = build_preset_invoke("ollama", model="qwen2.5-coder:7b")
        assert argv == ["ollama", "run", "qwen2.5-coder:7b", "{prompt}"]

    def test_preset_without_model_support_refuses_model(self):
        with pytest.raises(RigError, match="does not support --model"):
            build_preset_invoke("copilot", model="opus")

    def test_agy_add_persists_model_and_resolver(self, tmp_path):
        path = tmp_path / "rigs.json"
        add_preset_rig(path, "brain", "agy", model="sonnet")
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["brain"]["model"] == "sonnet"
        assert raw["brain"]["model_resolver"] == "agy-live"
        config = load_rig_config(path)
        assert config.rigs["brain"].model == "sonnet"
        assert config.rigs["brain"].model_resolver == "agy-live"

    def test_static_add_does_not_persist_resolver(self, tmp_path):
        path = tmp_path / "rigs.json"
        add_preset_rig(path, "coder", "claude", model="opus")
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "model_resolver" not in raw["coder"]
        assert raw["coder"]["invoke"][:3] == ["claude", "--model", "opus"]

    def test_swap_off_agy_drops_stale_resolver(self, tmp_path):
        path = write_config(tmp_path, {"worker": {"invoke": ["x", "{prompt}"]}})
        swap_preset_rig(path, "worker", "agy", model="sonnet")
        assert load_rig_config(path).rigs["worker"].model_resolver == "agy-live"
        swap_preset_rig(path, "worker", "claude", model="opus")
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "model_resolver" not in raw["worker"]
        assert "model" not in raw["worker"]


class TestFuzzyMatchModel:
    @pytest.mark.parametrize("query,expected", [
        ("sonnet", "Claude Sonnet 4.6 (Thinking)"),
        ("opus", "Claude Opus 4.6 (Thinking)"),
        ("gpt-oss", "GPT-OSS 120B (Medium)"),
        ("gpt-oss-120b", "GPT-OSS 120B (Medium)"),
        ("claude-sonnet", "Claude Sonnet 4.6 (Thinking)"),
        ("gemini-3.5-flash", "Gemini 3.5 Flash (High)"),
        ("Claude Sonnet 4.6 (Thinking)", "Claude Sonnet 4.6 (Thinking)"),
    ])
    def test_resolves(self, query, expected):
        assert fuzzy_match_model(query, AGY_MODEL_LIST) == expected

    def test_dashes_and_spaces_interchangeable(self):
        assert fuzzy_match_model("gemini 3.5 flash", AGY_MODEL_LIST) == fuzzy_match_model(
            "gemini-3.5-flash", AGY_MODEL_LIST
        )

    def test_tiebreak_prefers_highest_effort(self):
        # flash hits Low/Medium/High; the effort tie-break picks High.
        assert fuzzy_match_model("flash", AGY_MODEL_LIST) == "Gemini 3.5 Flash (High)"

    def test_ambiguous_family_tiebreak_is_deterministic(self):
        # gemini hits every Gemini line; extra-token count ties, effort favors
        # the High variants, alphabetical breaks Pro vs Flash -> Pro.
        assert fuzzy_match_model("gemini", AGY_MODEL_LIST) == "Gemini 3.1 Pro (High)"
        assert fuzzy_match_model("pro", AGY_MODEL_LIST) == "Gemini 3.1 Pro (High)"

    def test_garbage_errors_loudly_with_listing(self):
        with pytest.raises(RigError) as exc:
            fuzzy_match_model("banana", AGY_MODEL_LIST)
        msg = str(exc.value)
        assert "matched no agy model" in msg
        assert "Claude Sonnet 4.6 (Thinking)" in msg

    def test_resolve_agy_model_uses_injected_names(self):
        assert resolve_agy_model("opus", names=AGY_MODEL_LIST) == "Claude Opus 4.6 (Thinking)"


class TestRemoveRig:
    def test_remove_deletes_entry(self, tmp_path):
        path = write_config(tmp_path, {
            "worker": {"invoke": ["x", "{prompt}"]},
            "spare": {"invoke": ["y", "{prompt}"]},
        })
        assert remove_rig(path, "worker") == "worker"
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "worker" not in raw
        assert "spare" in raw

    def test_remove_unknown_errors(self, tmp_path):
        path = write_config(tmp_path, {"worker": {"invoke": ["x", "{prompt}"]}})
        with pytest.raises(RigError, match="no rig 'ghost' to remove"):
            remove_rig(path, "ghost")

    def test_remove_missing_config_errors(self, tmp_path):
        with pytest.raises(RigError, match="no rig"):
            remove_rig(tmp_path / "rigs.json", "worker")


class TestRemoveCLI:
    def test_remove_via_cli(self, tmp_path):
        path = write_config(tmp_path, {
            "a": {"invoke": ["x", "{prompt}"]},
            "b": {"invoke": ["y", "{prompt}"]},
        })
        assert r4t_main(["rig", "remove", "a", "--rig-config", str(path)]) == 0
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "a" not in raw and "b" in raw

    def test_rm_alias(self, tmp_path):
        path = write_config(tmp_path, {"a": {"invoke": ["x", "{prompt}"]}})
        assert r4t_main(["rig", "rm", "a", "--rig-config", str(path)]) == 0
        assert "a" not in json.loads(path.read_text(encoding="utf-8"))

    def test_remove_multiple(self, tmp_path):
        path = write_config(tmp_path, {
            "a": {"invoke": ["x", "{prompt}"]},
            "b": {"invoke": ["y", "{prompt}"]},
            "c": {"invoke": ["z", "{prompt}"]},
        })
        assert r4t_main(["rig", "remove", "a", "b", "--rig-config", str(path)]) == 0
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "a" not in raw and "b" not in raw and "c" in raw

    def test_remove_unknown_returns_1(self, tmp_path):
        path = write_config(tmp_path, {"a": {"invoke": ["x", "{prompt}"]}})
        assert r4t_main(["rig", "remove", "ghost", "--rig-config", str(path)]) == 1

    def test_remove_refuses_pinned_rig(self, tmp_path):
        path = write_config(tmp_path, {
            "a": {"invoke": ["x", "{prompt}"]},
            "pins": {"phil": "a"},
        })
        assert r4t_main(["rig", "remove", "a", "--rig-config", str(path)]) == 1
        assert "a" in json.loads(path.read_text(encoding="utf-8"))

    def test_remove_force_ignores_pin(self, tmp_path):
        path = write_config(tmp_path, {
            "a": {"invoke": ["x", "{prompt}"]},
            "pins": {"phil": "a"},
        })
        assert r4t_main(
            ["rig", "remove", "a", "--force", "--rig-config", str(path)]
        ) == 0
        assert "a" not in json.loads(path.read_text(encoding="utf-8"))
