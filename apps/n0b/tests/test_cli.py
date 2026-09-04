"""n0b CLI tests."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

N0B_PY = Path(__file__).resolve().parents[1] / "n0b.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commands.ai_common import merged_hints, parse_replacement, read_pair_file, save_hints
from commands.ai_image_cmd import (
    IMAGE_FAMILIES,
    N0B_ROOT,
    _IMAGE_SNIPPET,
    build_image_argv,
    cmd_image,
    read_image_prompt,
    resolve_image_model,
    resolve_image_ref,
)
from commands.image_gguf import (
    GGUF_TEXT_BACKBONE,
    parse_hub_file,
    text_config_from_gguf,
)
from commands.ai_speak_cmd import (
    apply_pronunciations,
    apply_speak_replacements,
    cmd_speak,
    load_speak_text,
    resolve_speak_engine,
    resolve_speak_voice,
    save_sticky_voice,
    speakable,
)
from commands.ai_transcribe_cmd import (
    apply_replacements,
    calc_fps,
    cmd_transcribe,
    format_timed_speech,
    loop_run,
    parse_whisper_timed_stdout,
    read_replacements,
    resolve_engine_model,
    resolve_flavor,
    resolve_transcribe_engine,
    save_replacements,
    HINTS_FILE,
    REPLACEMENTS_FILE,
    DEFAULT_PARAKEET_MODEL,
    _FASTER_WHISPER_SNIPPET,
    _FASTER_WHISPER_TIMED_SNIPPET,
    _WHISPER_SNIPPET,
    _WHISPER_TIMED_SNIPPET,
)
from commands.ai_ollama import (
    OllamaError,
    ensure_vision_model,
    resolve_vision_model,
)
from commands.ai_audio_cmd import cmd_audio  # noqa: E402
from commands.ai_video_cmd import cmd_video, parse_video_args  # noqa: E402
from commands.secrets_cmd import cmd_set, resolve  # noqa: E402
from commands.video_cmd import cmd_gif, cmd_last_frame, resolve_gif_settings  # noqa: E402
from cli import parse_audio_argv, parse_image_argv  # noqa: E402


def run_n0b(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(N0B_PY), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_help():
    proc = run_n0b("--help")
    assert proc.returncode == 0
    assert "json" in proc.stdout
    assert "ai" in proc.stdout


def test_json_pretty_print():
    proc = subprocess.run(
        [sys.executable, str(N0B_PY), "json"],
        input='{"b":1,"a":2}',
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert parsed == {"b": 1, "a": 2}


def test_ports_free():
    proc = run_n0b("ports", "free")
    assert proc.returncode == 0
    port = int(proc.stdout.strip())
    assert 1 <= port <= 65535


def test_secrets_from_env(monkeypatch):
    monkeypatch.setenv("N0B_TEST_SECRET", "hello")
    proc = run_n0b("secrets", "get", "N0B_TEST_SECRET")
    assert proc.returncode == 0
    assert proc.stdout == "hello"


def test_secrets_missing():
    proc = run_n0b("secrets", "get", "N0B_NONEXISTENT_SECRET_XYZ")
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_secrets_set_and_resolve(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("N0B_SET_SECRET", raising=False)
    assert cmd_set("N0B_SET_SECRET", "s3cret") == 0
    path = tmp_path / "lib" / "n0b-set-secret.txt"
    assert path.read_text() == "s3cret\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert resolve("N0B_SET_SECRET") == "s3cret"


def test_secrets_set_dir(tmp_path):
    assert cmd_set("MY_KEY", "v", base_dir=str(tmp_path)) == 0
    assert (tmp_path / "my-key.txt").read_text() == "v\n"


def test_secrets_set_stdin(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(N0B_PY), "secrets", "set", "PIPED_KEY", "--dir", str(tmp_path)],
        input="fromstdin\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert (tmp_path / "piped-key.txt").read_text() == "fromstdin\n"


def test_secrets_set_empty_value_rejected(tmp_path):
    assert cmd_set("MY_KEY", "  ", base_dir=str(tmp_path)) == 1
    assert not (tmp_path / "my-key.txt").exists()


def test_secrets_set_env_file_upsert(tmp_path):
    env_file = tmp_path / "some.env"
    env_file.write_text("OTHER=1\nMY_KEY=old\n")
    assert cmd_set("MY_KEY", "new", env_file=str(env_file)) == 0
    assert env_file.read_text() == "OTHER=1\nMY_KEY=new\n"


def test_secrets_set_keychain_invokes_security():
    with patch("commands.secrets_cmd.subprocess.run") as run, \
            patch("commands.secrets_cmd.sys.platform", "darwin"):
        run.return_value.returncode = 0
        assert cmd_set("KC_KEY", "v", keychain=True) == 0
        argv = run.call_args[0][0]
        assert argv[:2] == ["security", "add-generic-password"]
        assert "KC_KEY" in argv and "v" in argv


def test_secrets_get_keychain_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KC_ONLY_KEY", raising=False)
    with patch("commands.secrets_cmd.subprocess.run") as run, \
            patch("commands.secrets_cmd.sys.platform", "darwin"):
        run.return_value.returncode = 0
        run.return_value.stdout = "kcval\n"
        assert resolve("KC_ONLY_KEY") == "kcval"
        argv = run.call_args[0][0]
        assert argv[:2] == ["security", "find-generic-password"]


def test_secrets_set_where_flags_exclusive(tmp_path):
    proc = run_n0b("secrets", "set", "X", "v", "--keychain", "--env-file", "x.env")
    assert proc.returncode == 2


def test_ai_research_requires_prompt():
    proc = run_n0b("ai", "research")
    assert proc.returncode == 2
    assert "Usage: n0b ai research" in proc.stderr


def test_video_ltx2_parse_flag():
    req = parse_video_args(["--ltx2", "hello"])
    assert req.ltx_version == 2
    assert req.prompt == "hello"


def test_video_install_only():
    with patch("commands.ai_video_cmd.install_ltx2", return_value=0) as install:
        rc = cmd_video(None, ["--install"])
    assert rc == 0
    install.assert_called_once()


def test_audio_requires_prompt():
    rc = cmd_audio(None, [])
    assert rc == 2


def test_resolve_image_ref_warns_on_multiple(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(b"\x89PNG\r\n")
    b.write_bytes(b"\x89PNG\r\n")
    ref, note, err = resolve_image_ref([str(a), str(b)])
    assert err is None
    assert ref == str(a)
    assert note is not None and "first" in note


def test_build_image_argv_includes_ref_and_strength(tmp_path):
    ref = tmp_path / "photo.png"
    ref.write_bytes(b"\x89PNG\r\n")
    argv, note = build_image_argv(["oil painting"], [str(ref)], 0.35, "out.png")
    assert argv == ["--ref", str(ref), "--strength", "0.35", "-o", "out.png", "oil painting"]
    assert note is None


def test_cmd_image_forwards_ref(tmp_path):
    ref = tmp_path / "photo.png"
    ref.write_bytes(b"\x89PNG\r\n")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)
    with patch("commands.ai_image_cmd.ensure_image", return_value=fake_python), patch(
        "commands.ai_image_cmd.subprocess.run"
    ) as run:
        run.return_value.returncode = 0
        rc = cmd_image(None, ["make it painterly"], [str(ref)], 0.4, None)
    assert rc == 0
    argv = run.call_args[0][0]
    assert argv[0] == str(fake_python)
    assert argv[1] == "-c"
    assert argv[3] == "make it painterly"
    assert argv[5] == str(ref)
    assert argv[6] == "0.4"


def test_read_image_prompt_reads_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("a fox\nwith a hat"))
    assert read_image_prompt(["-", "high detail"]) == ["a fox\nwith a hat", "high detail"]


def test_cmd_image_reads_prompt_from_stdin(tmp_path, monkeypatch):
    fake_python = tmp_path / "venv" / "bin" / "python3"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)
    monkeypatch.setattr("sys.stdin", io.StringIO("a fox with a hat"))
    with patch("commands.ai_image_cmd.ensure_image", return_value=fake_python), patch(
        "commands.ai_image_cmd.subprocess.run"
    ) as run:
        run.return_value.returncode = 0
        assert cmd_image(None, ["-"], [], 0.6, None) == 0
    assert run.call_args[0][0][3] == "a fox with a hat"


def test_resolve_image_model_accepts_hub_gguf_url():
    url = (
        "https://huggingface.co/owner/qwen-gguf/"
        "blob/main/text-encoder.gguf"
    )
    assert resolve_image_model(url) == (url, None)


def test_resolve_image_model_accepts_nested_hub_gguf_url():
    url = (
        "https://huggingface.co/owner/repo/blob/main/"
        "encoders/text-encoder.gguf"
    )
    assert resolve_image_model(url) == (url, None)


def test_resolve_image_model_accepts_mistral3_family():
    assert resolve_image_model("mistral3") == ("mistral3", None)
    assert resolve_image_model("z-image") == (None, None)


def test_resolve_image_model_rejects_unknown_family():
    model, error = resolve_image_model("flux")
    assert model is None
    assert error is not None and "mistral3" in error


def test_resolve_image_model_rejects_non_gguf_hub_file():
    model, error = resolve_image_model("https://huggingface.co/org/model/blob/main/model.bin")
    assert model is None
    assert error is not None and ".gguf" in error


def test_cmd_image_forwards_hub_model(tmp_path):
    fake_python = tmp_path / "venv" / "bin" / "python3"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)
    model = "https://huggingface.co/org/model/blob/main/model.gguf"
    with patch("commands.ai_image_cmd.ensure_image", return_value=fake_python), patch(
        "commands.ai_image_cmd.subprocess.run"
    ) as run:
        run.return_value.returncode = 0
        assert cmd_image(model, ["a fox"], [], 0.6, None) == 0
    assert run.call_args[0][0][-1] == model


def test_cmd_image_forwards_mistral3_family(tmp_path):
    fake_python = tmp_path / "venv" / "bin" / "python3"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)
    with patch("commands.ai_image_cmd.ensure_image", return_value=fake_python), patch(
        "commands.ai_image_cmd.subprocess.run"
    ) as run:
        run.return_value.returncode = 0
        assert cmd_image("mistral3", ["a fox"], [], 0.6, None) == 0
    argv = run.call_args[0][0]
    assert argv[-1] == "mistral3"
    assert "ErnieImagePipeline" in argv[2]
    assert IMAGE_FAMILIES["mistral3"]["repo"] in argv[2]


def test_cmd_image_rejects_ref_for_mistral3(tmp_path):
    ref = tmp_path / "photo.png"
    ref.write_bytes(b"\x89PNG\r\n")
    assert cmd_image("mistral3", ["a fox"], [str(ref)], 0.4, None) == 2


def test_image_snippet_maps_mistral3_without_vendor_urls():
    assert "mistral3" in IMAGE_FAMILIES
    assert "ErnieImagePipeline" in _IMAGE_SNIPPET
    assert "load_gguf_text_encoder" in _IMAGE_SNIPPET
    assert str(N0B_ROOT) in _IMAGE_SNIPPET
    assert "ponpoke" not in _IMAGE_SNIPPET
    assert "BennyDaBall" not in _IMAGE_SNIPPET
    assert "abliterated" not in _IMAGE_SNIPPET.lower()


def test_parse_hub_file_keeps_nested_gguf_path():
    repo, revision, filename = parse_hub_file(
        "https://huggingface.co/owner/repo/blob/main/encoders/text-encoder.gguf"
    )
    assert repo == "owner/repo"
    assert revision == "main"
    assert filename == "encoders/text-encoder.gguf"


def test_text_config_from_gguf_is_mistral_text_not_vlm(monkeypatch):
    @dataclass
    class MistralConfig:
        model_type: str = "mistral"
        hidden_size: int = 0
        num_hidden_layers: int = 0
        intermediate_size: int = 0
        num_attention_heads: int = 0
        num_key_value_heads: int = 0
        vocab_size: int = 0
        rms_norm_eps: float = 0.0

    transformers = ModuleType("transformers")
    transformers.MistralConfig = MistralConfig
    transformers.Qwen3Config = MistralConfig
    monkeypatch.setitem(sys.modules, "transformers", transformers)

    config = text_config_from_gguf({
        "model_type": "mistral3",
        "hidden_size": 3072,
        "num_hidden_layers": 26,
        "intermediate_size": 9216,
        "num_attention_heads": 32,
        "num_key_value_heads": 8,
        "vocab_size": 131072,
        "rms_norm_eps": 1e-5,
        "file_type": 7,
    })
    assert type(config).__name__ == "MistralConfig"
    assert config.model_type == "mistral"
    assert config.hidden_size == 3072
    assert config.num_hidden_layers == 26
    assert not hasattr(config, "vision_config") or config.vision_config is None
    assert GGUF_TEXT_BACKBONE["mistral3"] == "mistral"


def _fake_gguf_transformers(monkeypatch, captured):
    transformers = ModuleType("transformers")
    gguf_utils = ModuleType("transformers.modeling_gguf_pytorch_utils")

    class AutoTokenizer:
        @classmethod
        def from_pretrained(cls, repo_id, **kwargs):
            captured["tokenizer_repo"] = repo_id
            captured["tokenizer_kwargs"] = kwargs
            return "tokenizer"

    class AutoModel:
        @classmethod
        def from_pretrained(cls, repo_id, **kwargs):
            captured["auto_model_repo"] = repo_id
            return "encoder"

    class MistralModel:
        @classmethod
        def from_pretrained(cls, repo_id, **kwargs):
            captured["repo"] = repo_id
            captured["config"] = kwargs["config"]
            captured["gguf_file"] = kwargs["gguf_file"]
            return "encoder"

    class Qwen3Model(AutoModel):
        pass

    transformers.AutoModel = AutoModel
    transformers.AutoTokenizer = AutoTokenizer
    transformers.MistralModel = MistralModel
    transformers.Qwen3Model = Qwen3Model
    gguf_utils.load_gguf_checkpoint = lambda path, **kwargs: {
        "config": {
            "model_type": "mistral3",
            "hidden_size": 3072,
            "num_hidden_layers": 26,
        }
    }
    gguf_utils.get_gguf_hf_weights_map = lambda *args, **kwargs: {}
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(
        sys.modules, "transformers.modeling_gguf_pytorch_utils", gguf_utils
    )


def test_load_gguf_text_encoder_uses_mistral_model(monkeypatch):
    from commands import image_gguf

    captured: dict = {}
    _fake_gguf_transformers(monkeypatch, captured)
    monkeypatch.setattr(image_gguf, "enable_gguf_arch_aliases", lambda: None)
    monkeypatch.setattr(image_gguf, "text_config_from_gguf", lambda parsed: parsed)

    encoder, tokenizer = image_gguf.load_gguf_text_encoder(
        "owner/repo",
        "encoders/te.gguf",
        "main",
        "baidu/ERNIE-Image-Turbo",
        "bf16",
        "/tmp/te.gguf",
        "mistral3",
    )
    assert encoder == "encoder"
    assert tokenizer == "tokenizer"
    assert captured["gguf_file"] == "encoders/te.gguf"
    assert captured["config"]["hidden_size"] == 3072
    assert type(captured["config"]).__name__ != "Mistral3Config"


def test_load_gguf_text_encoder_uses_pipeline_tokenizer(monkeypatch):
    from commands import image_gguf

    captured: dict = {}
    _fake_gguf_transformers(monkeypatch, captured)
    monkeypatch.setattr(image_gguf, "enable_gguf_arch_aliases", lambda: None)
    monkeypatch.setattr(image_gguf, "text_config_from_gguf", lambda parsed: parsed)
    monkeypatch.setattr(image_gguf, "_qwen_head_dim", lambda path: 32)

    encoder, tokenizer = image_gguf.load_gguf_text_encoder(
        "owner/quant",
        "encoder.gguf",
        "main",
        "Tongyi-MAI/Z-Image-Turbo",
        "bf16",
        "/tmp/encoder.gguf",
        "qwen3",
    )
    assert (encoder, tokenizer) == ("encoder", "tokenizer")
    assert captured["tokenizer_repo"] == "Tongyi-MAI/Z-Image-Turbo"
    assert captured["tokenizer_kwargs"] == {"subfolder": "tokenizer"}


def test_cmd_image_install_only(tmp_path):
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with patch("commands.ai_image_cmd.ensure_image", return_value=fake_python) as setup:
        rc = cmd_image(None, [], [], 0.6, None, install=True)
    assert rc == 0
    setup.assert_called_once()


def test_cmd_image_uninstall(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    with patch("venv_util.BIN_VENV", venv), patch("venv_util.LEGACY_VENVS", ()), patch(
        "venv_util.ZIMAGE_LEGACY_REPO", tmp_path / "legacy"
    ):
        rc = cmd_image(None, [], [], 0.6, None, uninstall=True)
    assert rc == 0
    assert not venv.exists()


def test_cmd_image_auto_setup_on_prompt(tmp_path):
    fake_python = tmp_path / "venv" / "bin" / "python3"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)
    with patch("commands.ai_image_cmd.ensure_image", return_value=fake_python) as setup, patch(
        "commands.ai_image_cmd.subprocess.run"
    ) as run:
        run.return_value.returncode = 0
        rc = cmd_image(None, ["a fox"], [], 0.6, None)
    assert rc == 0
    setup.assert_called_once()


def test_image_help_shows_ref():
    proc = run_n0b("ai", "image", "--help")
    assert proc.returncode == 0
    assert "--ref" in proc.stdout
    assert "--strength" in proc.stdout
    assert "--install" in proc.stdout
    assert "--uninstall" in proc.stdout


def test_parse_image_argv_flags_after_prompt(tmp_path):
    ref = tmp_path / "photo.png"
    a = parse_image_argv(
        ["oil painting", "--ref", str(ref), "--strength", "0.35", "-o", "out.png"]
    )
    assert a.prompt == ["oil painting"]
    assert a.ref == [str(ref)]
    assert a.strength == 0.35
    assert a.out == "out.png"


def test_parse_audio_argv_flags_after_prompt():
    a = parse_audio_argv(["rain on tin", "-o", "/tmp/out.wav"])
    assert a.prompt == ["rain on tin"]
    assert a.out == "/tmp/out.wav"


def test_parse_audio_argv_install():
    a = parse_audio_argv(["--install"])
    assert a.install is True
    assert a.prompt == []


def test_audio_install_flag(tmp_path):
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with patch("commands.ai_audio_cmd.ensure_audio", return_value=fake_python) as setup:
        rc = cmd_audio(None, ["--install"])
    assert rc == 0
    setup.assert_called_once_with("audioldm")


def test_cmd_audio_install_via_cli(tmp_path):
    fake_python = tmp_path / "venv" / "bin" / "python3"
    from cli import main

    with patch("commands.ai_audio_cmd.ensure_audio", return_value=fake_python) as setup:
        rc = main(["ai", "audio", "--install"])
    assert rc == 0
    setup.assert_called_once_with("audioldm")


def test_merged_hints_file_then_flags(tmp_path):
    hints_file = tmp_path / "transcribe-hints.txt"
    hints_file.write_text("# my glossary\nPay-i\n\nNeil Obremski\n")
    assert merged_hints(["a8s", " r4t "], hints_file) == "Pay-i, Neil Obremski, a8s, r4t"


def test_merged_hints_no_file(tmp_path):
    assert merged_hints(["only-flag"], tmp_path / "missing.txt") == "only-flag"
    assert merged_hints([], tmp_path / "missing.txt") == ""


def test_transcribe_missing_file():
    rc = cmd_transcribe("/nonexistent/audio.m4a", [], None, "turbo")
    assert rc == 1


def test_transcribe_no_audio_no_save():
    rc = cmd_transcribe(None, [], None, "turbo")
    assert rc == 2


def test_save_hints_appends_and_dedupes(tmp_path):
    hints_file = tmp_path / "cfg" / "transcribe-hints.txt"
    assert save_hints(["Pay-i", "a8s, r4t"], hints_file) == 0
    assert hints_file.read_text() == "Pay-i\na8s\nr4t\n"
    assert save_hints(["pay-i", "k7e"], hints_file) == 0
    assert hints_file.read_text() == "Pay-i\na8s\nr4t\nk7e\n"


def test_save_hints_requires_hints(tmp_path):
    assert save_hints([], tmp_path / "hints.txt") == 2


def test_save_hints_no_trailing_newline(tmp_path):
    hints_file = tmp_path / "hints.txt"
    hints_file.write_text("a8s")
    assert save_hints(["k7e"], hints_file) == 0
    assert hints_file.read_text() == "a8s\nk7e\n"


def test_transcribe_save_only(tmp_path):
    hints_file = tmp_path / "hints.txt"
    with patch("commands.ai_transcribe_cmd.HINTS_FILE", hints_file):
        rc = cmd_transcribe(None, ["Pay-i"], None, "turbo", save=True)
    assert rc == 0
    assert hints_file.read_text() == "Pay-i\n"


def test_read_replacements_skips_bad_lines(tmp_path, capsys):
    f = tmp_path / "transcribe-replacements.txt"
    f.write_text("# comment\nJerry => Gerry\nnodelimiter\n\\bAmber up\\b => AmperUp\n")
    pairs = read_replacements(f)
    assert pairs == [("Jerry", "Gerry"), ("\\bAmber up\\b", "AmperUp")]
    assert "nodelimiter" in capsys.readouterr().err


def test_apply_replacements_annotates_every_match():
    text, applied = apply_replacements(
        "Jerry said hi. Then Jerry left.", [("Jerry", "Gerry")]
    )
    assert text == (
        "Jerry (possible transcribe error, might be 'Gerry') said hi. "
        "Then Jerry (possible transcribe error, might be 'Gerry') left."
    )
    assert applied == ["Jerry => Gerry (x2)"]


def test_apply_replacements_regex_and_no_match():
    text, applied = apply_replacements(
        "amber up is live", [("[Aa]mber ?up", "AmperUp"), ("Jerry", "Gerry")]
    )
    assert "might be 'AmperUp'" in text
    assert applied == ["[Aa]mber ?up => AmperUp (x1)"]


def test_apply_replacements_bad_regex_skipped(capsys):
    text, applied = apply_replacements("hello", [("(unclosed", "x")])
    assert text == "hello"
    assert applied == []
    assert "bad replacement regex" in capsys.readouterr().err


def test_save_replacements_dedupes_by_pattern(tmp_path):
    f = tmp_path / "transcribe-replacements.txt"
    assert save_replacements(["Jerry => Gerry"], f) == 0
    assert save_replacements(["Jerry => Larry", "2020 => 2026"], f) == 0
    assert f.read_text() == "Jerry => Gerry\n2020 => 2026\n"


def test_transcribe_applies_replacements(tmp_path, capsys):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    repl = tmp_path / "transcribe-replacements.txt"
    repl.write_text("Jerry => Gerry\n")
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", repl),
        patch("commands.ai_transcribe_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "Jerry said hi.\n"
        rc = cmd_transcribe(
            str(audio), [], "en", "base", flavor="plain", engine="whisper"
        )
    assert rc == 0
    out, err = capsys.readouterr()
    assert out == "Jerry (possible transcribe error, might be 'Gerry') said hi.\n"
    assert "Jerry => Gerry (x1)" in err


def test_transcribe_invokes_whisper_venv(tmp_path):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "hello\n"
        rc = cmd_transcribe(
            str(audio), ["Pay-i"], "en", "base", flavor="plain", engine="whisper"
        )
        assert rc == 0
        argv = run.call_args[0][0]
        assert argv[0] == str(fake_python)
        assert argv[1] == "-c"
        assert argv[3:] == [str(audio), "base", "en", "Pay-i", "0"]


def test_transcribe_invokes_mlx_whisper_model_map(tmp_path):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "hello\n"
        rc = cmd_transcribe(
            str(audio), [], "en", "turbo", flavor="plain", engine="mlx-whisper"
        )
        assert rc == 0
        argv = run.call_args[0][0]
        assert argv[3:] == [
            str(audio),
            "mlx-community/whisper-large-v3-turbo",
            "en",
            "",
            "0",
        ]


def test_transcribe_invokes_faster_whisper_model_map(tmp_path):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "hello\n"
        rc = cmd_transcribe(
            str(audio), [], "en", "turbo", flavor="plain", engine="faster-whisper"
        )
        assert rc == 0
        argv = run.call_args[0][0]
        assert argv[2] == _FASTER_WHISPER_SNIPPET
        assert argv[3:] == [str(audio), "large-v3-turbo", "en", "", "0"]


def test_transcribe_invokes_parakeet(tmp_path, capsys):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "hello from parakeet\n"
        rc = cmd_transcribe(
            str(audio),
            ["Pay-i"],
            "en",
            "turbo",
            flavor="plain",
            engine="parakeet-mlx",
            condition_on_previous=True,
        )
        assert rc == 0
        argv = run.call_args[0][0]
        assert argv[3:] == [
            str(audio),
            DEFAULT_PARAKEET_MODEL,
            "0",
            "120.0",
            "15.0",
        ]
    _out, err = capsys.readouterr()
    assert "hints ignored for parakeet-mlx" in err
    assert "--language ignored for parakeet-mlx" in err
    assert "--condition-on-previous ignored for parakeet-mlx" in err


def test_transcribe_condition_on_previous_flag(tmp_path):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "hello\n"
        rc = cmd_transcribe(
            str(audio),
            [],
            "en",
            "base",
            flavor="plain",
            condition_on_previous=True,
            engine="whisper",
        )
        assert rc == 0
        argv = run.call_args[0][0]
        assert argv[3:] == [str(audio), "base", "en", "", "1"]


def test_whisper_snippets_pass_condition_flag():
    assert "condition_on_previous_text=condition == \"1\"" in _WHISPER_SNIPPET
    assert "condition_on_previous_text=condition == \"1\"" in _WHISPER_TIMED_SNIPPET


def test_faster_whisper_snippets_use_int8_and_condition_flag():
    for snippet in (_FASTER_WHISPER_SNIPPET, _FASTER_WHISPER_TIMED_SNIPPET):
        assert "from faster_whisper import WhisperModel" in snippet
        assert 'compute_type="int8"' in snippet
        assert "condition_on_previous_text=condition == \"1\"" in snippet


def test_loop_run_detects_repetition():
    looped = " ".join(["Thank you."] * 16)
    assert loop_run(looped) >= 4
    assert loop_run("So, let's go. So, let's go. So, let's go. So, let's go.") >= 4
    assert loop_run("Hello world. This is fine. Nothing repeats here.") < 4
    assert loop_run("") == 1


def test_resolve_transcribe_engine_auto():
    with patch("commands.ai_transcribe_cmd.mlx_available", return_value=True):
        assert resolve_transcribe_engine(None) == "mlx-whisper"
        assert resolve_transcribe_engine("auto") == "mlx-whisper"
    with patch("commands.ai_transcribe_cmd.mlx_available", return_value=False):
        assert resolve_transcribe_engine("auto") == "faster-whisper"
        assert resolve_transcribe_engine(None) == "faster-whisper"
    assert resolve_transcribe_engine("parakeet-mlx") == "parakeet-mlx"
    assert resolve_transcribe_engine("faster-whisper") == "faster-whisper"
    # explicit --engine whisper stays available (e.g. CUDA machines)
    assert resolve_transcribe_engine("whisper") == "whisper"


def test_resolve_engine_model_maps():
    assert resolve_engine_model("whisper", "turbo") == "turbo"
    assert (
        resolve_engine_model("mlx-whisper", "turbo")
        == "mlx-community/whisper-large-v3-turbo"
    )
    assert (
        resolve_engine_model("mlx-whisper", "mlx-community/whisper-tiny")
        == "mlx-community/whisper-tiny"
    )
    assert resolve_engine_model("parakeet-mlx", "turbo") == DEFAULT_PARAKEET_MODEL
    assert (
        resolve_engine_model("parakeet-mlx", "mlx-community/parakeet-tdt-1.1b")
        == "mlx-community/parakeet-tdt-1.1b"
    )
    with pytest.raises(ValueError, match="unknown mlx-whisper model"):
        resolve_engine_model("mlx-whisper", "base.en")


def test_resolve_engine_model_faster_whisper():
    assert resolve_engine_model("faster-whisper", "turbo") == "large-v3-turbo"
    assert resolve_engine_model("faster-whisper", "base") == "base"
    assert resolve_engine_model("faster-whisper", "large-v3") == "large-v3"
    assert (
        resolve_engine_model("faster-whisper", "org/custom-ct2-model")
        == "org/custom-ct2-model"
    )


def test_warn_if_loop_emits(capsys):
    from commands.ai_transcribe_cmd import warn_if_loop

    assert warn_if_loop("Hello. World.") == 1
    assert "possible repetition loop" not in capsys.readouterr().err
    looped = " ".join(["Thank you."] * 5)
    assert warn_if_loop(looped) >= 4
    assert "possible repetition loop" in capsys.readouterr().err


def test_transcribe_warns_on_loop(tmp_path, capsys):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    looped = " ".join(["Thank you."] * 8)
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = looped + "\n"
        rc = cmd_transcribe(
            str(audio), [], "en", "base", flavor="plain", engine="whisper"
        )
    assert rc == 0
    _out, err = capsys.readouterr()
    assert "possible repetition loop" in err


def test_transcribe_help():
    proc = run_n0b("ai", "transcribe", "--help")
    assert proc.returncode == 0
    assert "--hint" in proc.stdout
    assert "--language" in proc.stdout
    assert "--flavor" in proc.stdout
    assert "--vision-model" in proc.stdout
    assert "--condition-on-previous" in proc.stdout
    assert "--engine" in proc.stdout


def test_resolve_flavor_overrides():
    path = Path("/tmp/x")
    assert resolve_flavor("plain", path) == "plain"
    assert resolve_flavor("fancy", path) == "fancy"


def test_resolve_flavor_auto_uses_video_detect(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    with patch("commands.ai_transcribe_cmd.has_video_stream", return_value=True):
        assert resolve_flavor("auto", media) == "fancy"
    with patch("commands.ai_transcribe_cmd.has_video_stream", return_value=False):
        assert resolve_flavor("auto", media) == "plain"


def test_format_timed_speech():
    text = format_timed_speech(
        {
            "language": "en",
            "text": "Hello world",
            "segments": [
                {"start": 0.0, "end": 1.25, "text": "Hello"},
                {"start": 1.25, "end": 2.0, "text": "world"},
            ],
        }
    )
    assert "Language: en" in text
    assert "Full Speech Transcript: Hello world" in text
    assert "[Speech Transcript from 0.0 to 1.25 seconds]" in text
    assert "Hello" in text


def test_parse_whisper_timed_stdout_tolerates_detected_language():
    payload = {
        "language": "en",
        "text": "hello",
        "segments": [{"start": 0, "end": 1, "text": "hello"}],
    }
    contaminated = "Detected language: English\n" + json.dumps(payload) + "\n"
    assert parse_whisper_timed_stdout(contaminated) == payload
    assert parse_whisper_timed_stdout(json.dumps(payload)) == payload


def test_parse_whisper_timed_stdout_rejects_garbage():
    with pytest.raises(ValueError, match="not JSON"):
        parse_whisper_timed_stdout("Detected language: English\nhello\n")


def test_calc_fps_caps_and_spreads(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    with patch("commands.ai_transcribe_cmd.probe_duration_seconds", return_value=10.0):
        assert calc_fps(media) == 1.0
    with patch("commands.ai_transcribe_cmd.probe_duration_seconds", return_value=100.0):
        assert calc_fps(media) == 0.5
    with patch("commands.ai_transcribe_cmd.probe_duration_seconds", return_value=None):
        assert calc_fps(media) == 1.0


def test_transcribe_fancy_requires_video(tmp_path, capsys):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.has_video_stream", return_value=False),
    ):
        rc = cmd_transcribe(str(audio), [], None, "base", flavor="fancy")
    assert rc == 2
    assert "requires a video stream" in capsys.readouterr().err


def test_transcribe_fancy_vision_model_lacks_vision(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.has_video_stream", return_value=True),
        patch(
            "commands.ai_transcribe_cmd.ensure_vision_model",
            side_effect=OllamaError(
                "model 'qwen3:4b' has no vision support; try: ollama pull qwen3.6"
            ),
        ),
    ):
        rc = cmd_transcribe(
            str(video), [], None, "base", flavor="fancy", vision_model="qwen3:4b"
        )
    assert rc == 1
    err = capsys.readouterr().err
    assert "no vision support" in err
    assert "ollama pull qwen3.6" in err


def test_transcribe_auto_picks_fancy_for_video(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    timed = json.dumps(
        {
            "language": "en",
            "text": "hi",
            "segments": [{"start": 0, "end": 1, "text": "hi"}],
        }
    )
    frame = tmp_path / "0001.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    with (
        patch("commands.ai_transcribe_cmd.ensure_transcribe_engine", return_value=fake_python),
        patch("commands.ai_transcribe_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_transcribe_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_transcribe_cmd.has_video_stream", return_value=True),
        patch("commands.ai_transcribe_cmd.ensure_vision_model"),
        patch("commands.ai_transcribe_cmd.calc_fps", return_value=0.5),
        patch("commands.ai_transcribe_cmd.extract_frames", return_value=[frame]),
        patch(
            "commands.ai_transcribe_cmd.chat_with_images",
            return_value="Synopsis: a clip.",
        ) as chat,
        patch("commands.ai_transcribe_cmd._run_stt", return_value=(0, timed)),
    ):
        rc = cmd_transcribe(str(video), [], "en", "base", flavor="auto")
    assert rc == 0
    out, err = capsys.readouterr()
    assert out == "Synopsis: a clip.\n"
    assert "flavor: fancy" in err
    chat.assert_called_once()
    assert chat.call_args[0][0] == "qwen3.6"


def test_resolve_vision_model_precedence(monkeypatch):
    monkeypatch.delenv("N0B_TRANSCRIBE_VISION_MODEL", raising=False)
    assert resolve_vision_model(None) == "qwen3.6"
    monkeypatch.setenv("N0B_TRANSCRIBE_VISION_MODEL", "qwen3.5:4b")
    assert resolve_vision_model(None) == "qwen3.5:4b"
    assert resolve_vision_model("custom-vl") == "custom-vl"


def test_ensure_vision_model_rejects_text_only(monkeypatch):
    monkeypatch.setattr(
        "commands.ai_ollama.ensure_ollama_running", lambda: None
    )
    monkeypatch.setattr(
        "commands.ai_ollama.model_capabilities",
        lambda model: ["completion", "tools"],
    )
    with pytest.raises(OllamaError, match="no vision support"):
        ensure_vision_model("qwen3:4b")


def test_ensure_vision_model_missing(monkeypatch):
    monkeypatch.setattr(
        "commands.ai_ollama.ensure_ollama_running", lambda: None
    )

    def boom(model: str):
        raise OllamaError("HTTP 404 from Ollama /api/show: model not found")

    monkeypatch.setattr("commands.ai_ollama.model_capabilities", boom)
    with pytest.raises(OllamaError, match="ollama pull qwen3.6"):
        ensure_vision_model("missing-vl")

def test_speakable_keeps_phoneme_overrides():
    md = "See [Pay-i](/pˈeɪ ˈaɪ/) and [docs](https://example.com/x)."
    assert speakable(md) == "See [Pay-i](/pˈeɪ ˈaɪ/) and docs."


def test_speakable_strips_root_relative_links():
    md = "Open [install](/docs/install) next."
    assert speakable(md) == "Open install next."


def test_speakable_drops_fences_and_tables():
    md = "# Title\n\n| a | b |\n|---|---|\n\nHello\n\n```py\nx=1\n```\n"
    assert speakable(md) == " Title\n\n\nHello\n"


def test_apply_speak_replacements_substitutes():
    text, applied = apply_speak_replacements(
        "a8s ships via tell.", [("\\ba8s\\b", "A eight S")]
    )
    assert text == "A eight S ships via tell."
    assert applied == ["\\ba8s\\b => A eight S (x1)"]


def test_apply_pronunciations_wraps_ipa():
    text, applied = apply_pronunciations(
        "Pay-i uses k7e.", [("Pay-i", "pˈeɪ ˈaɪ")]
    )
    assert text == "[Pay-i](/pˈeɪ ˈaɪ/) uses k7e."
    assert applied == ["Pay-i => /pˈeɪ ˈaɪ/ (x1)"]


def test_resolve_speak_voice_sticky(tmp_path, monkeypatch):
    voice_file = tmp_path / "speak-voice.txt"
    voice_file.write_text("af_nicole\n")
    with patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", voice_file):
        assert resolve_speak_voice(None, "kokoro") == ("af_nicole", str(voice_file))
        assert resolve_speak_voice("af_bella", "kokoro") == ("af_bella", "cli")


def test_resolve_speak_voice_builtin_default(tmp_path):
    missing = tmp_path / "missing.txt"
    with patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", missing):
        assert resolve_speak_voice(None, "kokoro") == ("af_heart", "built-in default")
        assert resolve_speak_voice(None, "say") == (None, "system default")


def test_load_speak_text_inline_and_file(tmp_path):
    assert load_speak_text(["hello", "world"]) == "hello world"
    f = tmp_path / "note.txt"
    f.write_text("from file")
    assert load_speak_text([str(f)]) == "from file"


def test_resolve_speak_engine_prefers_say():
    with patch("commands.ai_speak_cmd.shutil.which", return_value="/usr/bin/say"):
        assert resolve_speak_engine(None) == "say"
        assert resolve_speak_engine("kokoro") == "kokoro"


def test_save_sticky_voice(tmp_path):
    voice_file = tmp_path / "speak-voice.txt"
    with patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", voice_file):
        assert save_sticky_voice("af_bella", voice_file) == 0
    assert voice_file.read_text() == "af_bella\n"


def test_speak_save_voice_only(tmp_path):
    voice_file = tmp_path / "speak-voice.txt"
    with patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", voice_file):
        rc = cmd_speak(None, None, "af_nicole", 1.0, save=True)
    assert rc == 0
    assert voice_file.read_text() == "af_nicole\n"


def test_speak_save_replacements_only(tmp_path):
    repl = tmp_path / "speak-replacements.txt"
    with (
        patch("commands.ai_speak_cmd.SPEAK_REPLACEMENTS_FILE", repl),
        patch("commands.ai_speak_cmd.SPEAK_PRONUNCIATIONS_FILE", tmp_path / "p.txt"),
    ):
        rc = cmd_speak(
            None, None, None, 1.0, save=True, replaces=["\\ba8s\\b => A eight S"]
        )
    assert rc == 0
    assert repl.read_text() == "\\ba8s\\b => A eight S\n"


def test_speak_applies_teachings_before_kokoro(tmp_path, capsys):
    src = tmp_path / "note.txt"
    src.write_text("a8s ready")
    repl = tmp_path / "speak-replacements.txt"
    repl.write_text("\\ba8s\\b => A eight S\n")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    captured: dict[str, object] = {}
    with (
        patch("commands.ai_speak_cmd.ensure_kokoro", return_value=fake_python),
        patch("commands.ai_speak_cmd.SPEAK_REPLACEMENTS_FILE", repl),
        patch("commands.ai_speak_cmd.SPEAK_PRONUNCIATIONS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", tmp_path / "missing-voice.txt"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        def capture_run(cmd, **kwargs):
            import json

            captured["pieces"] = json.loads(
                Path(cmd[3]).read_text(encoding="utf-8")
            )
            return subprocess.CompletedProcess(cmd, 0)

        run.side_effect = capture_run
        rc = cmd_speak(
            [str(src)], str(tmp_path / "out.wav"), None, 1.0, engine="kokoro"
        )
    assert rc == 0
    assert captured["pieces"] == [
        {"text": "A eight S ready", "speed": 1.0, "silence_after": 0.0}
    ]
    err = capsys.readouterr().err
    assert "replacements applied" in err


def test_speak_say_play_inline(tmp_path):
    with (
        patch("commands.ai_speak_cmd.resolve_speak_engine", return_value="say"),
        patch("commands.ai_speak_cmd.shutil.which", return_value="/usr/bin/say"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        rc = cmd_speak(["hello"], None, None, 1.0, engine="say")
    assert rc == 0
    argv = run.call_args[0][0]
    assert argv[0] == "say"
    assert "-f" in argv
    assert "-o" not in argv


def test_speak_markdown_say_uses_section_pauses(tmp_path):
    src = tmp_path / "notes.md"
    src.write_text("# Alpha\n\nHello.\n\n## Beta\n\nWorld.\n")
    spoken: dict[str, str] = {}
    with (
        patch("commands.ai_speak_cmd.resolve_speak_engine", return_value="say"),
        patch("commands.ai_speak_cmd.shutil.which", return_value="/usr/bin/say"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        def capture(cmd, **kwargs):
            spoken["text"] = Path(cmd[cmd.index("-f") + 1]).read_text(
                encoding="utf-8"
            )
            return subprocess.CompletedProcess(cmd, 0)

        run.side_effect = capture
        rc = cmd_speak([str(src)], None, None, 1.0, engine="say")
    assert rc == 0
    assert "[[slnc 2000]]" in spoken["text"]
    assert "Alpha" in spoken["text"]
    assert "Beta" in spoken["text"]


def test_speak_markdown_flat_skips_section_pauses(tmp_path):
    src = tmp_path / "notes.md"
    src.write_text("# Alpha\n\nHello.\n\n## Beta\n")
    spoken: dict[str, str] = {}
    with (
        patch("commands.ai_speak_cmd.resolve_speak_engine", return_value="say"),
        patch("commands.ai_speak_cmd.shutil.which", return_value="/usr/bin/say"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        def capture(cmd, **kwargs):
            spoken["text"] = Path(cmd[cmd.index("-f") + 1]).read_text(
                encoding="utf-8"
            )
            return subprocess.CompletedProcess(cmd, 0)

        run.side_effect = capture
        rc = cmd_speak([str(src)], None, None, 1.0, engine="say", flat=True)
    assert rc == 0
    assert "[[slnc" not in spoken["text"]


def test_speak_markdown_pause_overrides(tmp_path):
    src = tmp_path / "notes.md"
    src.write_text("# Alpha\n\nHello.\n\n## Beta\n\n### Gamma\n")
    spoken: dict[str, str] = {}
    with (
        patch("commands.ai_speak_cmd.resolve_speak_engine", return_value="say"),
        patch("commands.ai_speak_cmd.shutil.which", return_value="/usr/bin/say"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        def capture(cmd, **kwargs):
            spoken["text"] = Path(cmd[cmd.index("-f") + 1]).read_text(
                encoding="utf-8"
            )
            return subprocess.CompletedProcess(cmd, 0)

        run.side_effect = capture
        rc = cmd_speak(
            [str(src)],
            None,
            None,
            1.0,
            engine="say",
            pause_major=1.5,
            pause_minor=0.8,
            pause_para=0.2,
        )
    assert rc == 0
    assert "[[slnc 1500]]" in spoken["text"]
    assert "[[slnc 800]]" in spoken["text"]
    assert "[[slnc 200]]" in spoken["text"]
    assert "[[slnc 2000]]" not in spoken["text"]


def test_speak_markdown_no_emphasis(tmp_path, capsys):
    src = tmp_path / "notes.md"
    src.write_text("# Title\n\nUse **care** with `x`.\n\n## Next\n")
    spoken: dict[str, str] = {}
    with (
        patch("commands.ai_speak_cmd.resolve_speak_engine", return_value="say"),
        patch("commands.ai_speak_cmd.shutil.which", return_value="/usr/bin/say"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        def capture(cmd, **kwargs):
            spoken["text"] = Path(cmd[cmd.index("-f") + 1]).read_text(
                encoding="utf-8"
            )
            return subprocess.CompletedProcess(cmd, 0)

        run.side_effect = capture
        rc = cmd_speak(
            [str(src)], None, None, 1.0, engine="say", emphasis=False
        )
    assert rc == 0
    assert "[[emph +]]" not in spoken["text"]
    assert "[[slnc 2000]]" in spoken["text"]
    assert "care" in spoken["text"]
    assert "emphasis=off" in capsys.readouterr().err


def test_speak_markdown_kokoro_manifest(tmp_path, capsys):
    src = tmp_path / "notes.md"
    src.write_text("# Alpha\n\n**bold** and `code`\n\n## Beta\n")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    captured: dict[str, object] = {}
    with (
        patch("commands.ai_speak_cmd.ensure_kokoro", return_value=fake_python),
        patch("commands.ai_speak_cmd.SPEAK_REPLACEMENTS_FILE", tmp_path / "r.txt"),
        patch("commands.ai_speak_cmd.SPEAK_PRONUNCIATIONS_FILE", tmp_path / "p.txt"),
        patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", tmp_path / "v.txt"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        def capture_run(cmd, **kwargs):
            captured["pieces"] = json.loads(
                Path(cmd[3]).read_text(encoding="utf-8")
            )
            return subprocess.CompletedProcess(cmd, 0)

        run.side_effect = capture_run
        rc = cmd_speak(
            [str(src)], str(tmp_path / "out.wav"), None, 1.0, engine="kokoro"
        )
    assert rc == 0
    pieces = captured["pieces"]
    assert isinstance(pieces, list)
    assert len(pieces) > 1
    texts = [p["text"] for p in pieces if p["text"].strip()]
    assert any("Alpha" in t for t in texts)
    assert any("bold" in t for t in texts)
    assert any("code" in t for t in texts)
    assert any(p["silence_after"] >= 2.0 for p in pieces)
    assert any(p["speed"] < 1.0 for p in pieces)
    err = capsys.readouterr().err
    assert "markdown:" in err
    assert "emphasis=on" in err


def test_speak_markdown_only_fences_returns_nothing(tmp_path, capsys):
    src = tmp_path / "notes.md"
    src.write_text("```\nsecret()\n```\n")
    with (
        patch("commands.ai_speak_cmd.resolve_speak_engine", return_value="say"),
        patch("commands.ai_speak_cmd.shutil.which", return_value="/usr/bin/say"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        rc = cmd_speak([str(src)], None, None, 1.0, engine="say")
    assert rc == 2
    run.assert_not_called()
    assert "nothing to say" in capsys.readouterr().err


def test_speak_markdown_empty_input_returns_nothing(tmp_path, capsys):
    with (
        patch("commands.ai_speak_cmd.resolve_speak_engine", return_value="say"),
        patch("commands.ai_speak_cmd.shutil.which", return_value="/usr/bin/say"),
        patch("commands.ai_speak_cmd.subprocess.run") as run,
    ):
        rc = cmd_speak([""], None, None, 1.0, engine="say")
    assert rc == 2
    run.assert_not_called()
    assert "nothing to say" in capsys.readouterr().err


def test_speak_help():
    proc = run_n0b("ai", "speak", "--help")
    assert proc.returncode == 0
    assert "--pronounce" in proc.stdout
    assert "--save" in proc.stdout
    assert "--engine" in proc.stdout
    assert "--pause-major" in proc.stdout
    assert "--pause-minor" in proc.stdout
    assert "--pause-para" in proc.stdout
    assert "--flat" in proc.stdout
    assert "--no-emphasis" in proc.stdout
    assert "play on speakers" in proc.stdout


def test_video_gif_help():
    proc = run_n0b("video", "gif", "--help")
    assert proc.returncode == 0
    assert "--preset" in proc.stdout
    assert "--fps" in proc.stdout
    assert "--width" in proc.stdout


def test_resolve_gif_settings_presets_and_overrides():
    thumb = resolve_gif_settings("thumb")
    assert thumb["adaptive"] is True
    assert thumb["width"] == 320
    assert thumb["colors"] == 64
    small = resolve_gif_settings("small")
    assert small["adaptive"] is False
    assert small["fps"] == 8.0
    assert small["width"] == 800
    assert small["colors"] == 32
    overridden = resolve_gif_settings("thumb", fps=5.0, width=400, colors=128)
    assert overridden["adaptive"] is False
    assert overridden["fps"] == 5.0
    assert overridden["width"] == 400
    assert overridden["colors"] == 128


def test_cmd_gif_missing_file(tmp_path, capsys):
    rc = cmd_gif(str(tmp_path / "missing.mp4"))
    assert rc == 1
    assert "no such file" in capsys.readouterr().err


def test_cmd_gif_small_preset_invokes_ffmpeg(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "out.gif"
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "palettegen" in " ".join(argv):
            Path(argv[-1]).write_bytes(b"pal")
        else:
            Path(argv[-1]).write_bytes(b"GIF89a")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("commands.video_cmd.subprocess.run", side_effect=fake_run):
        rc = cmd_gif(str(video), str(out), preset="small")
    assert rc == 0
    assert out.is_file()
    assert len(calls) == 2
    assert "palettegen" in " ".join(calls[0])
    assert "paletteuse=dither=sierra2_4a" in " ".join(calls[1])
    assert "fps=8" in " ".join(calls[0])
    assert "scale=800:-1:flags=lanczos" in " ".join(calls[1])
    assert f"GIF saved to: {out}" in capsys.readouterr().out


def test_cmd_gif_thumb_preset_extracts_frames(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "thumb.gif"
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        joined = " ".join(argv)
        if "%04d.png" in joined:
            frames_dir = Path(argv[-1]).parent
            frames_dir.mkdir(parents=True, exist_ok=True)
            (frames_dir / "0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        elif "palettegen" in joined:
            Path(argv[-1]).write_bytes(b"pal")
        else:
            Path(argv[-1]).write_bytes(b"GIF89a")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with (
        patch("commands.video_cmd.subprocess.run", side_effect=fake_run),
        patch("commands.video_cmd.calc_gif_fps", return_value=0.5),
    ):
        rc = cmd_gif(str(video), str(out), preset="thumb")
    assert rc == 0
    assert any("%04d.png" in " ".join(c) for c in calls)
    assert any("palettegen" in " ".join(c) for c in calls)
    assert any("paletteuse=dither=sierra2_4a" in " ".join(c) for c in calls)
    assert "scale=320:-1:flags=lanczos" in " ".join(calls[-1])
    assert f"GIF saved to: {out}" in capsys.readouterr().out


def test_cmd_last_frame_missing(tmp_path, capsys):
    rc = cmd_last_frame(str(tmp_path / "nope.mp4"), None)
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()
