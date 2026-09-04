"""Tests for the repo-wide shared Python runtime and dependency groups."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))

import venv_exec  # noqa: E402
from venv_util import (  # noqa: E402
    BIN_VENV,
    _activate_group,
    _base_python,
    _ensure_venv,
    _group_requirements,
    _install_group,
    _requirements_digest,
    _venv_python,
    ensure_faster_whisper,
    ensure_group,
    ensure_image,
    ensure_kokoro,
    ensure_mlx_whisper,
    ensure_parakeet,
    ensure_whisper,
    group_dir,
    install_all,
    interpreter_key,
    python_bin,
    requirements_file,
    runtime_venv,
    uninstall,
)


def test_runtime_and_groups_live_under_repo_venv():
    assert BIN_VENV == _REPO / ".venv"
    assert runtime_venv() == BIN_VENV / "runtime"
    assert python_bin() == _venv_python(BIN_VENV / "runtime")


def test_requirements_files_exist():
    assert requirements_file("ai.txt").is_file()
    assert requirements_file("ai-mlx.txt").is_file()
    assert requirements_file("ai-fast.txt").is_file()
    assert requirements_file("dev.txt").is_file()
    assert requirements_file("b3t.txt").is_file()


def test_group_requirements_ai_fast_has_no_torch():
    req_files = _group_requirements("ai-fast")
    assert [f.name for f in req_files] == ["ai-fast.txt"]
    assert "torch" not in req_files[0].read_text(encoding="utf-8")


def test_uninstall_removes_shared_and_legacy_venvs(tmp_path):
    root = tmp_path / ".venv"
    root.mkdir()
    legacy = tmp_path / "b3t-venv"
    legacy.mkdir()
    with (
        patch("venv_util.BIN_VENV", root),
        patch("venv_util.LEGACY_VENVS", (legacy,)),
        patch("venv_util.ZIMAGE_LEGACY_REPO", tmp_path / "missing"),
    ):
        assert uninstall() == 0
    assert not root.exists()
    assert not legacy.exists()


def test_interpreter_key_includes_abi_and_platform():
    key = interpreter_key(Path(sys.executable))
    assert sys.implementation.cache_tag in key
    assert "-" in key


def test_base_python_prefers_uv_managed_312():
    result = subprocess.CompletedProcess(
        ["/opt/uv", "python", "find", "3.12"],
        0,
        stdout="/managed/python3.12\n",
    )
    with patch("venv_util.shutil.which", return_value="/opt/uv"), patch(
        "venv_util.subprocess.run", return_value=result
    ):
        assert _base_python() == "/managed/python3.12"


def test_runtime_with_different_interpreter_key_is_replaced(tmp_path):
    root = tmp_path / ".venv"
    replacement = root / "runtime" / "bin" / "python3"
    with (
        patch("venv_util.BIN_VENV", root),
        patch("venv_util._base_python", return_value="/managed/python3.12"),
        patch("venv_util._python_works", return_value=True),
        patch("venv_util.interpreter_key", side_effect=["cpython-313", "cpython-312"]),
        patch("venv_util._replace_runtime", return_value=replacement) as replace,
    ):
        assert _ensure_venv() == replacement
    replace.assert_called_once_with("/managed/python3.12")


def test_group_dir_is_keyed_by_interpreter(tmp_path):
    with patch("venv_util.BIN_VENV", tmp_path / ".venv"), patch(
        "venv_util.interpreter_key", return_value="cpython-313-test"
    ):
        assert group_dir("b3t", Path(sys.executable)) == (
            tmp_path / ".venv" / "groups" / "cpython-313-test" / "b3t"
        )


def test_group_dir_rejects_path_traversal():
    with pytest.raises(ValueError):
        group_dir("../b3t", Path(sys.executable))


def test_requirements_digest_changes_with_contents(tmp_path):
    req = tmp_path / "group.txt"
    req.write_text("requests\n", encoding="utf-8")
    first = _requirements_digest([req])
    req.write_text("requests==2.0\n", encoding="utf-8")
    assert _requirements_digest([req]) != first


def test_broken_runtime_is_rebuilt_without_removing_groups(tmp_path):
    root = tmp_path / ".venv"
    broken = _venv_python(root / "runtime")
    broken.parent.mkdir(parents=True)
    broken.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    broken.chmod(0o755)
    preserved = root / "groups" / "cpython-test" / "b3t" / "installed"
    preserved.parent.mkdir(parents=True)
    preserved.write_text("yes", encoding="utf-8")

    with patch("venv_util.BIN_VENV", root), patch(
        "venv_util._base_python", return_value=sys._base_executable
    ):
        python = _ensure_venv()

    assert python == _venv_python(root / "runtime")
    assert subprocess.run([str(python), "-c", ""], check=False).returncode == 0
    assert preserved.read_text(encoding="utf-8") == "yes"


def test_legacy_repo_venv_is_migrated_to_shared_layout(tmp_path):
    root = tmp_path / ".venv"
    root.mkdir()
    (root / "pyvenv.cfg").write_text("home = old-homebrew\n", encoding="utf-8")
    (root / "old-package").write_text("stale", encoding="utf-8")

    with patch("venv_util.BIN_VENV", root), patch(
        "venv_util._base_python", return_value=sys._base_executable
    ):
        python = _ensure_venv()

    assert subprocess.run([str(python), "-c", ""], check=False).returncode == 0
    assert not (root / "pyvenv.cfg").exists()
    assert not (root / "old-package").exists()


def test_ensure_image_reuses_matching_group(tmp_path):
    python = tmp_path / ".venv" / "runtime" / "bin" / "python3"
    directory = tmp_path / ".venv" / "groups" / "key" / "ai"
    with (
        patch("venv_util._ensure_venv", return_value=python),
        patch("venv_util._group_requirements", return_value=[]),
        patch("venv_util.group_dir", return_value=directory),
        patch("venv_util._group_ready", return_value=True),
        patch("venv_util._install_group") as install,
        patch("venv_util._activate_group") as activate,
    ):
        assert ensure_image() == python
    install.assert_not_called()
    activate.assert_called_once_with(directory)


def test_ensure_group_installs_unsatisfied_group(tmp_path):
    python = tmp_path / "runtime" / "bin" / "python3"
    directory = tmp_path / "groups" / "b3t"
    req = tmp_path / "b3t.txt"
    req.write_text("requests\n", encoding="utf-8")
    with (
        patch("venv_util._ensure_venv", return_value=python),
        patch("venv_util._group_requirements", return_value=[req]),
        patch("venv_util.group_dir", return_value=directory),
        patch("venv_util._group_ready", return_value=False),
        patch("venv_util._install_group") as install,
        patch("venv_util._activate_group"),
    ):
        assert ensure_group("b3t") == python
    install.assert_called_once()
    assert install.call_args.args[:3] == (python, "b3t", directory)


def test_install_group_uses_uv_target_and_atomic_swap(tmp_path):
    python = tmp_path / "runtime" / "bin" / "python3"
    directory = tmp_path / "groups" / "b3t"
    directory.mkdir(parents=True)
    (directory / "old").write_text("old", encoding="utf-8")
    req = tmp_path / "b3t.txt"
    req.write_text("requests\n", encoding="utf-8")
    seen: dict[str, list[str]] = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0)

    with patch("venv_util.shutil.which", return_value="/opt/uv"), patch(
        "venv_util.subprocess.run", side_effect=fake_run
    ):
        _install_group(python, "b3t", directory, [req], "digest")

    assert seen["argv"][:6] == [
        "/opt/uv",
        "pip",
        "install",
        "--python",
        str(python),
        "--target",
    ]
    assert seen["argv"][-2:] == ["-r", str(req)]
    assert not (directory / "old").exists()
    assert (directory / ".requirements.sha256").read_text().strip() == "digest"


def test_install_group_failure_preserves_previous_group(tmp_path):
    python = tmp_path / "runtime" / "bin" / "python3"
    directory = tmp_path / "groups" / "b3t"
    directory.mkdir(parents=True)
    previous = directory / "working"
    previous.write_text("yes", encoding="utf-8")
    req = tmp_path / "b3t.txt"
    req.write_text("requests\n", encoding="utf-8")

    with patch("venv_util.shutil.which", return_value=None), patch(
        "venv_util.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, [str(python), "-m", "pip"]),
    ), pytest.raises(subprocess.CalledProcessError):
        _install_group(python, "b3t", directory, [req], "digest")

    assert previous.read_text(encoding="utf-8") == "yes"
    assert not list(directory.parent.glob(".b3t-*.tmp"))


def test_activate_group_replaces_other_group_pythonpath(tmp_path):
    directory = tmp_path / "groups" / "ai"
    with patch.dict(os.environ, {"PYTHONPATH": "old-group"}, clear=False):
        _activate_group(directory)
        assert os.environ["PYTHONPATH"].split(os.pathsep)[0] == str(directory)
        assert "old-group" not in os.environ["PYTHONPATH"]
        assert os.environ["PYTHONNOUSERSITE"] == "1"


def test_ensure_functions_are_thin_wrappers():
    assert ensure_kokoro.__module__ == "venv_util"
    assert ensure_whisper.__module__ == "venv_util"
    assert ensure_mlx_whisper.__module__ == "venv_util"
    assert ensure_parakeet.__module__ == "venv_util"
    assert ensure_faster_whisper.__module__ == "venv_util"


@pytest.mark.parametrize(
    ("system", "machine", "has_mlx"),
    [("Darwin", "arm64", True), ("Linux", "x86_64", False)],
)
def test_install_all_platform_groups(tmp_path, system, machine, has_mlx):
    python = _venv_python(tmp_path / ".venv" / "runtime")
    groups: list[str] = []

    def fake_ensure(group: str, probe: str | None = None):
        groups.append(group)
        return python

    with (
        patch("venv_util.BIN_VENV", tmp_path / ".venv"),
        patch("venv_util.ensure_group", side_effect=fake_ensure),
        patch("venv_util.platform.system", return_value=system),
        patch("venv_util.platform.machine", return_value=machine),
    ):
        assert install_all() == python
    assert ("ai-mlx" in groups) is has_mlx
    assert "ai-fast" in groups


def test_venv_exec_uses_group_runtime(tmp_path):
    python = tmp_path / "runtime" / "bin" / "python3"
    script = tmp_path / "app.py"
    script.write_text("", encoding="utf-8")
    with patch("venv_exec.ensure_group", return_value=python) as ensure, patch(
        "venv_exec.os.execvpe"
    ) as execvpe:
        assert venv_exec.main(["b3t", str(script), "--help"]) == 0
    ensure.assert_called_once_with("b3t", probe="import openpyxl, requests")
    assert execvpe.call_args.args[0] == str(python)
    assert execvpe.call_args.args[1] == [
        str(python),
        str(script.resolve()),
        "--help",
    ]


def test_venv_exec_supports_python_module_command(tmp_path):
    python = tmp_path / "runtime" / "bin" / "python3"
    with patch("venv_exec.ensure_group", return_value=python), patch(
        "venv_exec.os.execvpe"
    ) as execvpe:
        assert venv_exec.main(["dev", "--", "-m", "pytest", "-q"]) == 0
    assert execvpe.call_args.args[1] == [str(python), "-m", "pytest", "-q"]


def test_venv_exec_rejects_empty_python_command():
    assert venv_exec.main(["dev", "--"]) == 2
