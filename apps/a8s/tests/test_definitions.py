"""Tests for definitions.py — verb selection, prompt formatting, argv expansion,
and auto-discovery."""
from __future__ import annotations

from pathlib import Path

import pytest

from definitions import (
    _autodiscover_definition,
    _expand_argv,
    build_command,
    build_prompt,
    default_definition_path,
    load_definition,
    select_verb,
)


# ---------- select_verb ----------

class TestSelectVerb:
    def test_clear_takes_precedence(self):
        # Even with a sender + alias, clear:true wins.
        msg = {"from": "GERRY", "to": "CLAUDE", "alias": "devs", "clear": True}
        assert select_verb(msg) == "clear"

    def test_senderless_is_prompt(self):
        msg = {"from": "", "to": "CLAUDE", "content": "hi"}
        assert select_verb(msg) == "prompt"

    def test_missing_from_is_prompt(self):
        msg = {"to": "CLAUDE", "content": "hi"}
        assert select_verb(msg) == "prompt"

    def test_with_alias_is_messageAlias(self):
        msg = {"from": "GERRY", "to": "CLAUDE", "alias": "devs", "content": "hi"}
        assert select_verb(msg) == "messageAlias"

    def test_with_sender_no_alias_is_message(self):
        msg = {"from": "GERRY", "to": "CLAUDE", "content": "hi"}
        assert select_verb(msg) == "message"

    def test_empty_alias_string_is_message(self):
        msg = {"from": "GERRY", "to": "CLAUDE", "alias": "", "content": "hi"}
        assert select_verb(msg) == "message"


# ---------- build_prompt ----------

DEFINITION = {
    "promptMessage": "{sender} tells you ({recipient}): {message}",
    "promptMessageAlias": "{sender} tells you ({recipient}) and {others_count} others on the {alias} alias: {message}",
}


class TestBuildPrompt:
    def test_clear_returns_empty(self):
        assert build_prompt({}, DEFINITION, "clear") == ""

    def test_prompt_is_raw_content(self):
        msg = {"from": "", "to": "CLAUDE", "content": "show capabilities"}
        assert build_prompt(msg, DEFINITION, "prompt") == "show capabilities"

    def test_prompt_appends_file_lines(self):
        msg = {
            "from": "",
            "content": "see attached",
            "files": [{"path": "/tmp/build.log"}, {"path": "/tmp/data.csv"}],
        }
        out = build_prompt(msg, DEFINITION, "prompt")
        assert out == "see attached\n\nFILE: /tmp/build.log\nFILE: /tmp/data.csv"

    def test_message_uses_promptMessage(self):
        msg = {"from": "GERRY", "to": "CLAUDE", "content": "fix this"}
        assert build_prompt(msg, DEFINITION, "message") == "GERRY tells you (CLAUDE): fix this"

    def test_messageAlias_uses_promptMessageAlias(self):
        msg = {
            "from": "GERRY",
            "to": "CLAUDE",
            "content": "standup",
            "alias": "devs",
            "others_count": 1,
        }
        out = build_prompt(msg, DEFINITION, "messageAlias")
        assert out == "GERRY tells you (CLAUDE) and 1 others on the devs alias: standup"

    def test_message_with_date_prefix(self):
        msg = {"from": "GERRY", "to": "CLAUDE", "content": "hi", "date": "2026-04-27T15:30:00Z"}
        out = build_prompt(msg, DEFINITION, "message")
        # Template doesn't include {date}, so it's prefixed.
        assert out == "[2026-04-27T15:30:00Z] GERRY tells you (CLAUDE): hi"

    def test_template_with_date_placeholder_no_prefix(self):
        defn = {"promptMessage": "[{date}] {sender}: {message}"}
        msg = {"from": "GERRY", "to": "CLAUDE", "content": "hi", "date": "X"}
        out = build_prompt(msg, defn, "message")
        # Prefix is suppressed because the template already includes {date}.
        assert out == "[X] GERRY: hi"

    def test_message_with_files(self):
        msg = {
            "from": "GERRY",
            "to": "CLAUDE",
            "content": "review",
            "files": [{"path": "/x"}],
        }
        out = build_prompt(msg, DEFINITION, "message")
        assert out == "GERRY tells you (CLAUDE): review\n\nFILE: /x"


# ---------- build_command + _expand_argv ----------

class TestBuildCommand:
    def test_dispatches_to_invokePrompt(self):
        defn = {"invokePrompt": ["claude", "-p", "$PROMPT"]}
        argv = build_command(defn, "hello", "prompt")
        assert argv == ["claude", "-p", "hello"]

    def test_dispatches_to_invokeMessage(self):
        defn = {"invokeMessage": ["claude", "--continue", "-p", "$PROMPT"]}
        argv = build_command(defn, "x", "message")
        assert argv == ["claude", "--continue", "-p", "x"]

    def test_dispatches_to_invokeMessageAlias(self):
        defn = {"invokeMessageAlias": ["claude", "-p", "$PROMPT"]}
        argv = build_command(defn, "y", "messageAlias")
        assert argv == ["claude", "-p", "y"]

    def test_dispatches_to_invokeClear(self):
        defn = {"invokeClear": ["claude", "-p", "/clear"]}
        argv = build_command(defn, "", "clear")
        assert argv == ["claude", "-p", "/clear"]

    def test_unknown_verb_raises(self):
        with pytest.raises(ValueError, match="unknown verb"):
            build_command({}, "x", "bogus")

    def test_missing_invoke_raises(self):
        with pytest.raises(ValueError, match="invokeMessage"):
            build_command({"invokePrompt": ["x"]}, "p", "message")

    def test_a8s_dir_substitution(self):
        from core import SCRIPT_DIR
        defn = {"invokePrompt": ["$A8S_DIR/dummy-cli", "$PROMPT"]}
        argv = build_command(defn, "hi", "prompt")
        assert argv == [f"{SCRIPT_DIR}/dummy-cli", "hi"]

    def test_does_not_mutate_original_argv(self):
        defn = {"invokePrompt": ["claude", "-p", "$PROMPT"]}
        original = list(defn["invokePrompt"])
        build_command(defn, "hello", "prompt")
        assert defn["invokePrompt"] == original


class TestExpandArgv:
    def test_no_placeholders(self):
        assert _expand_argv(["claude", "-p", "literal"], "ignored") == [
            "claude",
            "-p",
            "literal",
        ]

    def test_prompt_substitution(self):
        assert _expand_argv(["x", "$PROMPT", "y"], "hello") == ["x", "hello", "y"]

    def test_prompt_in_concatenated_arg(self):
        assert _expand_argv(["wrap=$PROMPT-end"], "X") == ["wrap=X-end"]

    def test_a8s_dir_substitution(self):
        from core import SCRIPT_DIR
        assert _expand_argv(["$A8S_DIR/x"], "") == [f"{SCRIPT_DIR}/x"]


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


# ---------- load_definition ----------

class TestLoadDefinition:
    def test_loads_explicit_definition(self, fake_home, tmp_path, monkeypatch):
        # Write a custom definition.
        defn_path = tmp_path / "custom.json"
        defn_path.write_text('{"invokePrompt": ["echo", "$PROMPT"]}')

        # Add an agent that points at it.
        import registry
        registry.save_registry({"X": {"root": str(tmp_path), "definition": str(defn_path)}})

        loaded = load_definition("X")
        assert loaded == {"invokePrompt": ["echo", "$PROMPT"]}

    def test_falls_back_to_default(self, fake_home):
        # Agent registered with NO definition field — load_definition falls
        # back to the bundled default.json.
        import registry
        registry.save_registry({"X": {"root": "/tmp"}})

        loaded = load_definition("X")
        # default.json has all four invoke verbs.
        assert "invokePrompt" in loaded
        assert "invokeClear" in loaded

    def test_missing_file_raises(self, fake_home):
        import registry
        registry.save_registry({"X": {"root": "/tmp", "definition": "/nonexistent.json"}})
        with pytest.raises(FileNotFoundError):
            load_definition("X")
