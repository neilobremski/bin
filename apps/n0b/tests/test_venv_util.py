"""Tests for repo-wide shared venv (lib/venv_util.py)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))

from venv_util import (  # noqa: E402
    BIN_VENV,
    ensure_image,
    ensure_kokoro,
    ensure_mlx_whisper,
    ensure_parakeet,
    ensure_whisper,
    requirements_file,
    uninstall,
)


def test_venv_lives_at_repo_root():
    assert BIN_VENV == _REPO / ".venv"
    assert BIN_VENV.name == ".venv"


def test_requirements_files_exist():
    assert requirements_file("ai.txt").is_file()
    assert requirements_file("ai-mlx.txt").is_file()
    assert requirements_file("dev.txt").is_file()
    assert requirements_file("b3t.txt").is_file()


def test_uninstall_removes_shared_venv(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    legacy = tmp_path / "kokoro-venv"
    legacy.mkdir()
    with patch("venv_util.BIN_VENV", venv), patch(
        "venv_util.LEGACY_VENVS", (legacy,)
    ), patch("venv_util.ZIMAGE_LEGACY_REPO", tmp_path / "missing"):
        assert uninstall() == 0
    assert not venv.exists()
    assert not legacy.exists()


def test_ensure_image_reuses_existing_venv(tmp_path):
    py = tmp_path / ".venv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    with patch("venv_util.BIN_VENV", tmp_path / ".venv"), patch(
        "venv_util._probe", return_value=True
    ), patch("venv_util._pip_install_file") as install:
        assert ensure_image() == py
    install.assert_not_called()


def test_ensure_functions_are_thin_wrappers():
    assert ensure_kokoro.__module__ == "venv_util"
    assert ensure_whisper.__module__ == "venv_util"
    assert ensure_mlx_whisper.__module__ == "venv_util"
    assert ensure_parakeet.__module__ == "venv_util"


def test_install_all_includes_ai_mlx_on_apple_silicon(tmp_path):
    from venv_util import install_all

    py = tmp_path / ".venv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    groups: list[str] = []

    def fake_ensure(group: str, probe: str | None = None):
        groups.append(group)
        return py

    with (
        patch("venv_util.BIN_VENV", tmp_path / ".venv"),
        patch("venv_util.ensure_group", side_effect=fake_ensure),
        patch("venv_util.platform.system", return_value="Darwin"),
        patch("venv_util.platform.machine", return_value="arm64"),
    ):
        assert install_all() == py
    assert "ai-mlx" in groups


def test_install_all_skips_ai_mlx_off_apple(tmp_path):
    from venv_util import install_all

    py = tmp_path / ".venv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    groups: list[str] = []

    def fake_ensure(group: str, probe: str | None = None):
        groups.append(group)
        return py

    with (
        patch("venv_util.BIN_VENV", tmp_path / ".venv"),
        patch("venv_util.ensure_group", side_effect=fake_ensure),
        patch("venv_util.platform.system", return_value="Linux"),
        patch("venv_util.platform.machine", return_value="x86_64"),
    ):
        assert install_all() == py
    assert "ai-mlx" not in groups
