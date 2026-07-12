from __future__ import annotations

from pathlib import Path

import pytest

from roster import (
    RosterError,
    load_roster,
    parse_roster,
    resolve_roster_path,
)


def parse(text: str):
    return parse_roster(text, Path("ROSTER.md"))


class TestParsing:
    def test_basic_fields(self, repo):
        roster = load_roster(repo / "ROSTER.md")
        gerry = roster.find("gerry")
        assert gerry is not None
        assert gerry.status == "AI"
        assert gerry.rig == "leader"
        assert gerry.role == "Technical Producer"
        assert gerry.leader
        assert not gerry.errors
        assert "Defends the schedule" in gerry.persona

    def test_cell_captured_when_declared(self, repo):
        gerry = load_roster(repo / "ROSTER.md").find("gerry")
        assert gerry.cell == "leadership"

    def test_cell_empty_when_absent(self, repo):
        phil = load_roster(repo / "ROSTER.md").find("phil")
        assert phil.cell == ""

    def test_lookup_is_case_insensitive(self, repo):
        roster = load_roster(repo / "ROSTER.md")
        assert roster.find("PHIL") is not None
        assert roster.find("Phil") is roster.find("phil")

    def test_unknown_member(self, repo):
        roster = load_roster(repo / "ROSTER.md")
        assert roster.find("nobody") is None

    def test_leader(self, repo):
        roster = load_roster(repo / "ROSTER.md")
        assert roster.leader().name == "Gerry"

    def test_no_leader(self):
        roster = parse("### Solo\n- **Status:** AI\n- **Rig:** t\n")
        assert roster.leader() is None

    def test_human_leader_not_dispatched_as_leader(self):
        roster = parse(
            "### Boss\n- **Status:** Human\n- **Leader:** yes\n"
            "### Dev\n- **Status:** AI\n- **Rig:** t\n"
        )
        assert roster.leader() is None

    def test_human(self, repo):
        neil = load_roster(repo / "ROSTER.md").find("neil")
        assert neil.is_human
        assert neil.address == "neil"
        assert not neil.errors

    def test_human_needs_no_harness(self):
        roster = parse("### Human\n- **Status:** Human\n")
        assert not roster.find("human").errors

    def test_backticked_rig(self):
        roster = parse("### A\n- **Status:** AI\n- **Rig:** `rig-1`\n")
        assert roster.find("a").rig == "rig-1"

    def test_rig_is_lowercased(self):
        roster = parse("### A\n- **Status:** AI\n- **Rig:** Leader\n")
        assert roster.find("a").rig == "leader"

    def test_mandate_accepted_as_role(self):
        roster = parse(
            "### A\n- **Status:** AI\n- **Rig:** t\n- **Mandate:** The Server\n"
        )
        assert roster.find("a").role == "The Server"

    def test_blocks_end_at_next_heading(self):
        roster = parse(
            "### A\n- **Status:** AI\n- **Rig:** t\npersona a\n"
            "## Section\nloose prose\n"
            "### B\n- **Status:** AI\n- **Rig:** t\n"
        )
        assert "loose prose" not in roster.find("a").persona
        assert roster.find("b") is not None


class TestMalformed:
    def test_bad_status_disables_member(self, repo):
        roster = load_roster(repo / "ROSTER.md")
        broken = roster.find("broken")
        assert broken.errors
        assert "Status" in broken.error

    def test_command_harness_disables_member(self):
        roster = parse(
            "### A\n- **Status:** AI\n- **Rig:** `agent -p --yolo {prompt}`\n"
        )
        member = roster.find("a")
        assert member.errors
        assert "symbolic rig" in member.error

    def test_ai_without_rig_disabled(self):
        roster = parse("### A\n- **Status:** AI\n")
        assert "missing Rig" in roster.find("a").error

    def test_duplicate_names_disable_both(self):
        roster = parse(
            "### A\n- **Status:** AI\n- **Rig:** t\n"
            "### a\n- **Status:** AI\n- **Rig:** t\n"
        )
        assert all("duplicate" in m.error for m in roster.members)

    def test_malformed_block_never_raises(self):
        roster = parse("### \n### A\n- **Status:**\n- garbage ** stuff\n")
        assert isinstance(roster.members, list)


class TestPathResolution:
    def test_default(self, tmp_path):
        assert resolve_roster_path(tmp_path, None) == tmp_path / "ROSTER.md"

    def test_root_relative(self, tmp_path):
        assert (
            resolve_roster_path(tmp_path, "docs/TEAM.md")
            == tmp_path / "docs" / "TEAM.md"
        )

    def test_absolute(self, tmp_path):
        target = tmp_path / "elsewhere.md"
        assert resolve_roster_path(tmp_path / "repo", str(target)) == target

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(RosterError):
            load_roster(tmp_path / "nope.md")
