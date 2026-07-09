from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import (
    DEFAULT_CONCURRENCY,
    DEFAULT_HOP_LIMIT,
    DEFAULT_MAX_TURNS_PER_TASK,
    DEFAULT_TIMEOUT_SECONDS,
    HarnessError,
    load_harness_config,
)
from roster import Member


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "harnesses.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def member(name="Phil", harness="junior-dev") -> Member:
    return Member(name=name, harness=harness)


class TestLoading:
    def test_tiers_and_defaults(self, tmp_path):
        config = load_harness_config(
            write_config(tmp_path, {"fast": {"invoke": ["run", "{prompt}"]}})
        )
        tier = config.tiers["fast"]
        assert tier.invoke == ["run", "{prompt}"]
        assert tier.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert tier.concurrency == DEFAULT_CONCURRENCY
        assert tier.max_turns_per_task == DEFAULT_MAX_TURNS_PER_TASK
        assert tier.hop_limit == DEFAULT_HOP_LIMIT

    def test_explicit_limits(self, tmp_path):
        config = load_harness_config(
            write_config(
                tmp_path,
                {
                    "t": {
                        "invoke": ["x", "{prompt}"],
                        "timeout_seconds": 60,
                        "concurrency": 3,
                        "max_turns_per_task": 10,
                        "hop_limit": 2,
                    }
                },
            )
        )
        tier = config.tiers["t"]
        assert (tier.timeout_seconds, tier.concurrency) == (60, 3)
        assert (tier.max_turns_per_task, tier.hop_limit) == (10, 2)

    def test_comment_keys_ignored(self, tmp_path):
        config = load_harness_config(
            write_config(
                tmp_path,
                {
                    "_comment": "hi",
                    "t": {"_comment": "x", "invoke": ["x", "{prompt}"]},
                    "pins": {"_comment": "x", "phil": "t"},
                },
            )
        )
        assert list(config.tiers) == ["t"]
        assert config.pins == {"phil": "t"}

    def test_tier_names_case_insensitive(self, tmp_path):
        config = load_harness_config(
            write_config(tmp_path, {"Leader": {"invoke": ["x", "{prompt}"]}})
        )
        tier, err, _ = config.tier_for(member(harness="leader"))
        assert err is None
        assert tier.name == "leader"

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "harnesses.json"
        path.write_text("{nope", encoding="utf-8")
        with pytest.raises(HarnessError):
            load_harness_config(path)

    def test_non_object_raises(self, tmp_path):
        path = tmp_path / "harnesses.json"
        path.write_text("[1,2]", encoding="utf-8")
        with pytest.raises(HarnessError):
            load_harness_config(path)


class TestFailClosed:
    def test_missing_config_file(self, tmp_path):
        config = load_harness_config(tmp_path / "absent.json")
        assert config.missing
        tier, err, _ = config.tier_for(member())
        assert tier is None
        assert "fail closed" in err

    def test_unknown_tier(self, tmp_path):
        config = load_harness_config(
            write_config(tmp_path, {"other": {"invoke": ["x", "{prompt}"]}})
        )
        tier, err, _ = config.tier_for(member(harness="junior-dev"))
        assert tier is None
        assert "junior-dev" in err and "not found" in err

    def test_invoke_without_prompt_placeholder(self, tmp_path):
        config = load_harness_config(write_config(tmp_path, {"t": {"invoke": ["x"]}}))
        tier, err, _ = config.tier_for(member(harness="t"))
        assert tier is None
        assert "{prompt}" in err

    def test_empty_invoke(self, tmp_path):
        config = load_harness_config(write_config(tmp_path, {"t": {"invoke": []}}))
        tier, err, _ = config.tier_for(member(harness="t"))
        assert tier is None

    def test_bad_limit_invalidates_tier(self, tmp_path):
        config = load_harness_config(
            write_config(
                tmp_path,
                {"t": {"invoke": ["x", "{prompt}"], "timeout_seconds": -5}},
            )
        )
        tier, err, _ = config.tier_for(member(harness="t"))
        assert tier is None
        assert "timeout_seconds" in err

    def test_member_without_tier(self, tmp_path):
        config = load_harness_config(
            write_config(tmp_path, {"t": {"invoke": ["x", "{prompt}"]}})
        )
        tier, err, _ = config.tier_for(member(harness=None))
        assert tier is None


class TestPins:
    def test_pin_overrides_roster(self, tmp_path):
        config = load_harness_config(
            write_config(
                tmp_path,
                {
                    "cheap": {"invoke": ["c", "{prompt}"]},
                    "fancy": {"invoke": ["f", "{prompt}"]},
                    "pins": {"phil": "cheap"},
                },
            )
        )
        tier, err, pinned = config.tier_for(member(name="Phil", harness="fancy"))
        assert err is None
        assert pinned
        assert tier.name == "cheap"

    def test_pin_is_case_insensitive(self, tmp_path):
        config = load_harness_config(
            write_config(
                tmp_path,
                {"cheap": {"invoke": ["c", "{prompt}"]}, "pins": {"PHIL": "Cheap"}},
            )
        )
        tier, err, pinned = config.tier_for(member(name="phil", harness=None))
        assert err is None and pinned and tier.name == "cheap"

    def test_pin_to_unknown_tier_fails_closed(self, tmp_path):
        config = load_harness_config(
            write_config(
                tmp_path,
                {"cheap": {"invoke": ["c", "{prompt}"]}, "pins": {"phil": "gone"}},
            )
        )
        tier, err, pinned = config.tier_for(member(name="Phil", harness="cheap"))
        assert tier is None and pinned


class TestArgv:
    def test_prompt_substitution_single_element(self, tmp_path):
        config = load_harness_config(
            write_config(tmp_path, {"t": {"invoke": ["run", "-p", "{prompt}"]}})
        )
        argv = config.tiers["t"].argv('hello "world"; rm -rf /')
        assert argv == ["run", "-p", 'hello "world"; rm -rf /']

    def test_embedded_placeholder(self, tmp_path):
        config = load_harness_config(
            write_config(tmp_path, {"t": {"invoke": ["run", "prompt={prompt}"]}})
        )
        assert config.tiers["t"].argv("X") == ["run", "prompt=X"]
