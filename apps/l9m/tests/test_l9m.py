"""Unit tests for l9m — model resolution, prompt assembly, caching, arg parsing.

These tests do NOT require a running ollama instance. LLM-dependent behavior
is isolated behind monkeypatches.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make l9m importable
_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR))

import l9m


# ---------- _version_key ----------

class TestVersionKey:
    def test_simple_model_names(self):
        assert l9m._version_key("qwen3:0.6b") < l9m._version_key("qwen3:1.7b")

    def test_larger_param_wins(self):
        assert l9m._version_key("qwen3:1.7b") < l9m._version_key("qwen3:7b")

    def test_newer_version_wins(self):
        assert l9m._version_key("qwen2.5:7b") < l9m._version_key("qwen3:7b")

    def test_same_model_equal(self):
        assert l9m._version_key("qwen3:0.6b") == l9m._version_key("qwen3:0.6b")

    def test_no_numbers_returns_zeros(self):
        result = l9m._version_key("llama")
        assert result == (0,)  # only the trailing size regex (no match -> 0)

    def test_sorting_picks_best(self):
        models = ["qwen3:0.6b", "qwen2.5:7b", "qwen3:1.7b", "qwen3:7b"]
        best = sorted(models, key=l9m._version_key)[-1]
        assert best == "qwen3:7b"


# ---------- cache ----------

class TestCache:
    def test_write_read_roundtrip(self, tmp_path, monkeypatch):
        cache_file = tmp_path / ".cache" / "l9m.env"
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)

        l9m._write_cache("qwen3:7b")
        assert l9m._read_cache() == "qwen3:7b"

    def test_read_missing_file(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "nonexistent" / "l9m.env"
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m._read_cache() is None

    def test_read_ignores_other_lines(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("# comment\nFOO=bar\nMODEL=mymodel\nEXTRA=stuff\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m._read_cache() == "mymodel"

    def test_write_creates_parent_dirs(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "deep" / "nested" / "l9m.env"
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        l9m._write_cache("test-model")
        assert cache_file.exists()
        assert "MODEL=test-model" in cache_file.read_text()


# ---------- resolve_model ----------

class TestResolveModel:
    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("MODEL", "custom-model")
        assert l9m.resolve_model() == "custom-model"

    def test_env_var_stripped(self, monkeypatch):
        monkeypatch.setenv("MODEL", "  spaced-model  ")
        assert l9m.resolve_model() == "spaced-model"

    def test_cache_used_when_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MODEL", raising=False)
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=cached-model\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m.resolve_model() == "cached-model"

    def test_empty_env_falls_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MODEL", "   ")
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=cached-model\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m.resolve_model() == "cached-model"


# ---------- assemble_prompt ----------

class TestAssemblePrompt:
    def test_plain_prompt(self):
        result = l9m.assemble_prompt("hello", "", "", "")
        assert result == "hello"

    def test_context_wraps_prompt(self):
        result = l9m.assemble_prompt("question", "", "", "<Memories>\nstuff\n</Memories>")
        assert "question" in result
        assert "<Memories>" in result

    def test_bash_type_framing(self):
        result = l9m.assemble_prompt("list files", "bash", "show contents", "")
        assert "ONLY with the bash command" in result
        assert "INSTRUCTION:" in result
        assert "list files" in result
        assert "show contents" in result

    def test_bool_type_framing(self):
        result = l9m.assemble_prompt("is sky blue", "bool", "answer this", "")
        assert "YES or NO" in result
        assert "is sky blue" in result

    def test_list_type_framing(self):
        result = l9m.assemble_prompt("colors", "list", "enumerate", "")
        assert "one per line" in result
        assert "colors" in result

    def test_invalid_type_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            l9m.assemble_prompt("x", "invalid", "y", "")
        assert exc_info.value.code == 2

    def test_context_without_type(self):
        result = l9m.assemble_prompt("question", "", "", "<Memories>\ndata\n</Memories>")
        # Without type+instruction, prompt+context+prompt pattern
        assert result.startswith("question")
        assert result.endswith("question")
        assert "<Memories>" in result

    def test_type_without_instruction_still_plain(self):
        # If instruction is empty, type alone doesn't trigger framing
        result = l9m.assemble_prompt("hello", "bash", "", "")
        assert result == "hello"

    def test_instruction_without_type_still_plain(self):
        result = l9m.assemble_prompt("hello", "", "do stuff", "")
        assert result == "hello"


# ---------- main (argument parsing) ----------

class TestMain:
    def test_help_returns_zero(self, capsys):
        assert l9m.main(["--help"]) == 0
        out = capsys.readouterr().out
        assert "l9m" in out

    def test_empty_argv_shows_help(self, capsys):
        assert l9m.main([]) == 0
        out = capsys.readouterr().out
        assert "usage:" in out

    def test_model_flag_prints_model(self, monkeypatch, capsys):
        monkeypatch.setenv("MODEL", "test-model-xyz")
        assert l9m.main(["--model"]) == 0
        out = capsys.readouterr().out.strip()
        assert out == "test-model-xyz"

    def test_context_file_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m, "generate", lambda m, p, silent=False: "")
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        result = l9m.main(["-c", str(tmp_path / "nope.txt"), "-p", "hi"])
        assert result == 2

    def test_prompt_flag(self, monkeypatch):
        monkeypatch.setenv("MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        captured = {}

        def mock_generate(model, prompt, silent=False):
            captured["prompt"] = prompt
            return ""

        monkeypatch.setattr(l9m, "generate", mock_generate)
        # Need stdin to be a tty (or at least not provide content)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-p", "what is 2+2"])
        assert "what is 2+2" in captured["prompt"]

    def test_type_and_instruction_reach_prompt(self, monkeypatch):
        monkeypatch.setenv("MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        captured = {}

        def mock_generate(model, prompt, silent=False):
            captured["prompt"] = prompt
            return ""

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-t", "bash", "-i", "use zsh", "-p", "find big files"])
        assert "ONLY with the bash command" in captured["prompt"]
        assert "use zsh" in captured["prompt"]
        assert "find big files" in captured["prompt"]

    def test_echo_flag_prints_prompt(self, monkeypatch, capsys):
        monkeypatch.setenv("MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m, "generate", lambda m, p, silent=False: "")
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-e", "-p", "test prompt"])
        out = capsys.readouterr().out
        assert "test prompt" in out

    def test_positional_prompt(self, monkeypatch):
        monkeypatch.setenv("MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        captured = {}

        def mock_generate(model, prompt, silent=False):
            captured["prompt"] = prompt
            return ""

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["hello world"])
        assert captured["prompt"] == "hello world"


# ---------- _installed_qwen_models filtering ----------

class TestInstalledQwenModels:
    def test_filters_only_qwen(self, monkeypatch):
        """Mock the HTTP call to return mixed models, verify only qwen kept."""
        import json

        fake_data = json.dumps({"models": [
            {"name": "qwen3:0.6b"},
            {"name": "llama3:8b"},
            {"name": "qwen2.5:7b"},
            {"name": "mistral:7b"},
        ]}).encode()

        class FakeResp:
            def read(self):
                return fake_data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        monkeypatch.setattr(l9m.urllib.request, "urlopen", lambda *a, **kw: FakeResp())
        result = l9m._installed_qwen_models()
        assert result == ["qwen3:0.6b", "qwen2.5:7b"]

    def test_empty_on_network_error(self, monkeypatch):
        def boom(*a, **kw):
            raise ConnectionError("no ollama")
        monkeypatch.setattr(l9m.urllib.request, "urlopen", boom)
        assert l9m._installed_qwen_models() == []
