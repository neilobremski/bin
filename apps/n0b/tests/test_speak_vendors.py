"""Tests for multi-vendor n0b ai speak, voice fallback lists, and Realtime TTS."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import socket
import struct
import subprocess
import sys
import threading
import urllib.error
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli import main as cli_main  # noqa: E402
from commands import _pocket_runner  # noqa: E402
from commands.ai_speak_cmd import (  # noqa: E402
    cmd_speak,
    read_sticky_engine,
    resolve_speak_engine,
    resolve_speak_voice,
    save_sticky_engine,
)
from commands.secrets_cmd import cmd_set  # noqa: E402
from commands.speak_vendors import (  # noqa: E402
    WS_GUID,
    SpeakVendorError,
    _RealtimeWebSocket,
    resolve_vendor_model,
    synthesize_cartesia_pcm,
    synthesize_elevenlabs_pcm,
    synthesize_openai_pcm,
)
from commands.speak_voices import (  # noqa: E402
    POCKET_BY_ID,
    POCKET_MODEL_REVISION,
    chunk_text,
    pocket_cache_path,
    resolve_for_engine,
    resolve_vendor_preference_list,
)


def test_pocket_voice_preference_exact_prefix_contains_and_gender():
    assert resolve_for_engine("Alba", "pocket", "cli").voice == "alba"
    assert resolve_for_engine("pet", "pocket", "cli").voice == "peter_yearsley"
    assert resolve_for_engine("years", "pocket", "cli").voice == "peter_yearsley"
    res = resolve_for_engine("Ember, Male", "pocket", "cli")
    assert res.voice == "charles"
    assert res.how == "gender"
    assert res.missed == ("Ember",)
    assert resolve_for_engine("Ember, Female", "pocket", "cli").voice == "anna"


def test_male_never_substring_matches_female_or_names():
    # OpenAI catalog records no source gender metadata; "Male" must not match a name
    # and must fall back to the default voice ("marin").
    res = resolve_for_engine("Male", "openai", "cli")
    assert res.voice == "marin"
    assert res.how == "default"
    assert res.missed == ("Male",)


def test_kokoro_blends_and_friendly_preferences():
    blend = resolve_for_engine("af_nicole,af_bella", "kokoro", "cli")
    assert blend.voice == "af_nicole,af_bella"
    assert blend.how == "blend"

    jp = resolve_for_engine("jf_alpha", "kokoro", "cli")
    assert jp.voice == "jf_alpha"

    friendly = resolve_for_engine("Ember, Male", "kokoro", "cli")
    assert friendly.voice == "am_adam"
    assert friendly.how == "gender"


def test_foreign_sticky_voice_falls_back_cleanly_on_cloud_vendor(tmp_path):
    voice_file = tmp_path / "speak-voice.txt"
    voice_file.write_text("af_nicole\n", encoding="utf-8")
    with patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", voice_file):
        voice, src = resolve_speak_voice(None, "openai")
    assert voice == "marin"
    assert src == "built-in default"


def test_custom_cloud_voice_id_passes_through_on_cli():
    res = resolve_for_engine("voice_abc123", "openai", "cli")
    assert res.voice == "voice_abc123"
    assert res.how == "custom"

    el = resolve_for_engine("CustomElevenVoiceId99", "elevenlabs", "cli")
    assert el.voice == "CustomElevenVoiceId99"
    assert el.how == "custom"

    named = resolve_for_engine("George", "elevenlabs", "cli")
    assert named.voice == "JBFqnCBsd6RMkjVDRZzb"
    assert named.how == "exact"


def test_vendor_preference_list_skips_missing_secrets():
    spec = "openai:marin, elevenlabs:Rachel, pocket:Ember, pocket:Male, af_heart"
    with_key = resolve_vendor_preference_list(
        spec,
        "cli",
        "kokoro",
        secret_resolver=lambda name: "sk-live" if name == "OPENAI_API_KEY" else None,
        which=lambda _cmd: None,
    )
    assert (with_key.engine, with_key.voice) == ("openai", "marin")
    assert with_key.missed == ()

    without_keys = resolve_vendor_preference_list(
        spec,
        "cli",
        "kokoro",
        secret_resolver=lambda _name: None,
        which=lambda _cmd: None,
    )
    assert (without_keys.engine, without_keys.voice) == ("pocket", "charles")
    assert without_keys.missed == (
        "openai:marin",
        "elevenlabs:Rachel",
        "pocket:Ember",
    )


def test_auto_does_not_spend_cloud_credits_without_explicit_selection(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cmd_set("OPENAI_API_KEY", "sk-should-not-auto-select") == 0
    with (
        patch("commands.ai_speak_cmd.SPEAK_ENGINE_FILE", tmp_path / "no-engine.txt"),
        patch("commands.ai_speak_cmd.shutil.which", return_value=None),
    ):
        assert resolve_speak_engine(None) == "kokoro"
        assert resolve_speak_engine("auto") == "kokoro"


def test_sticky_engine_save_load_and_clear(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    engine_file = tmp_path / "speak-engine.txt"
    with patch("commands.ai_speak_cmd.SPEAK_ENGINE_FILE", engine_file):
        assert cli_main(["ai", "speak", "--vendor", "openai", "--save"]) == 0
        assert read_sticky_engine(engine_file) == "openai"
        # Without OPENAI_API_KEY, sticky openai falls back to local engine.
        with patch("commands.ai_speak_cmd.shutil.which", return_value=None):
            assert resolve_speak_engine(None) == "kokoro"
        # Once OPENAI_API_KEY is set via n0b secrets, sticky openai activates.
        assert cmd_set("OPENAI_API_KEY", "sk-ok") == 0
        assert resolve_speak_engine(None) == "openai"
        # Explicit --engine auto bypasses sticky engine; --save clears it.
        with patch("commands.ai_speak_cmd.shutil.which", return_value=None):
            assert resolve_speak_engine("auto") == "kokoro"
        assert save_sticky_engine("auto", engine_file) == 0
        assert not engine_file.exists()


def test_chunk_text_prefers_sentence_then_word_then_hard_limit():
    short = "Hello world."
    assert chunk_text(short, limit=50) == ["Hello world."]

    two = "First sentence ends here. Second sentence follows cleanly."
    chunks = chunk_text(two, limit=35)
    assert chunks == [
        "First sentence ends here.",
        "Second sentence follows cleanly.",
    ]

    words = "alpha beta gamma delta epsilon zeta"
    w_chunks = chunk_text(words, limit=16)
    assert " ".join(w_chunks) == words
    assert all(len(c) <= 16 for c in w_chunks)

    long_token = "x" * 25
    assert chunk_text(long_token, limit=10) == ["x" * 10, "x" * 10, "x" * 5]


def test_deprecated_openai_models_rejected_before_network(tmp_path, capsys):
    for bad in ("tts-1", "tts-1-hd", "gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15"):
        with pytest.raises(SpeakVendorError, match="deprecated /v1/audio/speech"):
            resolve_vendor_model("openai", bad)

    out = tmp_path / "out.wav"
    with patch("commands.ai_speak_cmd.synthesize_openai_pcm") as synth:
        rc = cmd_speak(
            ["hello"], str(out), "marin", 1.0, engine="openai", model="tts-1"
        )
    assert rc == 2
    synth.assert_not_called()
    assert "deprecated /v1/audio/speech" in capsys.readouterr().err


def test_bad_output_extension_fails_before_cloud_call(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cmd_set("OPENAI_API_KEY", "sk-live") == 0
    with patch("commands.ai_speak_cmd.synthesize_openai_pcm") as synth:
        rc = cmd_speak(
            ["hello"], str(tmp_path / "out.ogg"), "marin", 1.0, engine="openai"
        )
    assert rc == 2
    synth.assert_not_called()
    assert "unsupported output format" in capsys.readouterr().err


def test_missing_openai_secret_fails_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = cmd_speak(
        ["hello"], str(tmp_path / "out.wav"), "marin", 1.0, engine="openai"
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY not found" in err
    assert "n0b secrets set OPENAI_API_KEY" in err


def _server_read_client_frame(conn: socket.socket) -> tuple[int, bytes]:
    def exact(n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            part = conn.recv(n - len(buf))
            assert part, "unexpected client EOF"
            buf.extend(part)
        return bytes(buf)

    b0, b1 = exact(2)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    assert masked, "RFC 6455 requires client frames to be masked"
    length = b1 & 0x7F
    if length == 126:
        (length,) = struct.unpack(">H", exact(2))
    elif length == 127:
        (length,) = struct.unpack(">Q", exact(8))
    mask = exact(4)
    data = exact(length) if length else b""
    unmasked = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
    return opcode, unmasked


def _server_send_frame(
    conn: socket.socket, opcode: int, payload: bytes, *, fin: bool = True
) -> None:
    first = (0x80 if fin else 0x00) | (opcode & 0x0F)
    header = bytearray([first])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header.extend(struct.pack(">H", n))
    else:
        header.append(127)
        header.extend(struct.pack(">Q", n))
    conn.sendall(bytes(header) + payload)


def test_openai_realtime_websocket_protocol_fragments_ping_and_speed():
    client_sock, server_sock = socket.socketpair()
    received_events: list[dict] = []
    pong_seen = threading.Event()
    pcm_chunk_1 = b"\x10\x00" * 240
    pcm_chunk_2 = b"\x20\x00" * 240

    def server() -> None:
        try:
            _server_send_frame(
                server_sock,
                0x1,
                json.dumps({"type": "session.created", "session": {}}).encode(),
            )
            op, data = _server_read_client_frame(server_sock)
            assert op == 0x1
            received_events.append(json.loads(data))

            # Send Ping before session.updated to verify automatic Pong handling.
            _server_send_frame(server_sock, 0x9, b"keepalive")
            op_pong, pong_data = _server_read_client_frame(server_sock)
            if op_pong == 0xA and pong_data == b"keepalive":
                pong_seen.set()

            _server_send_frame(
                server_sock, 0x1, json.dumps({"type": "session.updated"}).encode()
            )

            # Piece 1: response.create -> send fragmented delta + response.done
            op, data = _server_read_client_frame(server_sock)
            assert op == 0x1
            received_events.append(json.loads(data))
            delta_json = json.dumps(
                {
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(pcm_chunk_1).decode("ascii"),
                }
            ).encode()
            mid = len(delta_json) // 2
            _server_send_frame(server_sock, 0x1, delta_json[:mid], fin=False)
            _server_send_frame(server_sock, 0x0, delta_json[mid:], fin=True)
            _server_send_frame(
                server_sock,
                0x1,
                json.dumps(
                    {"type": "response.done", "response": {"status": "completed"}}
                ).encode(),
            )

            # Piece 2 has different speed -> expect session.update then response.create
            op, data = _server_read_client_frame(server_sock)
            assert op == 0x1
            received_events.append(json.loads(data))
            _server_send_frame(
                server_sock, 0x1, json.dumps({"type": "session.updated"}).encode()
            )
            op, data = _server_read_client_frame(server_sock)
            assert op == 0x1
            received_events.append(json.loads(data))
            _server_send_frame(
                server_sock,
                0x1,
                json.dumps(
                    {
                        "type": "response.output_audio.delta",
                        "delta": base64.b64encode(pcm_chunk_2).decode("ascii"),
                    }
                ).encode(),
            )
            _server_send_frame(
                server_sock,
                0x1,
                json.dumps(
                    {"type": "response.done", "response": {"status": "completed"}}
                ).encode(),
            )
            # Drain client Close frame
            _server_read_client_frame(server_sock)
        finally:
            server_sock.close()

    t = threading.Thread(target=server, daemon=True)
    t.start()
    pcm = synthesize_openai_pcm(
        [
            {"text": "Hello there.", "speed": 1.0, "silence_after": 0.1},
            {"text": "Emphasized.", "speed": 0.92, "silence_after": 0.0},
        ],
        voice="voice_custom42",
        model="gpt-realtime-2.1-mini",
        api_key="sk-secret-test",
        connect_fn=lambda **kwargs: _RealtimeWebSocket(client_sock),
    )
    t.join(timeout=5)
    assert pong_seen.is_set()
    assert len(pcm) == len(pcm_chunk_1) + 2400 * 2 + len(pcm_chunk_2)
    assert received_events[0]["type"] == "session.update"
    assert received_events[0]["session"]["audio"]["output"]["voice"] == {
        "id": "voice_custom42"
    }
    assert received_events[1]["type"] == "response.create"
    assert received_events[1]["response"]["conversation"] == "none"
    assert received_events[2]["type"] == "session.update"
    assert received_events[2]["session"]["audio"]["output"]["speed"] == 0.92
    assert received_events[3]["type"] == "response.create"


def test_openai_realtime_failed_response_status_raises_and_scrubs_key():
    client_sock, server_sock = socket.socketpair()

    def server() -> None:
        try:
            _server_send_frame(
                server_sock, 0x1, json.dumps({"type": "session.created"}).encode()
            )
            _server_read_client_frame(server_sock)
            _server_send_frame(
                server_sock, 0x1, json.dumps({"type": "session.updated"}).encode()
            )
            _server_read_client_frame(server_sock)
            _server_send_frame(
                server_sock,
                0x1,
                json.dumps(
                    {
                        "type": "response.done",
                        "response": {
                            "status": "failed",
                            "status_details": {
                                "error": {
                                    "message": "bad key sk-top-secret-value quota"
                                }
                            },
                        },
                    }
                ).encode(),
            )
        finally:
            server_sock.close()

    threading.Thread(target=server, daemon=True).start()
    with pytest.raises(SpeakVendorError) as exc_info:
        synthesize_openai_pcm(
            [{"text": "Hello", "speed": 1.0, "silence_after": 0.0}],
            voice="marin",
            model="gpt-realtime-2.1-mini",
            api_key="sk-top-secret-value",
            connect_fn=lambda **kwargs: _RealtimeWebSocket(client_sock),
        )
    msg = str(exc_info.value)
    assert "failed" in msg
    assert "sk-top-secret-value" not in msg
    assert "[REDACTED]" in msg


def test_realtime_websocket_connect_verifies_accept_and_handles_http_401():
    client_raw, server_sock = socket.socketpair()

    def server_401() -> None:
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += server_sock.recv(4096)
        body = json.dumps({"error": {"message": "invalid key sk-bad"}}).encode()
        resp = (
            b"HTTP/1.1 401 Unauthorized\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        server_sock.sendall(resp)
        server_sock.close()

    threading.Thread(target=server_401, daemon=True).start()

    class DummyCtx:
        def wrap_socket(self, raw, server_hostname=None):
            return raw

    with (
        patch("commands.speak_vendors.ssl.create_default_context", return_value=DummyCtx()),
        patch("commands.speak_vendors.socket.create_connection", return_value=client_raw),
        pytest.raises(SpeakVendorError) as exc_info,
    ):
        _RealtimeWebSocket.connect(api_key="sk-bad", model="gpt-realtime-2.1-mini")
    assert "HTTP 401" in str(exc_info.value)
    assert "sk-bad" not in str(exc_info.value)


def test_realtime_websocket_connect_accept_handshake():
    client_raw, server_sock = socket.socketpair()

    def server_101() -> None:
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += server_sock.recv(4096)
        headers = {}
        for line in buf.decode("latin1").split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        accept = base64.b64encode(
            hashlib.sha1((headers["sec-websocket-key"] + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        server_sock.sendall(
            f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode(
                "ascii"
            )
        )
        _server_send_frame(server_sock, 0x1, b'{"type":"ok"}')
        server_sock.close()

    threading.Thread(target=server_101, daemon=True).start()

    class DummyCtx:
        def wrap_socket(self, raw, server_hostname=None):
            return raw

    with (
        patch("commands.speak_vendors.ssl.create_default_context", return_value=DummyCtx()),
        patch("commands.speak_vendors.socket.create_connection", return_value=client_raw),
    ):
        ws = _RealtimeWebSocket.connect(api_key="sk-ok", model="gpt-realtime-2.1-mini")
        assert ws.recv_json() == {"type": "ok"}
        ws.close()


def test_cmd_speak_openai_markdown_uses_pinned_n0b_secret_and_writes_wav(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stale-env")
    assert cmd_set("OPENAI_API_KEY", "sk-pinned-secret") == 0

    md = tmp_path / "brief.md"
    md.write_text(
        "# Heading\n\nSee **bold** and [Pay-i](/pˈeɪ ˈaɪ/) now.\n\n## Next\n\nDone.\n"
    )
    out_wav = tmp_path / "out.wav"
    captured: dict[str, object] = {}

    def fake_openai(pieces, *, voice, model, api_key):
        captured["pieces"] = pieces
        captured["voice"] = voice
        captured["model"] = model
        captured["api_key"] = api_key
        return b"\x01\x00" * 2400

    with (
        patch("commands.ai_speak_cmd.SPEAK_REPLACEMENTS_FILE", tmp_path / "r.txt"),
        patch("commands.ai_speak_cmd.SPEAK_PRONUNCIATIONS_FILE", tmp_path / "p.txt"),
        patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", tmp_path / "v.txt"),
        patch("commands.ai_speak_cmd.SPEAK_ENGINE_FILE", tmp_path / "e.txt"),
        patch("commands.ai_speak_cmd.synthesize_openai_pcm", side_effect=fake_openai),
    ):
        rc = cli_main(
            [
                "ai",
                "speak",
                str(md),
                "--vendor",
                "openai",
                "-v",
                "Cedar",
                "-o",
                str(out_wav),
            ]
        )
    assert rc == 0
    assert captured["api_key"] == "sk-pinned-secret"
    assert captured["voice"] == "cedar"
    assert captured["model"] == "gpt-realtime-2.1-mini"
    pieces = captured["pieces"]
    assert isinstance(pieces, list)
    texts = [p["text"] for p in pieces if p["text"].strip()]
    # Whole-block prosody: inline **bold** stays inside its paragraph rather than fragmenting into 1-word API calls.
    assert texts == [
        "Heading",
        "See bold and Pay-i now.",
        "Next",
        "Done.",
    ]
    assert any(p["silence_after"] >= 2.0 for p in pieces)

    with wave.open(str(out_wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 24000
        assert w.getnframes() == 2400


def test_elevenlabs_and_cartesia_http_payloads_and_error_redaction():
    seen_el: dict[str, object] = {}

    class FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _n=-1):
            return self._payload

    def fake_urlopen_el(req, timeout=60.0):
        seen_el["url"] = req.full_url
        seen_el["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen_el["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp(b"\x02\x00" * 100)

    with patch("commands.speak_vendors.urllib.request.urlopen", side_effect=fake_urlopen_el):
        pcm = synthesize_elevenlabs_pcm(
            [{"text": "Hello world.", "speed": 1.1, "silence_after": 0.1}],
            voice="21m00Tcm4TlvDq8ikWAM",
            model="eleven_flash_v2_5",
            api_key="xi-secret",
        )
    assert len(pcm) == 200 + 2400 * 2
    assert "output_format=pcm_24000" in str(seen_el["url"])
    assert seen_el["headers"]["xi-api-key"] == "xi-secret"
    assert seen_el["body"] == {
        "text": "Hello world.",
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {"speed": 1.1},
    }

    seen_ca: dict[str, object] = {}

    def fake_urlopen_ca(req, timeout=60.0):
        seen_ca["url"] = req.full_url
        seen_ca["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen_ca["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp(b"\x03\x00" * 100)

    with patch("commands.speak_vendors.urllib.request.urlopen", side_effect=fake_urlopen_ca):
        pcm_ca = synthesize_cartesia_pcm(
            [{"text": "Hi there.", "speed": 1.0, "silence_after": 0.0}],
            voice="f786b574-daa5-4673-aa0c-cbe3e8534c02",
            model="sonic-3.6",
            api_key="ca-secret",
        )
    assert len(pcm_ca) == 200
    assert seen_ca["url"] == "https://api.cartesia.ai/tts/bytes"
    assert seen_ca["headers"]["x-api-key"] == "ca-secret"
    assert seen_ca["body"]["voice"] == {
        "mode": "id",
        "id": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    }

    def fake_http_err(req, timeout=60.0):
        raise urllib.error.HTTPError(
            req.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":{"message":"invalid key xi-secret"}}'),
        )

    with (
        patch("commands.speak_vendors.urllib.request.urlopen", side_effect=fake_http_err),
        pytest.raises(SpeakVendorError) as exc_info,
    ):
        synthesize_elevenlabs_pcm(
            [{"text": "Hello", "speed": 1.0, "silence_after": 0.0}],
            voice="21m00Tcm4TlvDq8ikWAM",
            model="eleven_flash_v2_5",
            api_key="xi-secret",
        )
    assert "xi-secret" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_ffmpeg_m4a_and_mp3_conversion_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cmd_set("OPENAI_API_KEY", "sk-ok") == 0
    ffmpeg_calls: list[list[str]] = []

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/ffmpeg" if cmd == "ffmpeg" else None

    def fake_run(argv, **kwargs):
        ffmpeg_calls.append(list(argv))
        Path(argv[-1]).write_bytes(b"encoded")
        return subprocess.CompletedProcess(argv, 0)

    for suffix in (".m4a", ".mp3"):
        target = tmp_path / f"report{suffix}"
        with (
            patch("commands.speak_vendors.shutil.which", side_effect=fake_which),
            patch("commands.speak_vendors.subprocess.run", side_effect=fake_run),
            patch(
                "commands.ai_speak_cmd.synthesize_openai_pcm",
                return_value=b"\x01\x00" * 480,
            ),
        ):
            rc = cmd_speak(
                ["hello"], str(target), "marin", 1.0, engine="openai"
            )
        assert rc == 0
        assert target.read_bytes() == b"encoded"
    assert any("aac" in c for c in ffmpeg_calls)
    assert any("libmp3lame" in c for c in ffmpeg_calls)


def test_speak_pocket_invokes_isolated_runner_with_resolved_voice(tmp_path):
    fake_python = tmp_path / "venv" / "bin" / "python3"
    out_wav = tmp_path / "pocket.wav"
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["pieces"] = json.loads(Path(cmd[2]).read_text(encoding="utf-8"))
        out_wav.write_bytes(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("commands.ai_speak_cmd.ensure_pocket_tts", return_value=fake_python),
        patch("commands.ai_speak_cmd.SPEAK_REPLACEMENTS_FILE", tmp_path / "r.txt"),
        patch("commands.ai_speak_cmd.SPEAK_PRONUNCIATIONS_FILE", tmp_path / "p.txt"),
        patch("commands.ai_speak_cmd.SPEAK_VOICE_FILE", tmp_path / "v.txt"),
        patch("commands.ai_speak_cmd.subprocess.run", side_effect=fake_run),
    ):
        rc = cmd_speak(
            ["Hello Pocket"], str(out_wav), "Ember, Male", 1.0, engine="pocket"
        )
    assert rc == 0
    assert captured["cmd"][0] == str(fake_python)
    assert captured["cmd"][4] == "charles"
    assert captured["pieces"] == [
        {"text": "Hello Pocket", "speed": 1.0, "silence_after": 0.0}
    ]


def test_pocket_runner_verifies_pinned_checksums(tmp_path, monkeypatch):
    hf_home = tmp_path / "hf"
    voice = POCKET_BY_ID["alba"]
    fake_assets = [
        _pocket_runner._Asset(POCKET_MODEL_REVISION, "model.bin", "", 4),
        _pocket_runner._Asset(POCKET_MODEL_REVISION, voice.pocket_embedding, "", 6),
    ]
    patched_assets: list[_pocket_runner._Asset] = []
    good_bytes = {"model.bin": b"good", voice.pocket_embedding: b"embed!"}
    for a in fake_assets:
        raw = good_bytes[a.name]
        digest = hashlib.sha256(raw).hexdigest()
        patched_assets.append(
            _pocket_runner._Asset(a.revision, a.name, digest, len(raw))
        )
        p = pocket_cache_path(hf_home, a.revision, a.name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)

    monkeypatch.setattr(_pocket_runner, "_assets_for", lambda _vid: patched_assets)
    import types

    fake_hub = types.ModuleType("huggingface_hub")
    downloads: list[str] = []

    def fake_download(*, repo_id, filename, revision, cache_dir):
        downloads.append(filename)
        p = pocket_cache_path(hf_home, revision, filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(good_bytes[filename])
        return str(p)

    fake_hub.hf_hub_download = fake_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    _pocket_runner.ensure_verified_assets(hf_home, "alba")
    assert downloads == []

    # Same-size in-place corruption -> caught by SHA-256, discarded, and re-downloaded.
    emb_path = pocket_cache_path(hf_home, POCKET_MODEL_REVISION, voice.pocket_embedding)
    emb_path.write_bytes(b"tamper")
    _pocket_runner.ensure_verified_assets(hf_home, "alba")
    assert downloads == [voice.pocket_embedding]
    assert emb_path.read_bytes() == b"embed!"
