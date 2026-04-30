"""Tests for definitions.py — single-verb argv interpolation, age formatting,
and auto-discovery."""
from __future__ import annotations

from pathlib import Path

import pytest

from datetime import datetime, timezone

from definitions import (
    _autodiscover_definition,
    _expand_argv,
    _format_age,
    _message_body,
    build_command,
    default_definition_path,
    load_definition,
)


# ---------- _format_age ----------

class TestFormatAge:
    NOW = datetime(2026, 4, 28, 14, 30, 0, tzinfo=timezone.utc)

    def _ago(self, **kwargs):
        from datetime import timedelta
        return (self.NOW - timedelta(**kwargs)).isoformat().replace("+00:00", "Z")

    def test_seconds(self):
        assert _format_age(self._ago(seconds=5), now=self.NOW) == "5 seconds ago"

    def test_singular_second(self):
        assert _format_age(self._ago(seconds=1), now=self.NOW) == "1 second ago"

    def test_zero_seconds(self):
        assert _format_age(self._ago(seconds=0), now=self.NOW) == "0 seconds ago"

    def test_minutes(self):
        assert _format_age(self._ago(minutes=5), now=self.NOW) == "5 minutes ago"

    def test_singular_minute(self):
        assert _format_age(self._ago(minutes=1), now=self.NOW) == "1 minute ago"

    def test_hours(self):
        assert _format_age(self._ago(hours=3), now=self.NOW) == "3 hours ago"

    def test_days(self):
        assert _format_age(self._ago(days=2), now=self.NOW) == "2 days ago"

    def test_weeks(self):
        assert _format_age(self._ago(days=14), now=self.NOW) == "2 weeks ago"

    def test_future_clamps_to_zero(self):
        # Clock skew shouldn't produce negative ages.
        from datetime import timedelta
        future = (self.NOW + timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        assert _format_age(future, now=self.NOW) == "0 seconds ago"

    def test_empty_string(self):
        assert _format_age("", now=self.NOW) == ""

    def test_unparseable(self):
        assert _format_age("not-a-date", now=self.NOW) == ""

    def test_iso_without_z_suffix(self):
        # `_write_outbox` writes `Z` but accept timezone-aware ISO too.
        ts = "2026-04-28T14:25:00+00:00"
        assert _format_age(ts, now=self.NOW) == "5 minutes ago"


# ---------- _message_body ----------

class TestMessageBody:
    def test_content_only(self):
        assert _message_body({"content": "hello"}) == "hello"

    def test_content_with_files(self):
        msg = {
            "content": "see attached",
            "files": [{"path": "/tmp/build.log"}, {"path": "/tmp/data.csv"}],
        }
        assert _message_body(msg) == "see attached\n\nFILE: /tmp/build.log\nFILE: /tmp/data.csv"

    def test_empty(self):
        assert _message_body({}) == ""


# ---------- build_command + _expand_argv ----------

class TestBuildCommand:
    def test_substitutes_sender_recipient_message(self):
        defn = {"invoke": ["claude", "--continue", "-p", "$SENDER tells $RECIPIENT: $MESSAGE"]}
        msg = {"from": "GERRY", "to": "CLAUDE", "content": "fix this"}
        argv = build_command(defn, msg)
        assert argv == ["claude", "--continue", "-p", "GERRY tells CLAUDE: fix this"]

    def test_alias_routed_keeps_alias_in_recipient(self):
        # Strict opacity / mailing-list semantics: when the sender wrote
        # `to: devs`, the recipient's $RECIPIENT resolves to "devs".
        defn = {"invoke": ["claude", "-p", "$SENDER tells $RECIPIENT: $MESSAGE"]}
        msg = {"from": "GERRY", "to": "devs", "content": "standup"}
        argv = build_command(defn, msg)
        assert argv == ["claude", "-p", "GERRY tells devs: standup"]

    def test_missing_invoke_raises(self):
        with pytest.raises(ValueError, match="invoke"):
            build_command({}, {"from": "G", "to": "C"})

    def test_a8s_dir_substitution(self):
        from core import SCRIPT_DIR
        defn = {"invoke": ["$A8S_DIR/dummy-cli", "$MESSAGE"]}
        msg = {"from": "A", "to": "B", "content": "hi"}
        argv = build_command(defn, msg)
        assert argv == [f"{SCRIPT_DIR}/dummy-cli", "hi"]

    def test_does_not_mutate_original_argv(self):
        defn = {"invoke": ["claude", "-p", "$MESSAGE"]}
        original = list(defn["invoke"])
        build_command(defn, {"from": "A", "to": "B", "content": "hello"})
        assert defn["invoke"] == original

    def test_message_body_includes_files(self):
        defn = {"invoke": ["x", "$MESSAGE"]}
        msg = {
            "from": "GERRY",
            "to": "CLAUDE",
            "content": "review",
            "files": [{"path": "/tmp/x"}],
        }
        argv = build_command(defn, msg)
        assert argv == ["x", "review\n\nFILE: /tmp/x"]

    def test_timestamp_substitution_from_msg_date(self):
        defn = {"invoke": ["x", "[$TIMESTAMP] $SENDER: $MESSAGE"]}
        msg = {
            "from": "GERRY",
            "to": "CLAUDE",
            "date": "2026-04-28T14:30:00.000000Z",
            "content": "hi",
        }
        argv = build_command(defn, msg)
        assert argv == ["x", "[2026-04-28T14:30:00.000000Z] GERRY: hi"]

    def test_age_substitution_relative_to_now(self, monkeypatch):
        from datetime import timedelta
        import definitions as dmod
        frozen = datetime(2026, 4, 28, 14, 35, 0, tzinfo=timezone.utc)
        msg_date = (frozen - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen
        monkeypatch.setattr(dmod, "datetime", FakeDT)

        defn = {"invoke": ["x", "($AGE) $MESSAGE"]}
        msg = {"from": "G", "to": "C", "date": msg_date, "content": "hi"}
        argv = build_command(defn, msg)
        assert argv == ["x", "(5 minutes ago) hi"]

    def test_missing_date_yields_empty_age_and_timestamp(self):
        defn = {"invoke": ["x", "TS:$TIMESTAMP", "AGE:$AGE", "$MESSAGE"]}
        msg = {"from": "G", "to": "C", "content": "hi"}
        argv = build_command(defn, msg)
        assert argv == ["x", "TS:", "AGE:", "hi"]


class TestExpandArgv:
    def test_no_placeholders(self):
        assert _expand_argv(["claude", "-p", "literal"], "S", "R", "M") == [
            "claude", "-p", "literal",
        ]

    def test_message_substitution(self):
        assert _expand_argv(["x", "$MESSAGE", "y"], "", "", "hello") == ["x", "hello", "y"]

    def test_sender_recipient_message_in_one_arg(self):
        assert _expand_argv(["$SENDER->$RECIPIENT: $MESSAGE"], "A", "B", "hi") == ["A->B: hi"]

    def test_timestamp_and_age(self):
        argv = _expand_argv(
            ["[$TIMESTAMP][$AGE] $MESSAGE"],
            "A", "B", "hi",
            timestamp="2026-04-28T14:30:00Z",
            age="5 minutes ago",
        )
        assert argv == ["[2026-04-28T14:30:00Z][5 minutes ago] hi"]

    def test_a8s_dir_substitution(self):
        from core import SCRIPT_DIR
        assert _expand_argv(["$A8S_DIR/x"], "", "", "") == [f"{SCRIPT_DIR}/x"]


# ---------- _autodiscover_definition ----------

class TestAutodiscoverDefinition:
    def test_single_marker_uses_matching_builtin(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# X\n")
        path, note = _autodiscover_definition(tmp_path)
        assert path == str(default_definition_path("claude"))
        assert "auto-detected via CLAUDE.md" in note

    def test_no_marker_uses_default(self, tmp_path):
        path, note = _autodiscover_definition(tmp_path)
        assert path == str(default_definition_path("default"))
        assert "no marker file" in note

    def test_multiple_markers_uses_default(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# X\n")
        (tmp_path / "GEMINI.md").write_text("# Y\n")
        path, note = _autodiscover_definition(tmp_path)
        assert path == str(default_definition_path("default"))
        assert "multiple markers" in note
        assert "CLAUDE.md" in note and "GEMINI.md" in note

    def test_codex_marker(self, tmp_path):
        (tmp_path / "CODEX.md").write_text("# C\n")
        path, note = _autodiscover_definition(tmp_path)
        assert path == str(default_definition_path("codex"))

    def test_copilot_marker(self, tmp_path):
        # Copilot's marker is its native repo-instructions location, not an
        # invented `COPILOT.md` — see core.MARKER_FILES for rationale.
        gh = tmp_path / ".github"
        gh.mkdir()
        (gh / "copilot-instructions.md").write_text("# CP\n")
        path, note = _autodiscover_definition(tmp_path)
        assert path == str(default_definition_path("copilot"))
        assert "auto-detected via .github/copilot-instructions.md" in note


# ---------- load_definition ----------

class TestLoadDefinition:
    def test_loads_explicit_definition(self, fake_home, tmp_path, monkeypatch):
        defn_path = tmp_path / "custom.json"
        defn_path.write_text('{"invoke": ["echo", "$MESSAGE"]}')

        import registry
        registry.save_registry({"X": {"root": str(tmp_path), "definition": str(defn_path)}})

        loaded = load_definition("X")
        assert loaded == {"invoke": ["echo", "$MESSAGE"]}

    def test_falls_back_to_default(self, fake_home):
        # Agent registered with NO definition field — load_definition falls
        # back to the bundled default.json.
        import registry
        registry.save_registry({"X": {"root": "/tmp"}})

        loaded = load_definition("X")
        assert "invoke" in loaded

    def test_missing_file_raises(self, fake_home):
        import registry
        registry.save_registry({"X": {"root": "/tmp", "definition": "/nonexistent.json"}})
        with pytest.raises(FileNotFoundError):
            load_definition("X")


# ---------- build_idle_command + idle_timeout_seconds ----------

class TestBuildIdleCommand:
    def test_returns_none_when_no_idle(self):
        from definitions import build_idle_command
        assert build_idle_command({"invoke": ["x"]}, "neil") is None

    def test_returns_none_when_idle_invoke_missing(self):
        from definitions import build_idle_command
        assert build_idle_command({"invoke": ["x"], "idle": {"timeout": 60}}, "neil") is None

    def test_expands_recipient_to_agent_name(self):
        from definitions import build_idle_command
        defn = {"invoke": ["x"], "idle": {"timeout": 60, "invoke": ["claude", "$RECIPIENT idle"]}}
        assert build_idle_command(defn, "neil") == ["claude", "neil idle"]

    def test_message_fields_are_empty(self):
        # Idle has no incoming message — sender/message/timestamp/age all blank.
        from definitions import build_idle_command
        defn = {"invoke": ["x"], "idle": {"timeout": 60, "invoke": [
            "echo", "S=$SENDER", "M=$MESSAGE", "T=$TIMESTAMP", "A=$AGE",
        ]}}
        assert build_idle_command(defn, "neil") == [
            "echo", "S=", "M=", "T=", "A=",
        ]

    def test_a8s_dir_substitution_works(self):
        from core import SCRIPT_DIR
        from definitions import build_idle_command
        defn = {"invoke": ["x"], "idle": {"timeout": 60, "invoke": ["$A8S_DIR/check"]}}
        assert build_idle_command(defn, "neil") == [f"{SCRIPT_DIR}/check"]


class TestIdleTimeoutSeconds:
    def test_none_when_no_idle(self):
        from definitions import idle_timeout_seconds
        assert idle_timeout_seconds({"invoke": ["x"]}) is None

    def test_returns_float_when_set(self):
        from definitions import idle_timeout_seconds
        assert idle_timeout_seconds({"idle": {"timeout": 60}}) == 60.0

    def test_string_numeric_parses(self):
        from definitions import idle_timeout_seconds
        assert idle_timeout_seconds({"idle": {"timeout": "120"}}) == 120.0

    def test_zero_or_negative_returns_none(self):
        # Treat 0 and negative as "disabled" — caller skips if None.
        from definitions import idle_timeout_seconds
        assert idle_timeout_seconds({"idle": {"timeout": 0}}) is None
        assert idle_timeout_seconds({"idle": {"timeout": -10}}) is None

    def test_garbage_returns_none(self):
        from definitions import idle_timeout_seconds
        assert idle_timeout_seconds({"idle": {"timeout": "soon"}}) is None
        assert idle_timeout_seconds({"idle": {"timeout": None}}) is None
