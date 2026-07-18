"""Unit tests for l9m — model resolution, prompt assembly, caching, arg parsing.

These tests do NOT require a running ollama instance. LLM-dependent behavior
is isolated behind monkeypatches.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make l9m importable
_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR))

import l9m
import glow_stream


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

        l9m._write_cache("qwen3:7b", 32768)
        cache = l9m._read_cache()
        assert cache["MODEL"] == "qwen3:7b"
        assert cache["NUM_CTX"] == "32768"

    def test_read_missing_file(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "nonexistent" / "l9m.env"
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m._read_cache() == {}

    def test_read_parses_all_keys(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=mymodel\nNUM_CTX=4096\nEXTRA=stuff\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        cache = l9m._read_cache()
        assert cache["MODEL"] == "mymodel"
        assert cache["NUM_CTX"] == "4096"

    def test_write_creates_parent_dirs(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "deep" / "nested" / "l9m.env"
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        l9m._write_cache("test-model")
        assert cache_file.exists()
        assert "MODEL=test-model" in cache_file.read_text()


# ---------- resolve_model ----------

class TestResolveModel:
    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "custom-model")
        assert l9m.resolve_model() == "custom-model"

    def test_env_var_stripped(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "  spaced-model  ")
        assert l9m.resolve_model() == "spaced-model"

    def test_cache_used_when_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("L9M_MODEL", raising=False)
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=cached-model\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m.resolve_model() == "cached-model"

    def test_empty_env_falls_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "   ")
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=cached-model\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m.resolve_model() == "cached-model"

    def test_legacy_model_env_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MODEL", "legacy-model")
        monkeypatch.delenv("L9M_MODEL", raising=False)
        cache_file = tmp_path / "l9m.env"
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m, "_installed_qwen_models", lambda: ["qwen3:7b"])
        monkeypatch.setattr(l9m, "_model_num_ctx", lambda m: 32768)
        monkeypatch.setattr(l9m, "_write_cache", lambda *a, **k: None)
        assert l9m.resolve_model() == "qwen3:7b"


# ---------- _model_num_ctx ----------

class TestModelNumCtx:
    def test_extracts_context_length(self, monkeypatch):
        import json
        fake_resp = json.dumps({
            "model_info": {"qwen2.context_length": 32768}
        }).encode()

        class FakeResp:
            def read(self): return fake_resp
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(l9m.urllib.request, "urlopen", lambda *a, **kw: FakeResp())
        assert l9m._model_num_ctx("qwen3:7b") == 32768

    def test_returns_none_on_missing_key(self, monkeypatch):
        import json
        fake_resp = json.dumps({"model_info": {"other_key": 999}}).encode()

        class FakeResp:
            def read(self): return fake_resp
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(l9m.urllib.request, "urlopen", lambda *a, **kw: FakeResp())
        assert l9m._model_num_ctx("qwen3:7b") is None

    def test_returns_none_on_network_error(self, monkeypatch):
        def boom(*a, **kw):
            raise OSError("timeout")
        monkeypatch.setattr(l9m.urllib.request, "urlopen", boom)
        assert l9m._model_num_ctx("qwen3:7b") is None

    def test_returns_none_on_empty_response(self, monkeypatch):
        import json
        fake_resp = json.dumps({}).encode()

        class FakeResp:
            def read(self): return fake_resp
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(l9m.urllib.request, "urlopen", lambda *a, **kw: FakeResp())
        assert l9m._model_num_ctx("qwen3:7b") is None


# ---------- resolve_context_limit ----------

class TestResolveContextLimit:
    def test_env_override(self, monkeypatch):
        monkeypatch.setattr(l9m, "CONTEXT_LIMIT_OVERRIDE", "5000")
        assert l9m.resolve_context_limit("any-model") == 5000

    def test_invalid_env_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.setattr(l9m, "CONTEXT_LIMIT_OVERRIDE", "bad")
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=test\nNUM_CTX=8192\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m.resolve_context_limit("test") == int(8192 * 0.25 * 3)

    def test_cached_num_ctx(self, monkeypatch, tmp_path):
        monkeypatch.setattr(l9m, "CONTEXT_LIMIT_OVERRIDE", "")
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=qwen3:7b\nNUM_CTX=32768\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        expected = int(32768 * 0.25 * 3)
        assert l9m.resolve_context_limit("qwen3:7b") == expected

    def test_model_mismatch_queries_ollama(self, monkeypatch, tmp_path):
        monkeypatch.setattr(l9m, "CONTEXT_LIMIT_OVERRIDE", "")
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=old-model\nNUM_CTX=4096\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        monkeypatch.setattr(l9m, "_model_num_ctx", lambda m: 16384)
        expected = int(16384 * 0.25 * 3)
        assert l9m.resolve_context_limit("new-model") == expected

    def test_fallback_when_no_num_ctx(self, monkeypatch, tmp_path):
        monkeypatch.setattr(l9m, "CONTEXT_LIMIT_OVERRIDE", "")
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=test\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        monkeypatch.setattr(l9m, "_model_num_ctx", lambda m: None)
        assert l9m.resolve_context_limit("test") == 10000

    def test_context_size_flag(self, monkeypatch, capsys):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "resolve_context_limit", lambda m: 24576)
        assert l9m.main(["--context-size"]) == 0
        out = capsys.readouterr().out.strip()
        assert out == "24576"

    def test_clear_flag_removes_context(self, tmp_path, monkeypatch):
        ctx_dir = tmp_path / "l9m"
        ctx_dir.mkdir()
        ctx_file = ctx_dir / "context.txt"
        ctx_file.write_text(">>> old\nstuff\n")
        monkeypatch.setattr(l9m, "CONTEXT_FILE", ctx_file)
        assert l9m.main(["--clear"]) == 0
        assert not ctx_file.exists()

    def test_clear_flag_no_error_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(l9m, "CONTEXT_FILE", tmp_path / "nope.txt")
        assert l9m.main(["--clear"]) == 0


class TestResolveNumCtx:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("L9M_NUM_CTX", "8192")
        assert l9m.resolve_num_ctx("any") == 8192

    def test_invalid_env_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv("L9M_NUM_CTX", "nope")
        cache_file = tmp_path / "l9m.env"
        cache_file.write_text("MODEL=test\nNUM_CTX=4096\n")
        monkeypatch.setattr(l9m, "CACHE_FILE", cache_file)
        assert l9m.resolve_num_ctx("test") == 4096

    def test_generate_options_includes_num_ctx(self, monkeypatch):
        monkeypatch.setenv("L9M_NUM_CTX", "16384")
        assert l9m._generate_options("m") == {"num_predict": -1, "num_ctx": 16384}

    def test_generate_options_omits_num_ctx_without_env(self, monkeypatch):
        monkeypatch.delenv("L9M_NUM_CTX", raising=False)
        assert l9m._generate_options("m") == {"num_predict": -1}

    def test_context_limit_uses_num_ctx_env(self, monkeypatch):
        monkeypatch.setattr(l9m, "CONTEXT_LIMIT_OVERRIDE", "")
        monkeypatch.setenv("L9M_NUM_CTX", "8192")
        expected = int(8192 * 0.25 * 3)
        assert l9m.resolve_context_limit("any") == expected

    def test_generate_sends_num_ctx(self, monkeypatch):
        monkeypatch.setenv("L9M_NUM_CTX", "8192")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        captured = {}

        def fake_urlopen(req, timeout=300):
            captured["body"] = json.loads(req.data)
            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def __iter__(self):
                    yield json.dumps({"response": "ok", "done": True}).encode() + b"\n"
            return Resp()

        monkeypatch.setattr(l9m.urllib.request, "urlopen", fake_urlopen)
        l9m.generate("fake", "hi", stream=None)
        assert captured["body"]["options"]["num_ctx"] == 8192


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

    def test_type_without_instruction_uses_default_framing(self):
        result = l9m.assemble_prompt("hello", "bash", "", "")
        assert "Answer ONLY with the bash command" in result
        assert "Answer:" in result

    def test_instruction_without_type_uses_instruction(self):
        result = l9m.assemble_prompt("hello", "", "do stuff", "")
        assert "INSTRUCTION: do stuff:" in result
        assert "<Prompt>hello</Prompt>" in result


# ---------- main (argument parsing) ----------

class TestMain:
    @pytest.fixture(autouse=True)
    def _isolate_context(self, tmp_path, monkeypatch):
        ctx_dir = tmp_path / "l9m"
        monkeypatch.setattr(l9m, "CONTEXT_DIR", ctx_dir)
        monkeypatch.setattr(l9m, "CONTEXT_FILE", ctx_dir / "context.txt")
        monkeypatch.setattr(l9m, "resolve_context_limit", lambda m: 10000)

    def test_help_returns_zero(self, capsys):
        assert l9m.main(["--help"]) == 0
        out = capsys.readouterr().out
        assert "l9m" in out

    def test_empty_argv_shows_help(self, capsys):
        assert l9m.main([]) == 0
        out = capsys.readouterr().out
        assert "usage:" in out

    def test_model_flag_prints_model(self, monkeypatch, capsys):
        monkeypatch.setenv("L9M_MODEL", "test-model-xyz")
        assert l9m.main(["--model"]) == 0
        out = capsys.readouterr().out.strip()
        assert out == "test-model-xyz"

    def test_context_file_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "")
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        result = l9m.main(["-c", str(tmp_path / "nope.txt"), "-p", "hi"])
        assert result == 2

    def test_prompt_flag(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["prompt"] = prompt
            return ""

        monkeypatch.setattr(l9m, "generate", mock_generate)
        # Need stdin to be a tty (or at least not provide content)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-p", "what is 2+2"])
        assert "what is 2+2" in captured["prompt"]

    def test_type_and_instruction_reach_prompt(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["prompt"] = prompt
            return ""

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-t", "bash", "-i", "use zsh", "-p", "find big files"])
        assert "ONLY with the bash command" in captured["prompt"]
        assert "use zsh" in captured["prompt"]
        assert "find big files" in captured["prompt"]

    def test_echo_flag_prints_prompt(self, monkeypatch, capsys):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "")
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-e", "-p", "test prompt"])
        out = capsys.readouterr().out
        assert "test prompt" in out

    def test_positional_prompt(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["prompt"] = prompt
            return ""

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["hello world"])
        assert captured["prompt"] == "hello world"


# ---------- safe markdown flush / glow streaming ----------

class TestSafeMarkdownFlushEnd:
    def test_empty(self):
        assert l9m.safe_markdown_flush_end("") == 0

    def test_paragraph_boundary(self):
        text = "hello\n\nworld"
        assert l9m.safe_markdown_flush_end(text) == 7

    def test_waits_inside_code_fence(self):
        text = "intro\n\n```python\nprint('hi')"
        assert l9m.safe_markdown_flush_end(text) == 7
        assert l9m.safe_markdown_flush_end(text[7:]) == 0

    def test_flushes_closed_code_fence(self):
        text = "intro\n\n```python\nprint('hi')\n```\n\n"
        assert l9m.safe_markdown_flush_end(text) == len(text)

    def test_multiple_paragraphs(self):
        text = "a\n\nb\n\nc"
        assert l9m.safe_markdown_flush_end(text) == 6


class TestResolveGlowStyle:
    def test_explicit_theme(self):
        assert l9m.resolve_glow_style("dracula") == "dracula"

    def test_auto_uses_clitheme(self, monkeypatch):
        monkeypatch.setenv("CLITHEME", "light")
        monkeypatch.setattr(glow_stream, "_query_terminal_background_luminance", lambda: None)
        assert l9m.resolve_glow_style("auto") == "light"

    def test_auto_uses_osc11_dark(self, monkeypatch):
        monkeypatch.delenv("CLITHEME", raising=False)
        monkeypatch.setattr(glow_stream, "_query_terminal_background_luminance", lambda: 10000)
        assert l9m.resolve_glow_style("auto") == "dark"

    def test_auto_uses_osc11_light(self, monkeypatch):
        monkeypatch.delenv("CLITHEME", raising=False)
        monkeypatch.setattr(glow_stream, "_query_terminal_background_luminance", lambda: 50000)
        assert l9m.resolve_glow_style("auto") == "light"

    def test_auto_falls_back_to_colorfgbg(self, monkeypatch):
        monkeypatch.delenv("CLITHEME", raising=False)
        monkeypatch.setattr(glow_stream, "_query_terminal_background_luminance", lambda: None)
        monkeypatch.setenv("COLORFGBG", "15;0")
        assert l9m.resolve_glow_style("auto") == "dark"

    def test_parse_osc11_rgb(self):
        reply = b"\x1b]11;rgb:1e1e/1e1e/2e2e\x07"
        assert glow_stream._parse_osc11_rgb(reply) == (0x1e1e, 0x1e1e, 0x2e2e)


class TestGlowFlag:
    @pytest.fixture(autouse=True)
    def _isolate_context(self, tmp_path, monkeypatch):
        ctx_dir = tmp_path / "l9m"
        monkeypatch.setattr(l9m, "CONTEXT_DIR", ctx_dir)
        monkeypatch.setattr(l9m, "CONTEXT_FILE", ctx_dir / "context.txt")
        monkeypatch.setattr(l9m, "resolve_context_limit", lambda m: 10000)

    def test_glow_missing_errors(self, monkeypatch, capsys):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "")
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        monkeypatch.setattr(l9m.shutil, "which", lambda name: None if name == "glow" else "/usr/bin/false")
        result = l9m.main(["--glow", "auto", "-p", "hi"])
        assert result == 1
        assert "glow not found" in capsys.readouterr().err

    def test_glow_theme_from_flag(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m.shutil, "which", lambda name: "/opt/homebrew/bin/glow")
        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["stream"] = stream
            return "ok"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["--glow", "dracula", "-p", "hi"])
        assert isinstance(captured["stream"], l9m.GlowStream)
        assert captured["stream"]._style == "dracula"

    def test_glow_theme_from_env(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setenv("L9M_GLOW", "tokyo-night")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m.shutil, "which", lambda name: "/opt/homebrew/bin/glow")
        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["stream"] = stream
            return "ok"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-p", "hi"])
        assert isinstance(captured["stream"], l9m.GlowStream)
        assert captured["stream"]._style == "tokyo-night"

    def test_flag_overrides_env_glow_theme(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setenv("L9M_GLOW", "dark")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m.shutil, "which", lambda name: "/opt/homebrew/bin/glow")
        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["stream"] = stream
            return "ok"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["--glow", "light", "-p", "hi"])
        assert captured["stream"]._style == "light"

    def test_glow_uses_explicit_style(self, monkeypatch):
        import subprocess
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            return subprocess.CompletedProcess(cmd, 0, stdout="\x1b[1mbold\x1b[0m\n", stderr="")

        monkeypatch.setattr(l9m.subprocess, "run", fake_run)
        gs = l9m.GlowStream(None, "/usr/bin/glow", "dracula")
        gs.write("**bold**")
        gs.finalize()
        assert captured["cmd"] == ["/usr/bin/glow", "--style", "dracula", "-"]
        assert captured["env"].get("CLICOLOR_FORCE") == "1"

    def test_flush_does_not_render_partial(self, monkeypatch):
        import subprocess
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs["input"])
            return subprocess.CompletedProcess(cmd, 0, stdout=f"G:{kwargs['input']}\n", stderr="")

        monkeypatch.setattr(l9m.subprocess, "run", fake_run)
        gs = l9m.GlowStream(None, "/usr/bin/glow")
        gs.write("hello")
        gs.write(" world")
        assert calls == []
        gs.flush()
        assert calls == []
        gs.finalize()
        assert calls == ["hello world"]

    def test_glow_skipped_for_structured_type(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m.shutil, "which", lambda name: "/opt/homebrew/bin/glow")
        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["stream"] = stream
            return "ls -la"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["--glow", "auto", "-t", "bash", "-p", "list files"])
        assert captured["stream"] is l9m.sys.stdout


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


# ---------- rolling context ----------

class TestRollingContext:
    @pytest.fixture(autouse=True)
    def _isolate_context(self, tmp_path, monkeypatch):
        ctx_dir = tmp_path / "l9m"
        ctx_dir.mkdir()
        monkeypatch.setattr(l9m, "CONTEXT_DIR", ctx_dir)
        monkeypatch.setattr(l9m, "CONTEXT_FILE", ctx_dir / "context.txt")
        monkeypatch.setattr(l9m, "resolve_context_limit", lambda m: 10000)
        self.ctx_dir = ctx_dir
        self.ctx_file = ctx_dir / "context.txt"

    def test_read_empty_when_no_file(self):
        assert l9m.read_context() == ""

    def test_append_creates_file(self):
        l9m.append_context("hello", "world")
        assert self.ctx_file.exists()
        content = self.ctx_file.read_text()
        assert ">>> hello" in content
        assert "world" in content

    def test_append_accumulates(self):
        l9m.append_context("q1", "a1")
        l9m.append_context("q2", "a2")
        content = self.ctx_file.read_text()
        assert ">>> q1" in content
        assert ">>> q2" in content

    def test_rolling_window_trims(self):
        l9m.append_context("first question", "first answer", limit=50)
        l9m.append_context("second question", "second answer", limit=50)
        content = self.ctx_file.read_text()
        assert len(content) <= 50
        assert ">>> second question" in content
        assert ">>> first question" not in content

    def test_trim_at_line_boundary(self):
        l9m.append_context("aaa", "bbb", limit=30)
        l9m.append_context("ccc", "ddd", limit=30)
        content = self.ctx_file.read_text()
        assert content.startswith(">>>") or content == ""

    def test_no_trim_at_exact_limit(self):
        entry = ">>> X\nY\n"
        l9m.append_context("X", "Y", limit=len(entry))
        content = self.ctx_file.read_text()
        assert content == entry

    def test_extremely_small_limit(self):
        l9m.append_context("hello", "world", limit=1)
        content = self.ctx_file.read_text()
        assert isinstance(content, str)

    def test_empty_prompt_and_response(self):
        l9m.append_context("", "")
        content = self.ctx_file.read_text()
        assert ">>> \n\n" in content

    def test_multiline_content(self):
        l9m.append_context("line1\nline2", "resp\nwith\nnewlines")
        content = self.ctx_file.read_text()
        assert ">>> line1\nline2" in content
        assert "resp\nwith\nnewlines" in content

    def test_unicode_roundtrip(self):
        l9m.append_context("café \U0001f680", "你好世界")
        content = self.ctx_file.read_text()
        assert "\U0001f680" in content
        assert "你好" in content

    def test_read_context_unreadable_file(self, monkeypatch):
        def raise_perm(*a, **kw):
            raise PermissionError("denied")
        self.ctx_file.write_text("data")
        monkeypatch.setattr(l9m.CONTEXT_FILE.__class__, "read_text", raise_perm)
        assert l9m.read_context() == ""

    def test_stdin_not_stored_in_context(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "resp")
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {
            "isatty": lambda self: False,
            "read": lambda self: "big piped document content",
        })())
        l9m.main([])
        assert not self.ctx_file.exists()

    def test_context_injected_into_prompt(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        # Pre-populate context
        l9m.append_context("earlier question", "earlier answer")

        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["prompt"] = prompt
            return "response"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-p", "new question"])
        assert "earlier question" in captured["prompt"]
        assert "earlier answer" in captured["prompt"]

    def test_blank_context_skips_rolling(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        l9m.append_context("earlier question", "earlier answer")

        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["prompt"] = prompt
            return "response"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        assert l9m.main(["-c", "", "-p", "new question"]) == 0
        assert "earlier question" not in captured["prompt"]
        assert ">>> new question" not in self.ctx_file.read_text()

    def test_context_file_overrides_rolling(self, tmp_path, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        # Pre-populate rolling context
        l9m.append_context("rolling stuff", "rolling response")

        ctx = tmp_path / "explicit.txt"
        ctx.write_text("explicit context here")

        captured = {}

        def mock_generate(model, prompt, stream=None):
            captured["prompt"] = prompt
            return "response"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-c", str(ctx), "-p", "new question"])
        assert "explicit context here" in captured["prompt"]
        assert "rolling stuff" not in captured["prompt"]

    def test_response_appended_after_generation(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)

        def mock_generate(model, prompt, stream=None):
            return "generated response"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-p", "my question"])
        content = self.ctx_file.read_text()
        assert ">>> my question" in content
        assert "generated response" in content

    def test_no_append_when_context_file_used(self, tmp_path, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)

        ctx = tmp_path / "explicit.txt"
        ctx.write_text("stuff")

        def mock_generate(model, prompt, stream=None):
            return "response"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())
        l9m.main(["-c", str(ctx), "-p", "q"])
        assert not self.ctx_file.exists()


# ---------- context compaction ----------

class TestCompaction:
    @pytest.fixture(autouse=True)
    def _isolate_context(self, tmp_path, monkeypatch):
        ctx_dir = tmp_path / "l9m"
        ctx_dir.mkdir()
        monkeypatch.setattr(l9m, "CONTEXT_DIR", ctx_dir)
        monkeypatch.setattr(l9m, "CONTEXT_FILE", ctx_dir / "context.txt")
        self.ctx_file = ctx_dir / "context.txt"

    def test_trim_context(self):
        text = "line0\nline1\nline2"
        trimmed = l9m._trim_context(text, 8)
        assert len(trimmed) <= 8
        assert trimmed in text

    def test_should_compact_at_threshold(self):
        self.ctx_file.write_text("x" * 79)
        assert not l9m.should_compact(100)
        self.ctx_file.write_text("x" * 80)
        assert l9m.should_compact(100)

    def test_should_compact_force(self):
        assert not l9m.should_compact(100, force=True)
        self.ctx_file.write_text("data")
        assert l9m.should_compact(100, force=True)

    def test_compact_context_replaces_log(self, monkeypatch):
        self.ctx_file.write_text(">>> old\nresponse\n")
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "- fact one\n- fact two")
        assert l9m.compact_context("fake", 10000)
        content = self.ctx_file.read_text()
        assert content.startswith("[compacted ")
        assert "fact one" in content
        assert ">>> old" not in content

    def test_compact_context_empty(self):
        assert not l9m.compact_context("fake", 100)

    def test_compact_context_llm_failure(self, monkeypatch):
        self.ctx_file.write_text(">>> old\nresponse\n")

        def boom(*a, **kw):
            raise l9m.L9mError("fail")

        monkeypatch.setattr(l9m, "generate", boom)
        assert not l9m.compact_context("fake", 100)

    def test_append_triggers_compact_with_model(self, monkeypatch):
        self.ctx_file.write_text("x" * 70)
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "compressed memory")
        l9m.append_context("q", "a" * 15, limit=100, model="fake")
        content = self.ctx_file.read_text()
        assert "[compacted " in content
        assert "compressed memory" in content

    def test_append_without_model_truncates_only(self):
        l9m.append_context("first question", "first answer", limit=50)
        l9m.append_context("second question", "second answer", limit=50)
        content = self.ctx_file.read_text()
        assert len(content) <= 50
        assert "[compacted " not in content

    def test_maybe_compact_force(self, monkeypatch):
        self.ctx_file.write_text(">>> q\na\n")
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "summary")
        assert l9m.maybe_compact("fake", 10000, force=True, silent=True)
        assert "[compacted " in self.ctx_file.read_text()

    def test_maybe_compact_failure_truncates(self, monkeypatch):
        self.ctx_file.write_text("x" * 200)

        def boom(*a, **kw):
            raise l9m.L9mError("fail")

        monkeypatch.setattr(l9m, "generate", boom)
        assert not l9m.maybe_compact("fake", 100, force=True, silent=True)
        assert len(self.ctx_file.read_text()) <= 100

    def test_main_compact_flag(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        self.ctx_file.write_text(">>> q\na\n")
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "summary")
        assert l9m.main(["--compact"]) == 0
        assert "[compacted " in self.ctx_file.read_text()

    def test_main_compact_empty_ok(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        assert l9m.main(["--compact"]) == 0


# ---------- chat mode ----------

class TestChat:
    @pytest.fixture(autouse=True)
    def _isolate_context(self, tmp_path, monkeypatch):
        ctx_dir = tmp_path / "l9m"
        ctx_dir.mkdir()
        monkeypatch.setattr(l9m, "CONTEXT_DIR", ctx_dir)
        monkeypatch.setattr(l9m, "CONTEXT_FILE", ctx_dir / "context.txt")
        monkeypatch.setattr(l9m, "resolve_context_limit", lambda m: 10000)
        self.ctx_dir = ctx_dir
        self.ctx_file = ctx_dir / "context.txt"

    @pytest.fixture(autouse=True)
    def _isolate_model(self, monkeypatch):
        monkeypatch.setenv("L9M_MODEL", "fake")
        monkeypatch.setattr(l9m, "_ollama_running", lambda: True)
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: True, "read": lambda self: ""})())

    def test_one_prompt_then_exit(self, monkeypatch):
        inputs = iter(["hello", "exit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        captured = []

        def mock_generate(model, prompt, stream=None):
            captured.append(prompt)
            return "hi there"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        result = l9m.main(["--chat"])
        assert result == 0
        assert len(captured) == 1
        assert "hello" in captured[0]

    def test_multiple_turns_accumulate_context(self, monkeypatch):
        inputs = iter(["first question", "second question", "quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        captured = []

        def mock_generate(model, prompt, stream=None):
            captured.append(prompt)
            if len(captured) == 1:
                return "first answer"
            return "second answer"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        l9m.main(["--chat"])
        assert len(captured) == 2
        assert "first question" in captured[1]
        assert "first answer" in captured[1]

    def test_quit_terminates(self, monkeypatch):
        inputs = iter(["quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "x")
        result = l9m.main(["--chat"])
        assert result == 0

    def test_exit_terminates(self, monkeypatch):
        inputs = iter(["exit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "x")
        result = l9m.main(["--chat"])
        assert result == 0

    def test_compact_command(self, monkeypatch):
        self.ctx_file.write_text(">>> old\nresponse\n")
        inputs = iter(["/compact", "quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

        def mock_generate(model, prompt, stream=None):
            if l9m.COMPACT_PROMPT in prompt:
                return "summary"
            return "hi"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        l9m.main(["--chat"])
        assert "[compacted " in self.ctx_file.read_text()

    def test_eof_terminates_cleanly(self, monkeypatch):
        def raise_eof(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "x")
        result = l9m.main(["--chat"])
        assert result == 0

    def test_empty_lines_skipped(self, monkeypatch):
        inputs = iter(["", "  ", "actual prompt", "quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        captured = []

        def mock_generate(model, prompt, stream=None):
            captured.append(prompt)
            return "response"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        l9m.main(["--chat"])
        assert len(captured) == 1
        assert "actual prompt" in captured[0]

    def test_type_flag_respected(self, monkeypatch):
        inputs = iter(["list files", "exit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        captured = []

        def mock_generate(model, prompt, stream=None):
            captured.append(prompt)
            return "ls -la"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        l9m.main(["--chat", "-t", "bash"])
        assert len(captured) == 1
        assert "ONLY with the bash command" in captured[0]

    def test_instruction_flag_respected(self, monkeypatch):
        inputs = iter(["do something", "exit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        captured = []

        def mock_generate(model, prompt, stream=None):
            captured.append(prompt)
            return "done"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        l9m.main(["--chat", "-i", "be concise"])
        assert len(captured) == 1
        assert "be concise" in captured[0]

    def test_keyboard_interrupt_on_input_exits(self, monkeypatch):
        call_count = [0]

        def interrupt_input(prompt=""):
            call_count[0] += 1
            if call_count[0] == 1:
                raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", interrupt_input)
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "resp")
        result = l9m.main(["--chat"])
        assert result == 0

    def test_keyboard_interrupt_during_generate_continues(self, monkeypatch):
        inputs = iter(["hello", "world", "quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        call_count = [0]

        def mock_generate(model, prompt, stream=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise KeyboardInterrupt
            return "response"

        monkeypatch.setattr(l9m, "generate", mock_generate)
        result = l9m.main(["--chat"])
        assert result == 0
        assert call_count[0] == 2

    def test_context_persists_to_file(self, monkeypatch):
        inputs = iter(["remember this", "quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
        monkeypatch.setattr(l9m, "generate", lambda m, p, stream=None: "noted")
        l9m.main(["--chat"])
        content = self.ctx_file.read_text()
        assert ">>> remember this" in content
        assert "noted" in content
