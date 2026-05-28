"""Tests for registry.py — schema I/O, alias resolution, sender lookup,
marker-file scan."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from registry import (
    _load_raw_registry,
    _scan_for_markers,
    find_participant,
    load_aliases,
    load_registry,
    parse_name,
    participants_from_registry,
    resolve_name,
    resolve_recipient,
    save_aliases,
    save_registry,
    sender_from_cwd,
)
from core import Participant, canonical_name, registry_path


# ---------- canonical_name ----------

class TestCanonicalName:
    """Issue #65 — names canonicalize to lowercase at registration boundaries
    (`a8s add`, `a8s alias`) so directory keys collapse and the case-collision
    footgun (two distinct `~/.a8s/agents/<NAME>/` dirs for `claude` vs
    `Claude`) goes away."""

    def test_lowercases(self):
        assert canonical_name("CLAUDE") == "claude"

    def test_strips(self):
        assert canonical_name("  claude  ") == "claude"

    def test_mixed_case(self):
        assert canonical_name("Claude") == "claude"

    def test_alphanumeric_passes(self):
        assert canonical_name("agent42") == "agent42"

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            canonical_name("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValueError):
            canonical_name("   ")

    def test_hyphen_accepted(self):
        assert canonical_name("knobert-android") == "knobert-android"

    def test_underscore_accepted(self):
        assert canonical_name("foo_bar") == "foo_bar"

    def test_space_rejected(self):
        with pytest.raises(ValueError):
            canonical_name("foo bar")

    def test_leading_separator_rejected(self):
        with pytest.raises(ValueError):
            canonical_name("-foo")
        with pytest.raises(ValueError):
            canonical_name("_foo")


# ---------- low-level I/O ----------

class TestRegistryIO:
    def test_empty_registry_returns_zero_sections(self, fake_home):
        raw = _load_raw_registry()
        assert raw == {"agents": {}, "aliases": {}}

    def test_save_and_load_agents(self, fake_home):
        save_registry({"CLAUDE": {"root": "/tmp/x", "definition": "/tmp/d.json"}})
        assert load_registry() == {"CLAUDE": {"root": "/tmp/x", "definition": "/tmp/d.json"}}

    def test_save_agents_preserves_aliases(self, fake_home):
        save_aliases({"devs": ["A", "B"]})
        save_registry({"X": {"root": "/r"}})
        assert load_aliases() == {"devs": ["A", "B"]}
        assert load_registry() == {"X": {"root": "/r"}}

    def test_save_aliases_preserves_agents(self, fake_home):
        save_registry({"X": {"root": "/r"}})
        save_aliases({"devs": ["X"]})
        assert load_registry() == {"X": {"root": "/r"}}
        assert load_aliases() == {"devs": ["X"]}

    def test_corrupt_file_returns_empty(self, fake_home):
        registry_path().write_text("not json")
        assert _load_raw_registry() == {"agents": {}, "aliases": {}}

    def test_non_dict_top_level_returns_empty(self, fake_home):
        registry_path().write_text("[1, 2, 3]")
        assert _load_raw_registry() == {"agents": {}, "aliases": {}}

    def test_missing_sections_default_empty(self, fake_home):
        registry_path().write_text(json.dumps({"agents": {"X": {"root": "/r"}}}))
        raw = _load_raw_registry()
        assert raw["agents"] == {"X": {"root": "/r"}}
        assert raw["aliases"] == {}


# ---------- resolve_name ----------

class TestResolveName:
    def test_agent_name_resolves_to_self(self, fake_home):
        save_registry({"CLAUDE": {"root": "/r"}})
        kind, members = resolve_name("CLAUDE")
        assert kind == "agent"
        assert members == ["CLAUDE"]

    def test_case_insensitive(self, fake_home):
        save_registry({"CLAUDE": {"root": "/r"}})
        kind, members = resolve_name("claude")
        assert kind == "agent"
        assert members == ["CLAUDE"]  # canonical casing

    def test_unknown_raises(self, fake_home):
        with pytest.raises(KeyError):
            resolve_name("BOGUS")

    def test_alias_expands(self, fake_home):
        save_registry({"A": {"root": "/a"}, "B": {"root": "/b"}})
        save_aliases({"devs": ["A", "B"]})
        kind, members = resolve_name("devs")
        assert kind == "alias"
        assert members == ["A", "B"]

    def test_nested_alias(self, fake_home):
        save_registry({"A": {"root": "/a"}, "B": {"root": "/b"}, "C": {"root": "/c"}})
        save_aliases({"inner": ["A", "B"], "outer": ["inner", "C"]})
        kind, members = resolve_name("outer")
        assert kind == "alias"
        assert members == ["A", "B", "C"]

    def test_nested_alias_dedupes(self, fake_home):
        save_registry({"A": {"root": "/a"}, "B": {"root": "/b"}})
        save_aliases({"x": ["A"], "y": ["A", "B"], "z": ["x", "y"]})
        kind, members = resolve_name("z")
        # A appears via both x and y; should be listed only once.
        assert members == ["A", "B"]

    def test_cycle_raises(self, fake_home):
        save_registry({"A": {"root": "/a"}})
        # x -> y -> x
        save_aliases({"x": ["y"], "y": ["x"]})
        with pytest.raises(ValueError, match="alias cycle"):
            resolve_name("x")

    def test_dangling_reference_raises(self, fake_home):
        # alias points at an agent that doesn't exist
        save_aliases({"devs": ["MISSING"]})
        with pytest.raises(KeyError):
            resolve_name("devs")


# ---------- find_participant + sender_from_cwd ----------

class TestFindParticipant:
    def test_finds_by_exact_name(self):
        parts = [Participant("CLAUDE", Path("/r")), Participant("GEMINI", Path("/r2"))]
        assert find_participant(parts, "CLAUDE").name == "CLAUDE"

    def test_case_insensitive(self):
        parts = [Participant("CLAUDE", Path("/r"))]
        assert find_participant(parts, "claude").name == "CLAUDE"

    def test_unknown_returns_none(self):
        assert find_participant([], "X") is None


class TestSenderFromCwd:
    def test_finds_agent_from_inside_root(self, fake_home, tmp_path, monkeypatch):
        agent_dir = tmp_path / "myagent"
        agent_dir.mkdir()
        save_registry({"X": {"root": str(agent_dir)}})
        monkeypatch.chdir(agent_dir)
        result = sender_from_cwd()
        assert result is not None
        name, info = result
        assert name == "X"

    def test_finds_agent_from_subdir(self, fake_home, tmp_path, monkeypatch):
        agent_dir = tmp_path / "myagent"
        sub = agent_dir / "sub" / "deeper"
        sub.mkdir(parents=True)
        save_registry({"X": {"root": str(agent_dir)}})
        monkeypatch.chdir(sub)
        name, _ = sender_from_cwd()
        assert name == "X"

    def test_outside_any_agent_returns_none(self, fake_home, tmp_path, monkeypatch):
        agent_dir = tmp_path / "myagent"
        agent_dir.mkdir()
        save_registry({"X": {"root": str(agent_dir)}})
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        monkeypatch.chdir(outside)
        assert sender_from_cwd() is None


class TestResolveRecipient:
    def test_finds_agent(self, fake_home):
        save_registry({"CLAUDE": {"root": "/r"}})
        result = resolve_recipient("claude")
        assert result is not None
        name, info = result
        assert name == "CLAUDE"

    def test_does_not_resolve_aliases(self, fake_home):
        # resolve_recipient is exact-agent only; alias expansion is resolve_name.
        save_aliases({"devs": ["X"]})
        assert resolve_recipient("devs") is None


# ---------- participants_from_registry ----------

class TestParticipantsFromRegistry:
    def test_empty(self, fake_home):
        assert participants_from_registry() == []

    def test_builds_participants(self, fake_home, tmp_path):
        d1 = tmp_path / "a"; d1.mkdir()
        d2 = tmp_path / "b"; d2.mkdir()
        save_registry({"A": {"root": str(d1)}, "B": {"root": str(d2)}})
        parts = participants_from_registry()
        names = sorted(p.name for p in parts)
        assert names == ["A", "B"]

    def test_skips_entries_with_no_root(self, fake_home):
        save_registry({"A": {"root": "/tmp"}, "B": {}})
        parts = participants_from_registry()
        assert {p.name for p in parts} == {"A"}


# ---------- parse_name + _scan_for_markers ----------

class TestParseName:
    def test_first_hash_line(self, tmp_path):
        f = tmp_path / "CLAUDE.md"
        f.write_text("# CLAUDE: code review\n\nbody\n")
        assert parse_name(f) == "CLAUDE"

    def test_skips_blank_then_finds(self, tmp_path):
        f = tmp_path / "CLAUDE.md"
        f.write_text("\n\n# Llama: local\n")
        assert parse_name(f) == "Llama"

    def test_no_hash_line_returns_none(self, tmp_path):
        f = tmp_path / "CLAUDE.md"
        f.write_text("plain text\nno headings\n")
        assert parse_name(f) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert parse_name(tmp_path / "nope.md") is None


class TestScanForMarkers:
    def test_finds_marker_in_immediate_child(self, tmp_path):
        d = tmp_path / "agent1"; d.mkdir()
        (d / "CLAUDE.md").write_text("# A: x\n")
        found = _scan_for_markers(tmp_path)
        assert len(found) == 1
        name, kind, dirpath = found[0]
        assert name == "A"
        assert kind == "claude"
        assert dirpath == d.resolve()

    def test_finds_marker_in_root_itself(self, tmp_path):
        (tmp_path / "GEMINI.md").write_text("# G: y\n")
        found = _scan_for_markers(tmp_path)
        assert len(found) == 1
        assert found[0][1] == "agy"

    def test_no_markers(self, tmp_path):
        assert _scan_for_markers(tmp_path) == []

    def test_one_marker_per_dir(self, tmp_path):
        # If both CLAUDE.md and GEMINI.md exist, _scan_for_markers picks the
        # FIRST marker iteration finds (per MARKER_FILES order).
        d = tmp_path / "x"; d.mkdir()
        (d / "CLAUDE.md").write_text("# A: x\n")
        (d / "GEMINI.md").write_text("# B: y\n")
        found = _scan_for_markers(tmp_path)
        # Only one entry per directory (the break in _scan_for_markers).
        assert len(found) == 1

    def test_finds_agents_md_as_opencode_fallback(self, tmp_path):
        d = tmp_path / "agent1"; d.mkdir()
        (d / "AGENTS.md").write_text("# O: opencode helper\n")
        found = _scan_for_markers(tmp_path)
        assert len(found) == 1
        name, kind, dirpath = found[0]
        assert name == "O"
        assert kind == "opencode"
        assert dirpath == d.resolve()

    def test_specific_marker_wins_over_agents_md(self, tmp_path):
        d = tmp_path / "agent1"; d.mkdir()
        (d / "CLAUDE.md").write_text("# A: x\n")
        (d / "AGENTS.md").write_text("# B: y\n")
        found = _scan_for_markers(tmp_path)
        assert len(found) == 1
        assert found[0][1] == "claude"
