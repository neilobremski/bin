"""n0b CLI tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

N0B_PY = Path(__file__).resolve().parents[1] / "n0b.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commands.ai_cmd import (  # noqa: E402
    apply_pronunciations,
    apply_replacements,
    apply_speak_replacements,
    build_image_argv,
    cmd_ai,
    cmd_image,
    cmd_speak,
    cmd_transcribe,
    load_speak_text,
    merged_hints,
    read_replacements,
    resolve_image_ref,
    resolve_speak_engine,
    resolve_speak_voice,
    save_hints,
    save_replacements,
    save_sticky_voice,
    speakable,
)
from commands.secrets_cmd import cmd_set, resolve  # noqa: E402


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


def test_ai_video_ltx2_passes_flag():
    with patch("commands.ai_cmd.subprocess.run") as run:
        run.return_value.returncode = 0
        rc = cmd_ai("video", "ltx-2", ["hello"])
        assert rc == 0
        argv = run.call_args[0][0]
        assert argv[0] == "bash"
        assert argv[2] == "--ltx2"
        assert argv[3] == "hello"


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
    with patch("commands.ai_cmd.subprocess.run") as run:
        run.return_value.returncode = 0
        rc = cmd_image(None, ["make it painterly"], [str(ref)], 0.4, None)
    assert rc == 0
    argv = run.call_args[0][0]
    assert argv[2:6] == ["--ref", str(ref), "--strength", "0.4"]
    assert argv[6] == "make it painterly"


def test_image_help_shows_ref():
    proc = run_n0b("ai", "image", "--help")
    assert proc.returncode == 0
    assert "--ref" in proc.stdout
    assert "--strength" in proc.stdout


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
    with patch("commands.ai_cmd.HINTS_FILE", hints_file):
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
        patch("commands.ai_cmd._whisper_python", return_value=fake_python),
        patch("commands.ai_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_cmd.REPLACEMENTS_FILE", repl),
        patch("commands.ai_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "Jerry said hi.\n"
        rc = cmd_transcribe(str(audio), [], "en", "base")
    assert rc == 0
    out, err = capsys.readouterr()
    assert out == "Jerry (possible transcribe error, might be 'Gerry') said hi.\n"
    assert "Jerry => Gerry (x1)" in err


def test_transcribe_invokes_whisper_venv(tmp_path):
    audio = tmp_path / "memo.wav"
    audio.write_bytes(b"RIFF")
    fake_python = tmp_path / "venv" / "bin" / "python3"
    with (
        patch("commands.ai_cmd._whisper_python", return_value=fake_python),
        patch("commands.ai_cmd.HINTS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_cmd.REPLACEMENTS_FILE", tmp_path / "missing2.txt"),
        patch("commands.ai_cmd.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "hello\n"
        rc = cmd_transcribe(str(audio), ["Pay-i"], "en", "base")
        assert rc == 0
        argv = run.call_args[0][0]
        assert argv[0] == str(fake_python)
        assert argv[1] == "-c"
        assert argv[3:] == [str(audio), "base", "en", "Pay-i"]


def test_transcribe_help():
    proc = run_n0b("ai", "transcribe", "--help")
    assert proc.returncode == 0
    assert "--hint" in proc.stdout
    assert "--language" in proc.stdout


def test_speakable_keeps_phoneme_overrides():
    md = "See [Pay-i](/pˈeɪ ˈaɪ/) and [docs](https://example.com/x)."
    assert speakable(md) == "See [Pay-i](/pˈeɪ ˈaɪ/) and docs."


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
    with patch("commands.ai_cmd.SPEAK_VOICE_FILE", voice_file):
        assert resolve_speak_voice(None, "kokoro") == ("af_nicole", str(voice_file))
        assert resolve_speak_voice("af_bella", "kokoro") == ("af_bella", "cli")


def test_resolve_speak_voice_builtin_default(tmp_path):
    missing = tmp_path / "missing.txt"
    with patch("commands.ai_cmd.SPEAK_VOICE_FILE", missing):
        assert resolve_speak_voice(None, "kokoro") == ("af_heart", "built-in default")
        assert resolve_speak_voice(None, "say") == (None, "system default")


def test_load_speak_text_inline_and_file(tmp_path):
    assert load_speak_text(["hello", "world"]) == "hello world"
    f = tmp_path / "note.txt"
    f.write_text("from file")
    assert load_speak_text([str(f)]) == "from file"


def test_resolve_speak_engine_prefers_say():
    with patch("commands.ai_cmd.shutil.which", return_value="/usr/bin/say"):
        assert resolve_speak_engine(None) == "say"
        assert resolve_speak_engine("kokoro") == "kokoro"


def test_save_sticky_voice(tmp_path):
    voice_file = tmp_path / "speak-voice.txt"
    with patch("commands.ai_cmd.SPEAK_VOICE_FILE", voice_file):
        assert save_sticky_voice("af_bella", voice_file) == 0
    assert voice_file.read_text() == "af_bella\n"


def test_speak_save_voice_only(tmp_path):
    voice_file = tmp_path / "speak-voice.txt"
    with patch("commands.ai_cmd.SPEAK_VOICE_FILE", voice_file):
        rc = cmd_speak(None, None, "af_nicole", 1.0, save=True)
    assert rc == 0
    assert voice_file.read_text() == "af_nicole\n"


def test_speak_save_replacements_only(tmp_path):
    repl = tmp_path / "speak-replacements.txt"
    with (
        patch("commands.ai_cmd.SPEAK_REPLACEMENTS_FILE", repl),
        patch("commands.ai_cmd.SPEAK_PRONUNCIATIONS_FILE", tmp_path / "p.txt"),
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
    captured: dict[str, str] = {}
    with (
        patch("commands.ai_cmd._kokoro_python", return_value=fake_python),
        patch("commands.ai_cmd.SPEAK_REPLACEMENTS_FILE", repl),
        patch("commands.ai_cmd.SPEAK_PRONUNCIATIONS_FILE", tmp_path / "missing.txt"),
        patch("commands.ai_cmd.SPEAK_VOICE_FILE", tmp_path / "missing-voice.txt"),
        patch("commands.ai_cmd.subprocess.run") as run,
    ):
        def capture_run(cmd, **kwargs):
            captured["text"] = Path(cmd[3]).read_text(encoding="utf-8")
            run.return_value.returncode = 0
            return run.return_value

        run.side_effect = capture_run
        rc = cmd_speak(
            [str(src)], str(tmp_path / "out.wav"), None, 1.0, engine="kokoro"
        )
    assert rc == 0
    assert captured["text"] == "A eight S ready"
    err = capsys.readouterr().err
    assert "replacements applied" in err


def test_speak_say_play_inline(tmp_path):
    with patch("commands.ai_cmd.resolve_speak_engine", return_value="say"), \
            patch("commands.ai_cmd.subprocess.run") as run:
        run.return_value.returncode = 0
        rc = cmd_speak(["hello"], None, None, 1.0, engine="say")
    assert rc == 0
    argv = run.call_args[0][0]
    assert argv[0] == "say"
    assert "-f" in argv
    assert "-o" not in argv


def test_speak_help():
    proc = run_n0b("ai", "speak", "--help")
    assert proc.returncode == 0
    assert "--pronounce" in proc.stdout
    assert "--save" in proc.stdout
    assert "--engine" in proc.stdout
    assert "play on speakers" in proc.stdout
