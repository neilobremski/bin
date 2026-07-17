"""OS-level isolation — run_as and container variants (plans/ISOLATE-SPEC.md §7).

Isolation is a PER-ORG setting (the 2026-07-16 ruling): one Unix user or one
image serves an org's whole roster, so it lives in r4t-org.json, not on the
machine-global rig. The org's choice rides to run_harness through the turn env.

Wrapper argv is asserted EXACTLY; the prereq probe and the container kill run
against fake `sudo`/`docker` binaries put on PATH — no real sudo, docker, or
LLM. State stays under the tmp R4T_HOME the shared fixtures set; the live
~/.config/r4t is never touched.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

import isolate
import state
from dispatch import DispatchContext, drain, handle_message, run_harness
from isolate import Isolation
from org import ORG_CONFIG_NAME, check_org, load_org
from rig import Rig, load_rig_config
from roster import Member

NODE = "acme"


def _fake_bin(directory: Path, name: str, body: str) -> Path:
    """Write an executable Python stub named `name` into `directory`."""
    path = directory / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fakebin(tmp_path, monkeypatch):
    d = tmp_path / "fakebin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
    return d


class TestWrapRunAs:
    def test_exact_argv(self):
        argv = isolate.wrap_run_as(
            ["claude", "-p", "{hi}"], "agent-x", "/stg/dir", "/work/place"
        )
        assert argv == [
            "sudo", "-u", "agent-x", "bash", "--login", "-c",
            'export TELL_OUTBOX_DIR="$1"; cd "$2"; shift 2; exec "$@"',
            "_", "/stg/dir", "/work/place", "claude", "-p", "{hi}",
        ]

    def test_env_rides_as_positionals_not_a_command_string(self):
        argv = isolate.wrap_run_as(["h", "a b"], "u", "/s", "/w")
        # The bootstrap is a single -c argument; the harness argv follows as
        # discrete positionals, so a space in an arg can never re-split.
        assert argv[6] == 'export TELL_OUTBOX_DIR="$1"; cd "$2"; shift 2; exec "$@"'
        assert argv[-2:] == ["h", "a b"]


class TestBuildContainer:
    def test_exact_argv(self):
        argv = isolate.build_container_argv(
            ["claude", "-p", "{hi}"],
            "myimg:latest",
            name="r4t-acme-phil-42",
            staging_dir="/stg",
            workplace="/work",
            tell_outbox="/stg",
            client_dir="/opt/bin",
        )
        assert argv == [
            "docker", "run", "--rm", "--name", "r4t-acme-phil-42",
            "-v", "/work:/work",
            "-w", "/work",
            "-v", "/stg:/stg",
            "-e", "TELL_OUTBOX_DIR=/stg",
            "-v", "/opt/bin:/opt/bin:ro",
            "-e", "PATH=/opt/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "myimg:latest", "claude", "-p", "{hi}",
        ]

    def test_container_args_appended_verbatim_before_image(self):
        argv = isolate.build_container_argv(
            ["h", "{p}"], "img", name="n",
            staging_dir="/s", workplace="/w", tell_outbox="/s",
            container_args=["--gpus", "all", "-v", "/creds:/creds:ro"],
            client_dir="/c",
        )
        i = argv.index("img")
        assert argv[i - 4:i] == ["--gpus", "all", "-v", "/creds:/creds:ro"]
        assert argv[i:] == ["img", "h", "{p}"]

    def test_delivered_dir_mounts_read_only(self):
        argv = isolate.build_container_argv(
            ["h", "{p}"], "img", name="n",
            staging_dir="/s", workplace="/w", tell_outbox="/s",
            delivered_dir="/deliver", client_dir="/c",
        )
        assert "-v" in argv and "/deliver:/deliver:ro" in argv

    def test_container_name_deterministic_with_ts_and_slugs_bad_chars(self):
        assert isolate.container_name("ac me", "Phil/1", ts=7) == "r4t-ac-me-Phil-1-7"


class TestOrgConfigValidation:
    """Isolation now parses out of r4t-org.json (org.py), validated where
    `doorbell_check` is: load_org degrades to no isolation on a bad value,
    check_org reports it."""

    def _write(self, tmp_path, settings: dict) -> Path:
        (tmp_path / ORG_CONFIG_NAME).write_text(json.dumps(settings), encoding="utf-8")
        return tmp_path

    def test_both_set_is_config_error(self, tmp_path):
        self._write(tmp_path, {"run_as": "u", "container": "img"})
        assert any("mutually exclusive" in m for m in check_org(tmp_path))

    def test_both_set_degrades_to_no_isolation(self, tmp_path):
        self._write(tmp_path, {"run_as": "u", "container": "img"})
        assert not load_org(tmp_path).isolation.active  # fail closed: neither applies

    def test_container_args_without_container_errors(self, tmp_path):
        self._write(tmp_path, {"container_args": ["--gpus", "all"]})
        assert any(
            'container_args" set but "container" is not' in m for m in check_org(tmp_path)
        )

    def test_blank_run_as_errors(self, tmp_path):
        self._write(tmp_path, {"run_as": "   "})
        assert any("non-empty username" in m for m in check_org(tmp_path))

    def test_valid_run_as_parses(self, tmp_path):
        self._write(tmp_path, {"run_as": "agent-x"})
        org = load_org(tmp_path)
        assert check_org(tmp_path) == []
        assert org.isolation.run_as == "agent-x" and org.isolation.active

    def test_valid_container_parses_with_args(self, tmp_path):
        self._write(tmp_path, {"container": "img", "container_args": ["-v", "/c:/c:ro"]})
        org = load_org(tmp_path)
        assert check_org(tmp_path) == []
        assert org.isolation.container == "img"
        assert org.isolation.container_args == ["-v", "/c:/c:ro"]

    def test_absent_isolation_is_the_default(self, tmp_path):
        assert not load_org(tmp_path).isolation.active


class TestEnvRoundTrip:
    """The org's choice reaches run_harness through the turn env only — the
    run_fn contract stays (rig, prompt, cwd, env, variant)."""

    def test_run_as_round_trips(self):
        env = Isolation(run_as="agent-x").to_env()
        assert isolate.isolation_from_env(env).run_as == "agent-x"

    def test_container_and_args_round_trip(self):
        env = Isolation(container="img", container_args=["-v", "/c:/c:ro"]).to_env()
        got = isolate.isolation_from_env(env)
        assert got.container == "img" and got.container_args == ["-v", "/c:/c:ro"]

    def test_bare_org_adds_nothing_to_env(self):
        assert Isolation().to_env() == {}
        assert not isolate.isolation_from_env({}).active


class TestSharedDirAssertion:
    def _mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_writable_dir_gets_2770_setgid(self, tmp_path):
        d = tmp_path / "staging"
        isolate.assert_writable_shared_dir(d, os.getgid())
        assert self._mode(d) == 0o2770
        assert d.stat().st_gid == os.getgid()

    def test_readonly_dir_gets_2750_setgid(self, tmp_path):
        d = tmp_path / "delivered"
        isolate.assert_readonly_shared_dir(d, os.getgid())
        assert self._mode(d) == 0o2750

    def test_reasserts_after_tampering(self, tmp_path):
        d = tmp_path / "staging"
        isolate.assert_writable_shared_dir(d, os.getgid())
        d.chmod(0o700)  # an agent (or drift) narrows it
        assert self._mode(d) != 0o2770
        isolate.assert_writable_shared_dir(d, os.getgid())  # re-assert before the next turn
        assert self._mode(d) == 0o2770

    def test_unknown_group_still_sets_mode(self, tmp_path):
        d = tmp_path / "staging"
        isolate.assert_writable_shared_dir(d, None)  # gid None: skip chown, keep mode
        assert self._mode(d) == 0o2770


# ---------- dispatch-level: fail closed, breaker, kill-by-name ----------


ROSTER = textwrap.dedent(
    """\
    # Team

    ### Gerry
    - **Status:** AI
    - **Rig:** leader
    - **Leader:** yes

    ### Phil
    - **Status:** AI
    - **Rig:** junior-dev
    """
)


def _iso_config(tmp_path, fake_harness) -> Path:
    script, _out = fake_harness
    invoke = [sys.executable, str(script), "{prompt}"]
    payload = {
        "throttle": {"max_concurrent": 0, "min_seconds_between_turn_starts": 0},
        "cell_budget_max": 200,
        "cell_budget_earn_per_hour": 100,
        "leader": {"invoke": invoke, "timeout_seconds": 30, "budget_max": 100, "budget_earn_per_hour": 50},
        "junior-dev": {
            "invoke": invoke, "timeout_seconds": 30, "budget_max": 100,
            "budget_earn_per_hour": 50,
        },
        "pins": {"gerry": "leader"},
    }
    path = tmp_path / "iso-rigs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def iso_ctx_factory(r4t_home, tmp_path, fake_harness, tells):
    def make(isolation: dict | None = None) -> DispatchContext:
        root = tmp_path / "iso-repo"
        root.mkdir(exist_ok=True)
        (root / "ROSTER.md").write_text(ROSTER, encoding="utf-8")
        _sent, capture = tells
        return DispatchContext(
            root=root,
            node=NODE,
            roster_path=root / "ROSTER.md",
            config_path=_iso_config(tmp_path, fake_harness),
            tell_fn=capture,
            isolation=Isolation(**(isolation or {})),
        )

    return make


class TestRunAsProbeFailsClosed:
    def test_failed_grant_probe_fails_turn_and_requeues_and_trips_breaker(
        self, iso_ctx_factory, fakebin
    ):
        _fake_bin(fakebin, "sudo", "import sys\nsys.exit(1)\n")  # no NOPASSWD grant
        ctx = iso_ctx_factory({"run_as": "agent-x"})

        handle_message(ctx, "acme:gerry", "acme:phil", "do work", drain_after=False)
        ran = drain(ctx, run_fn=run_harness)

        assert ran == 1  # the turn ran and failed closed (not skipped)
        assert state.queue_depth(NODE, "phil") >= 1  # message returned to the queue
        assert state.read_meta(NODE, "phil")["consecutive_failures"] == 1  # breaker counts it

    def test_probe_error_surfaces_the_fix(self, fakebin):
        _fake_bin(fakebin, "sudo", "import sys\nsys.exit(1)\n")
        rig = Rig(name="junior-dev", invoke=["true", "{prompt}"])
        env = {"TELL_OUTBOX_DIR": "/tmp/s", **Isolation(run_as="agent-x").to_env()}
        code, out, _dur, timed = run_harness(rig, "p", Path("/tmp"), env=env)
        assert code == 126 and not timed
        assert "no passwordless sudo" in out and "docs/isolation.md" in out


class TestOrgIsolationAppliesToEveryRig:
    """One org setting wraps every member turn identically, whatever rig runs
    it — the whole point of moving the knob rig -> org."""

    def test_same_run_as_wraps_two_different_rigs_identically(self, tmp_path, fakebin):
        record = tmp_path / "sudo-argv.txt"
        _fake_bin(
            fakebin, "sudo",
            f"""
            import sys
            a = sys.argv[1:]
            # record only the real wrapped invoke (the bootstrap -c string), not
            # the two prereq probes; then exit 0 so probes pass and the run is a
            # no-op we can inspect.
            if "-c" in a and a[a.index("-c") + 1].startswith("export TELL_OUTBOX_DIR"):
                open({str(record)!r}, "a").write(repr(a) + "\\n")
            sys.exit(0)
            """,
        )
        env = dict(os.environ)  # keep PATH so the real wrapped `sudo` resolves the stub
        env["TELL_OUTBOX_DIR"] = str(tmp_path / "stg")
        env.update(Isolation(run_as="agent-x").to_env())
        leader = Rig(name="leader", invoke=["claude-harness", "-p", "{prompt}"])
        junior = Rig(name="junior", invoke=["codex-harness", "exec", "{prompt}"])

        run_harness(leader, "P", tmp_path, env=dict(env))
        run_harness(junior, "P", tmp_path, env=dict(env))

        lines = record.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        recorded = [eval(line) for line in lines]  # noqa: S307 — test-owned stub output
        # Both turns are wrapped by the SAME boundary (sudo -u agent-x ...); only
        # the trailing harness argv differs, proving isolation is rig-agnostic.
        for a in recorded:
            assert a[:5] == ["-u", "agent-x", "bash", "--login", "-c"]
        assert recorded[0][-3:] == ["claude-harness", "-p", "P"]
        assert recorded[1][-3:] == ["codex-harness", "exec", "P"]


class TestRigLevelIsolationIsGone:
    """Rig-level run_as/container ceased to exist (pre-v1 scorch-the-earth). A
    stray key in rigs.json follows rig.py's unknown-key convention: ignored,
    not an error — and it never wraps a turn."""

    def test_rig_run_as_and_container_keys_are_ignored_not_errors(self, tmp_path):
        path = tmp_path / "rigs.json"
        path.write_text(
            json.dumps(
                {"iso": {"invoke": ["h", "{prompt}"], "run_as": "u", "container": "img"}}
            ),
            encoding="utf-8",
        )
        config = load_rig_config(path)
        rig = config.rigs["iso"]
        assert rig.error is None  # unknown keys are ignored, not rejected
        assert not hasattr(rig, "run_as") and not hasattr(rig, "container")
        member = Member(name="Bob", rig="iso")
        resolved, err, _pinned = config.rig_for(member)
        assert resolved is not None and err is None  # the rig still runs; no isolation


class TestContainerTimeoutKill:
    def test_timeout_kills_container_by_name(self, tmp_path, fakebin, monkeypatch):
        record = tmp_path / "docker-kills.txt"
        _fake_bin(
            fakebin, "docker",
            f"""
            import os, sys, time
            args = sys.argv[1:]
            if args and args[0] == "run":
                time.sleep(30)
            elif args and args[0] == "kill":
                open({str(record)!r}, "a").write(args[1] + "\\n")
            """,
        )
        rig = Rig(name="c", invoke=["harness", "{prompt}"], timeout_seconds=0.5)
        env = dict(os.environ)
        env["TELL_OUTBOX_DIR"] = str(tmp_path / "stg")
        env["R4T_NODE"] = "acme"
        env["R4T_MEMBER"] = "phil"
        env.update(Isolation(container="img").to_env())

        _code, _out, _dur, timed_out = run_harness(rig, "p", tmp_path, env=env)

        assert timed_out
        killed = record.read_text(encoding="utf-8").split()
        assert len(killed) == 1
        assert killed[0].startswith("r4t-acme-phil-")


class TestStatusRowRendering:
    def test_isolation_tag(self):
        from r4t import _isolation_tag

        assert _isolation_tag(Isolation(run_as="agent-x")) == "[user:agent-x]"
        assert _isolation_tag(Isolation(container="img:1")) == "[container:img:1]"
        assert _isolation_tag(Isolation()) == ""

    def test_status_header_shows_the_org_boundary(self, r4t_home, tmp_path, fake_harness, capsys):
        # The badge is one org-level line now, not a per-rig tag.
        from r4t import main as r4t_main

        org_dir = tmp_path / "iso-repo"
        org_dir.mkdir()
        (org_dir / "ROSTER.md").write_text(ROSTER, encoding="utf-8")
        (org_dir / ORG_CONFIG_NAME).write_text(
            json.dumps({"run_as": "agent-x"}), encoding="utf-8"
        )
        state.stamp_root(NODE, org_dir)
        cfg = _iso_config(tmp_path, fake_harness)
        rc = r4t_main(["status", "--node", NODE, "--rig-config", str(cfg)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "isolation: [user:agent-x]" in out
