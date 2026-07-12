"""node→root stamping — `--node` works from anywhere, CWD finds the node."""
from __future__ import annotations

import state
from r4t import main as r4t_main

NODE = "acme"


def test_stamp_and_read_root(r4t_home, repo):
    assert state.read_root(NODE) is None
    state.stamp_root(NODE, repo)
    assert state.read_root(NODE) == repo
    state.stamp_root(NODE, repo)  # idempotent rewrite
    assert state.read_root(NODE) == repo


def test_node_for_root_matches_subdirs(r4t_home, repo, tmp_path):
    state.stamp_root(NODE, repo)
    state.stamp_root("other", tmp_path / "elsewhere")
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    assert state.node_for_root(sub) == NODE
    assert state.node_for_root(repo) == NODE
    assert state.node_for_root(tmp_path) is None


def test_dispatch_stamps_root(r4t_home, repo, rig_config, fake_harness):
    rc = r4t_main([
        "dispatch",
        "--root", str(repo),
        "--from", "gerry",
        "--to", f"{NODE}:phil",
        "--message", "stamp me",
        "--rig-config", str(rig_config),
        "--no-notify",
    ])
    assert rc == 0
    assert state.read_root(NODE) == repo


def test_status_from_unrelated_cwd_uses_stamped_root(
    r4t_home, repo, rig_config, tmp_path, monkeypatch, capsys
):
    state.stamp_root(NODE, repo)
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rc = r4t_main(["status", "--node", NODE, "--rig-config", str(rig_config)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "roster not found" not in out
    assert "Health" in out
    assert "Gerry" in out


def test_bare_node_resolves_from_cwd(
    r4t_home, repo, rig_config, monkeypatch, capsys
):
    state.stamp_root(NODE, repo)
    state.team_dir("other").mkdir(parents=True)  # two teams: ambiguity is real
    monkeypatch.chdir(repo)
    rc = r4t_main(["status", "--rig-config", str(rig_config)])
    assert rc == 0
    assert "team: acme" in capsys.readouterr().out


def test_bare_node_still_errors_outside_any_root(
    r4t_home, repo, tmp_path, monkeypatch, capsys
):
    state.team_dir(NODE).mkdir(parents=True)
    state.team_dir("other").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    rc = r4t_main(["status"])
    assert rc == 2
    assert "pass --node" in capsys.readouterr().err
