"""n0b quota command tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commands.quota_cmd import (  # noqa: E402
    QUOTA_TOOLS,
    cmd_quota,
    detect_antigravity_process,
    get_listening_ports,
    parse_quota_response,
    resolve_api_ports,
)


SAMPLE_USER_STATUS = {
    "code": 0,
    "userStatus": {
        "email": "user@example.com",
        "planStatus": {
            "planInfo": {"planDisplayName": "Pro"},
            "availablePromptCredits": 42,
        },
        "clientModelConfigs": [
            {
                "label": "Gemini Flash",
                "model": "gemini-flash",
                "quotaInfo": {
                    "hourlyQuota": {
                        "remainingFraction": 0.56,
                        "resetTime": "2099-01-01T12:00:00Z",
                    }
                },
            }
        ],
    },
}


def test_parse_quota_response_user_status():
    parsed = parse_quota_response(SAMPLE_USER_STATUS, "userStatus")
    assert parsed["account"]["plan"] == "Pro"
    assert parsed["available_prompt_credits"] == 42
    assert len(parsed["models"]) == 1
    assert parsed["models"][0]["label"] == "Gemini Flash"
    assert parsed["models"][0]["buckets"][0]["remaining_fraction"] == pytest.approx(0.56)


def test_detect_antigravity_process_from_ps():
    ps_output = (
        "12345 /Users/me/.gemini/antigravity-cli/bin/language_server "
        "--csrf_token=abc123 --extension_server_port=9999\n"
    )
    with patch("commands.quota_cmd.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ps_output
        info = detect_antigravity_process()
    assert info["pid"] == 12345
    assert info["csrf_token"] == "abc123"
    assert info["extension_port"] == 9999


def test_detect_antigravity_prefers_language_server_with_csrf():
    ps_output = (
        "100 /Users/me/.local/bin/agy --continue\n"
        "200 /Applications/Antigravity IDE.app/.../language_server_macos_arm "
        "--csrf_token=abc123 --extension_server_port=9999\n"
    )
    with patch("commands.quota_cmd.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ps_output
        info = detect_antigravity_process()
    assert info["pid"] == 200
    assert info["csrf_token"] == "abc123"


def test_get_listening_ports_uses_lsof_and_flag(monkeypatch):
    monkeypatch.setattr(
        "commands.quota_cmd.shutil.which",
        lambda path: path if path.endswith("lsof") else None,
    )
    monkeypatch.setattr(
        "commands.quota_cmd._cli_log_port_map",
        lambda pid: {"https": 62154, "http": 62155},
    )

    def fake_run(argv, **kwargs):
        assert "-a" in argv
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="agy 12345 neilo  12u  IPv4 0x0  0t0  TCP 127.0.0.1:62154 (LISTEN)\n"
            "agy 12345 neilo  13u  IPv4 0x0  0t0  TCP 127.0.0.1:62155 (LISTEN)\n",
            stderr="",
        )

    monkeypatch.setattr("commands.quota_cmd.subprocess.run", fake_run)
    ports = resolve_api_ports({"pid": 12345, "extension_port": None})
    assert ports == {"https": 62154, "http": 62155}
    assert get_listening_ports(12345) == [62155, 62154]


def test_detect_antigravity_process_missing():
    with patch("commands.quota_cmd.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "99999 /usr/bin/sleep 1\n"
        with pytest.raises(RuntimeError, match="not running"):
            detect_antigravity_process()


def test_cmd_quota_agy_success(capsys):
    sample = {
        "tool": "agy",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "source": "userStatus",
        "api_port": 1234,
        **parse_quota_response(SAMPLE_USER_STATUS, "userStatus"),
    }
    with patch.dict(QUOTA_TOOLS["agy"], {"fetch": lambda **_: sample}):
        rc = cmd_quota(["agy"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Antigravity (agy)" in out
    assert "Gemini Flash" in out


def test_cmd_quota_json(capsys):
    sample = {
        "tool": "agy",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "source": "userStatus",
        "api_port": 1234,
        **parse_quota_response(SAMPLE_USER_STATUS, "userStatus"),
    }
    with patch.dict(QUOTA_TOOLS["agy"], {"fetch": lambda **_: sample}):
        rc = cmd_quota(["agy"], as_json=True)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "agy"
    assert payload["models"][0]["label"] == "Gemini Flash"


def test_cmd_quota_unknown_tool(capsys):
    rc = cmd_quota(["bogus"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown tool" in err.lower()
