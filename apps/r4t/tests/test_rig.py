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
    load_rig_config,
    preset_names,
    swap_preset_rig,
)
from roster import Member


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
