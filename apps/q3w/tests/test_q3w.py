"""Tests for q3w — NLP to bash command generation and execution."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_DIR = Path(__file__).resolve().parent.parent
_L9M_DIR = _PKG_DIR.parent / "l9m"
sys.path.insert(0, str(_PKG_DIR))
sys.path.insert(0, str(_L9M_DIR))

import l9m
import q3w


class TestQ3w:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        ctx_dir = tmp_path / "l9m"
        ctx_dir.mkdir()
        monkeypatch.setattr(l9m, "CONTEXT_DIR", ctx_dir)
        monkeypatch.setattr(l9m, "CONTEXT_FILE", ctx_dir / "context.txt")
        monkeypatch.setattr(l9m, "resolve_context_limit", lambda m: 10000)
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        self.ctx_file = ctx_dir / "context.txt"

    def test_help_returns_zero(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["q3w", "--help"])
        assert q3w.main() == 0
        assert "NLP to bash" in capsys.readouterr().out

    def test_no_words_returns_one(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["q3w", "-n"])
        assert q3w.main() == 1

    def test_dry_run_prints_command(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["q3w", "-n", "list", "files"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "ls -la")
        assert q3w.main() == 0
        assert "ls -la" in capsys.readouterr().out

    def test_dry_run_stores_context(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["q3w", "-n", "list", "files"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "ls -la")
        q3w.main()
        content = self.ctx_file.read_text()
        assert ">>> list files" in content
        assert "ls -la" in content

    def test_execution_stores_stdout_in_context(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["q3w", "say", "hello"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "echo hello")
        monkeypatch.setattr(q3w, "_looks_dangerous", lambda cmd, model: False)
        q3w.main()
        content = self.ctx_file.read_text()
        assert "STDOUT: hello" in content

    def test_execution_stores_stderr_in_context(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["q3w", "warn"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "echo oops >&2")
        monkeypatch.setattr(q3w, "_looks_dangerous", lambda cmd, model: False)
        q3w.main()
        content = self.ctx_file.read_text()
        assert "STDERR: oops" in content

    def test_reads_rolling_context(self, monkeypatch):
        l9m.append_context("previous", "prev_cmd")
        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["prompt"] = prompt
            return "echo hi"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.argv", ["q3w", "-n", "next"])
        q3w.main()
        assert "previous" in captured["prompt"]

    def test_empty_output_returns_one(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["q3w", "do", "stuff"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "   ")
        assert q3w.main() == 1
        assert "empty output" in capsys.readouterr().err

    def test_invalid_syntax_returns_two(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["q3w", "bad"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "if then fi done")
        monkeypatch.setattr(q3w, "_looks_dangerous", lambda cmd, model: False)
        assert q3w.main() == 2

    def test_invalid_syntax_no_context_stored(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["q3w", "bad"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "if then fi done")
        monkeypatch.setattr(q3w, "_looks_dangerous", lambda cmd, model: False)
        q3w.main()
        assert not self.ctx_file.exists()

    def test_force_skips_danger_prompt(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["q3w", "-f", "delete"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "echo safe")
        monkeypatch.setattr(q3w, "_looks_dangerous", lambda cmd, model: True)
        assert q3w.main() == 0

    def test_safety_check_fails_closed(self, monkeypatch):
        """If LLM is unreachable during safety check, treat as dangerous."""
        monkeypatch.setattr("sys.argv", ["q3w", "delete"])
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "rm -rf /")

        call_count = [0]
        def generate_that_fails_on_safety(model, prompt, stream=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return "rm -rf /"
            raise l9m.L9mError("unreachable")

        monkeypatch.setattr(l9m, "generate", generate_that_fails_on_safety)
        monkeypatch.setattr("builtins.input", lambda p: "n")
        assert q3w.main() == 130

    def test_output_capped_at_20_lines(self, monkeypatch):
        long_output = "\n".join(f"line{i}" for i in range(50))
        monkeypatch.setattr("sys.argv", ["q3w", "many", "lines"])
        monkeypatch.setattr(l9m, "generate",
                            lambda m, p, stream=None: f"printf '{long_output}'")
        monkeypatch.setattr(q3w, "_looks_dangerous", lambda cmd, model: False)
        q3w.main()
        content = self.ctx_file.read_text()
        stdout_lines = [l for l in content.splitlines() if l.startswith("STDOUT:")]
        assert len(stdout_lines) <= 20
        assert "truncated" in content
