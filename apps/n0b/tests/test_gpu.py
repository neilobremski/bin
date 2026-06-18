"""GPU command tests."""
from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commands.gpu_cmd import mlx_available  # noqa: E402


def test_mlx_unavailable_off_darwin(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert mlx_available() is False


def test_mlx_available_on_apple_silicon(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert mlx_available() is True
