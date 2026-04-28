"""Tests for definitions.py — verb selection, argv interpolation,
and auto-discovery."""
from __future__ import annotations

from pathlib import Path

import pytest

from definitions import (
    _autodiscover_definition,
    _expand_argv,
    _message_body,
    build_command,
    default_definition_path,
    load_definition,
    select_verb,
)


# ---------- select_verb ----------

class TestSelectVerb:
    def test_clear_takes_precedence(self):
        # Even with a sender + alias-style `to`, clear:true wins.
        msg = {"from": "GERRY", "to": "devs", "clear": True}
        assert select_verb(msg) == "clear"

    def test_senderless_is_prompt(self):
        msg = {"from": "", "to": "CLAUDE", "content": "hi"}
        assert select_verb(msg) == "prompt"

    def test_missing_from_is_prompt(self):
        msg = {"to": "CLAUDE", "content": "hi"}
        assert select_verb(msg) == "prompt"

    def test_with_sender_is_message(self):
        msg = {"from": "GERRY", "to": "CLAUDE", "content": "hi"}
        assert select_verb(msg) == "message"

    def test_alias_routed_is_still_message(self):
        # Strict opacity: alias-routed messages dispatch via the same verb
        # as direct ones — the only difference is what `to` resolves to.
        msg = {"from": "GERRY", "to": "devs", "content": "hi"}
        assert select_verb(msg) == "message"


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
    def test_dispatches_to_invokePrompt(self):
        defn = {"invokePrompt": ["claude", "-p", "$MESSAGE"]}
        msg = {"from": "", "to": "CLAUDE", "content": "hello"}
        argv = build_command(defn, msg, "prompt")
        assert argv == ["claude", "-p", "hello"]

    def test_dispatches_to_invokeMessage(self):
        defn = {"invokeMessage": ["claude", "--continue", "-p", "$SENDER tells $RECIPIENT: $MESSAGE"]}
        msg = {"from": "GERRY", "to": "CLAUDE", "content": "fix this"}
        argv = build_command(defn, msg, "message")
        assert argv == ["claude", "--continue", "-p", "GERRY tells CLAUDE: fix this"]

    def test_alias_routed_message_keeps_alias_in_recipient(self):
        # Strict opacity / mailing-list semantics: when the sender wrote
        # `to: devs`, the recipient's $RECIPIENT resolves to "devs" — they
        # know it came via the list, but not who else got it.
        defn = {"invokeMessage": ["claude", "-p", "$SENDER tells $RECIPIENT: $MESSAGE"]}
        msg = {"from": "GERRY", "to": "devs", "content": "standup"}
        argv = build_command(defn, msg, "message")
        assert argv == ["claude", "-p", "GERRY tells devs: standup"]

    def test_dispatches_to_invokeClear(self):
        defn = {"invokeClear": ["claude", "-p", "/clear"]}
        argv = build_command(defn, {"clear": True}, "clear")
        assert argv == ["claude", "-p", "/clear"]

    def test_clear_ignores_message_fields(self):
        # invokeClear shouldn't leak prior content into argv even if the
        # sentinel msg happens to carry stale fields.
        defn = {"invokeClear": ["x", "$SENDER", "$RECIPIENT", "$MESSAGE"]}
        msg = {"from": "GERRY", "to": "CLAUDE", "content": "stale", "clear": True}
        argv = build_command(defn, msg, "clear")
        assert argv == ["x", "", "", ""]

    def test_unknown_verb_raises(self):
        with pytest.raises(ValueError, match="unknown verb"):
            build_command({}, {}, "bogus")

    def test_missing_invoke_raises(self):
        with pytest.raises(ValueError, match="invokeMessage"):
            build_command({"invokePrompt": ["x"]}, {"from": "G", "to": "C"}, "message")

    def test_a8s_dir_substitution(self):
        from core import SCRIPT_DIR
        defn = {"invokePrompt": ["$A8S_DIR/dummy-cli", "$MESSAGE"]}
        msg = {"from": "", "to": "X", "content": "hi"}
        argv = build_command(defn, msg, "prompt")
        assert argv == [f"{SCRIPT_DIR}/dummy-cli", "hi"]

    def test_does_not_mutate_original_argv(self):
        defn = {"invokePrompt": ["claude", "-p", "$MESSAGE"]}
        original = list(defn["invokePrompt"])
        build_command(defn, {"from": "", "content": "hello"}, "prompt")
        assert defn["invokePrompt"] == original

    def test_message_body_includes_files(self):
        defn = {"invokeMessage": ["x", "$MESSAGE"]}
        msg = {
            "from": "GERRY",
            "to": "CLAUDE",
            "content": "review",
            "files": [{"path": "/tmp/x"}],
        }
        argv = build_command(defn, msg, "message")
        assert argv == ["x", "review\n\nFILE: /tmp/x"]


class TestExpandArgv:
    def test_no_placeholders(self):
        assert _expand_argv(["claude", "-p", "literal"], "S", "R", "M") == [
            "claude", "-p", "literal",
        ]

    def test_message_substitution(self):
        assert _expand_argv(["x", "$MESSAGE", "y"], "", "", "hello") == ["x", "hello", "y"]

    def test_sender_recipient_message_in_one_arg(self):
        assert _expand_argv(["$SENDER->$RECIPIENT: $MESSAGE"], "A", "B", "hi") == ["A->B: hi"]

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


# ---------- load_definition ----------

class TestLoadDefinition:
    def test_loads_explicit_definition(self, fake_home, tmp_path, monkeypatch):
        defn_path = tmp_path / "custom.json"
        defn_path.write_text('{"invokePrompt": ["echo", "$MESSAGE"]}')

        import registry
        registry.save_registry({"X": {"root": str(tmp_path), "definition": str(defn_path)}})

        loaded = load_definition("X")
        assert loaded == {"invokePrompt": ["echo", "$MESSAGE"]}

    def test_falls_back_to_default(self, fake_home):
        # Agent registered with NO definition field — load_definition falls
        # back to the bundled default.json.
        import registry
        registry.save_registry({"X": {"root": "/tmp"}})

        loaded = load_definition("X")
        assert "invokePrompt" in loaded
        assert "invokeClear" in loaded

    def test_missing_file_raises(self, fake_home):
        import registry
        registry.save_registry({"X": {"root": "/tmp", "definition": "/nonexistent.json"}})
        with pytest.raises(FileNotFoundError):
            load_definition("X")
