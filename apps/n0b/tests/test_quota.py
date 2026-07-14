"""n0b quota command tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import commands.quota_cmd as quota_cmd  # noqa: E402
from commands.quota_cmd import (  # noqa: E402
    QUOTA_TOOLS,
    cmd_quota,
    detect_antigravity_process,
    fetch_agy_quota,
    fetch_agy_quota_cloud,
    get_listening_ports,
    group_model_quotas,
    load_snapshot,
    parse_quota_response,
    resolve_api_ports,
    save_snapshot,
    snapshot_age_text,
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
            },
            {
                "label": "Claude Sonnet 4.6 (Thinking)",
                "model": "claude-sonnet",
                "quotaInfo": {
                    "remainingFraction": 1.0,
                    "resetTime": "2099-01-01T15:00:00Z",
                },
            },
        ],
    },
}


def test_parse_quota_response_user_status():
    parsed = parse_quota_response(SAMPLE_USER_STATUS, "userStatus")
    assert parsed["account"]["plan"] == "Pro"
    assert parsed["available_prompt_credits"] == 42
    assert len(parsed["models"]) == 2
    gemini = next(m for m in parsed["models"] if "Gemini" in m["label"])
    assert gemini["buckets"][0]["remaining_fraction"] == pytest.approx(0.56)


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


def test_group_model_quotas_matches_agy_pools():
    parsed = parse_quota_response(SAMPLE_USER_STATUS, "userStatus")
    assert len(parsed["groups"]) == 2
    gemini = parsed["groups"][0]
    assert gemini["name"] == "GEMINI MODELS"
    weekly = next(b for b in gemini["buckets"] if b["label"] == "Weekly Limit")
    five_hour = next(b for b in gemini["buckets"] if b["label"] == "Five Hour Limit")
    assert weekly["remaining_fraction"] == pytest.approx(0.56)
    assert five_hour["remaining_fraction"] is None


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
    assert "GEMINI MODELS" in out
    assert "Weekly Limit" in out


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
    assert payload["groups"][0]["name"] == "GEMINI MODELS"


def test_cmd_quota_unknown_tool(capsys):
    rc = cmd_quota(["bogus"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown tool" in err.lower()


def _sample_payload():
    return {
        "tool": "agy",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "origin": "live",
        "source": "userStatus",
        "api_port": 1234,
        **parse_quota_response(SAMPLE_USER_STATUS, "userStatus"),
    }


def test_snapshot_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(quota_cmd, "SNAPSHOT_PATH", tmp_path / "quota-agy.json")
    payload = _sample_payload()
    payload["raw"] = {"secret": "stripped"}
    save_snapshot(payload)
    snap = load_snapshot()
    assert snap is not None
    assert "raw" not in snap["payload"]
    assert snap["payload"]["account"]["plan"] == "Pro"
    assert snap["saved_at"]


def test_snapshot_age_text():
    with patch("commands.quota_cmd._utc_now") as now:
        from datetime import datetime, timezone

        now.return_value = datetime(2026, 1, 2, 3, 43, tzinfo=timezone.utc)
        assert snapshot_age_text("2026-01-02T03:00:00+00:00") == "43m"
        assert snapshot_age_text("2026-01-02T01:38:00+00:00") == "2h 5m"
        assert snapshot_age_text("2025-12-30T01:00:00+00:00") == "3d 2h"


def test_fetch_live_success_writes_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(quota_cmd, "SNAPSHOT_PATH", tmp_path / "quota-agy.json")
    monkeypatch.setattr(quota_cmd, "_fetch_agy_live", lambda **_: _sample_payload())
    result = fetch_agy_quota()
    assert result["origin"] == "live"
    assert load_snapshot() is not None


def test_fetch_falls_back_to_cloud(tmp_path, monkeypatch):
    monkeypatch.setattr(quota_cmd, "SNAPSHOT_PATH", tmp_path / "quota-agy.json")

    def live_down(**_):
        raise RuntimeError("not running")

    cloud = {**_sample_payload(), "origin": "cloud", "source": "loadCodeAssist"}
    monkeypatch.setattr(quota_cmd, "_fetch_agy_live", live_down)
    monkeypatch.setattr(quota_cmd, "fetch_agy_quota_cloud", lambda **_: cloud)
    result = fetch_agy_quota()
    assert result["origin"] == "cloud"
    assert load_snapshot() is not None


def test_fetch_falls_back_to_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(quota_cmd, "SNAPSHOT_PATH", tmp_path / "quota-agy.json")
    save_snapshot(_sample_payload())

    def down(**_):
        raise RuntimeError("down")

    monkeypatch.setattr(quota_cmd, "_fetch_agy_live", down)
    monkeypatch.setattr(quota_cmd, "fetch_agy_quota_cloud", down)
    result = fetch_agy_quota()
    assert result["origin"] == "snapshot"
    assert result["as_of"]
    assert result["age"]
    assert result["account"]["plan"] == "Pro"


def test_fetch_error_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.setattr(quota_cmd, "SNAPSHOT_PATH", tmp_path / "quota-agy.json")

    def down(**_):
        raise RuntimeError("down")

    monkeypatch.setattr(quota_cmd, "_fetch_agy_live", down)
    monkeypatch.setattr(quota_cmd, "fetch_agy_quota_cloud", down)
    with pytest.raises(RuntimeError, match=r"\(try:"):
        fetch_agy_quota()


def test_fetch_cloud_result_without_models_prefers_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(quota_cmd, "SNAPSHOT_PATH", tmp_path / "quota-agy.json")
    save_snapshot(_sample_payload())

    def live_down(**_):
        raise RuntimeError("not running")

    cloud = {
        "tool": "agy",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "origin": "cloud",
        "source": "loadCodeAssist",
        "error": None,
        "account": {"email": None, "plan": "Pro"},
        "available_prompt_credits": None,
        "models": [],
        "groups": [],
    }
    monkeypatch.setattr(quota_cmd, "_fetch_agy_live", live_down)
    monkeypatch.setattr(quota_cmd, "fetch_agy_quota_cloud", lambda **_: cloud)
    result = fetch_agy_quota()
    assert result["origin"] == "snapshot"


def test_cloud_auth_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(quota_cmd, "OAUTH_CREDS_PATH", tmp_path / "missing.json")
    with pytest.raises(RuntimeError, match=r"Not logged in.*\(try: agy"):
        fetch_agy_quota_cloud()


def test_cloud_parses_load_code_assist(monkeypatch, tmp_path):
    creds = tmp_path / "oauth_creds.json"
    creds.write_text(json.dumps({"refresh_token": "r", "access_token": "a", "expiry_date": 4102444800000}))
    monkeypatch.setattr(quota_cmd, "OAUTH_CREDS_PATH", creds)

    class FakeResponse:
        def __init__(self, body):
            self._body = json.dumps(body).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    cloud_body = {
        "currentTier": {"id": "standard-tier", "name": "Gemini Code Assist"},
        "paidTier": {"id": "g1-pro-tier", "name": "Google One AI Pro"},
    }
    monkeypatch.setattr(
        quota_cmd.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse(cloud_body)
    )
    result = fetch_agy_quota_cloud()
    assert result["origin"] == "cloud"
    assert result["account"]["plan"] == "Google One AI Pro"
    assert result["models"] == []


def test_format_snapshot_source_line():
    payload = {**_sample_payload(), "origin": "snapshot", "age": "43m"}
    text = quota_cmd.format_agy_quota_text(payload)
    assert "as of 43m ago" in text
    assert "✓" in text


def test_cloud_client_pair_unconfigured(tmp_path, monkeypatch):
    creds = tmp_path / "oauth_creds.json"
    creds.write_text(json.dumps({"refresh_token": "r", "access_token": "expired", "expiry_date": 1}))
    monkeypatch.setattr(quota_cmd, "OAUTH_CREDS_PATH", creds)
    monkeypatch.setattr("commands.secrets_cmd.resolve", lambda name: None)
    with pytest.raises(RuntimeError, match=r"try: n0b secrets set GEMINI_OAUTH_CLIENT_ID"):
        fetch_agy_quota_cloud()
