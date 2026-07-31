"""Tests for n0b ai research + --fanout (HTTP stubbed; no live API)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

N0B_PY = Path(__file__).resolve().parents[1] / "n0b.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli import build_parser, main  # noqa: E402
from research import (  # noqa: E402
    run_research,
    run_research_fanout,
    run_research_plan_only,
    mmr_select,
    _get_hash,
    _max_tool_calls,
)


def run_n0b(*args: str):
    import subprocess

    return subprocess.run(
        [sys.executable, str(N0B_PY), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _msg(text: str, usage: dict | None = None) -> dict:
    out: dict = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }
    if usage is not None:
        out["usage"] = usage
    return out


def _plan_payload(n_candidates: int = 12) -> dict:
    candidates = []
    angles = [
        ("mechanism", "how does X work internally"),
        ("evidence", "empirical evidence supporting X"),
        ("counter-evidence", "studies that contradict X"),
        ("adoption", "who uses X in practice"),
        ("risks", "failure modes and risks of X"),
        ("alternatives", "alternatives to X compared"),
        ("history", "historical development of X"),
        ("cost", "cost economics of deploying X"),
        ("regulation", "regulatory status of X"),
        ("ethics", "ethical concerns around X"),
        ("measurement", "how X is measured evaluated"),
        ("ecosystem", "tooling ecosystem around X"),
        ("scalability", "scalability limits of X"),
        ("security", "security properties of X"),
        ("ux", "user experience tradeoffs of X"),
        ("future", "roadmap future of X"),
    ]
    for angle, seed in angles[:n_candidates]:
        candidates.append({"angle": angle, "seed_query": seed})
    return {
        "brief": "A short planning brief about researching X across dimensions.",
        "candidates": candidates,
    }


def test_research_help_shows_fanout_and_plan_only():
    proc = run_n0b("ai", "research", "--help")
    assert proc.returncode == 0
    assert "--fanout" in proc.stdout
    assert "--plan-only" in proc.stdout


def test_ai_help_lists_research():
    proc = run_n0b("ai", "--help")
    assert proc.returncode == 0
    assert "research" in proc.stdout


def test_fanout_parses_before_remainder_prompt():
    parser = build_parser()
    args = parser.parse_args(["ai", "research", "--fanout", "3", "what", "is", "X"])
    assert args.fanout == 3
    assert args.prompt == ["what", "is", "X"]


def test_fanout_absent_leaves_prompt_only():
    parser = build_parser()
    args = parser.parse_args(["ai", "research", "what", "is", "X"])
    assert args.fanout is None
    assert args.prompt == ["what", "is", "X"]


def test_fanout_bare_defaults_to_four_via_main():
    with patch("commands.ai_research_cmd.run_research_fanout", return_value=0) as fanout, patch(
        "commands.ai_research_cmd.run_research", return_value=0
    ) as single:
        rc = main(["ai", "research", "--fanout", "alpha", "beta"])
    assert rc == 0
    fanout.assert_called_once()
    assert fanout.call_args[0][1] == 4
    single.assert_not_called()


def test_fanout_one_dispatches_single_shot():
    with patch("commands.ai_research_cmd.run_research", return_value=0) as single, patch(
        "commands.ai_research_cmd.run_research_fanout", return_value=0
    ) as fanout:
        rc = main(["ai", "research", "--fanout", "1", "alpha", "beta"])
    assert rc == 0
    single.assert_called_once_with(["alpha", "beta"])
    fanout.assert_not_called()


def test_ai_research_requires_prompt():
    proc = run_n0b("ai", "research")
    assert proc.returncode == 2
    assert "Usage: n0b ai research" in proc.stderr


def test_fanout_requires_prompt():
    proc = run_n0b("ai", "research", "--fanout", "3")
    assert proc.returncode == 2
    assert "Usage: n0b ai research" in proc.stderr


def test_mmr_skips_near_duplicate_candidates():
    candidates = [
        {"angle": "mechanism A", "seed_query": "how does quantum entanglement work"},
        {"angle": "mechanism B", "seed_query": "how does quantum entanglement work exactly"},
        {"angle": "economics", "seed_query": "market cost of quantum computing hardware"},
        {"angle": "regulation", "seed_query": "export control laws for quantum tech"},
        {"angle": "mechanism C", "seed_query": "how quantum entanglement works in detail"},
    ]
    selected = mmr_select(candidates, 3, lambda_=0.0)
    assert len(selected) == 3
    seeds = [s["seed_query"] for s in selected]
    # First is always kept; the near-paraphrases of entanglement should not
    # occupy both remaining slots when diverse alternatives exist.
    entanglementish = [
        s for s in seeds if "entanglement" in s or "entanglement" in s
    ]
    assert len(entanglementish) <= 1
    assert any("market cost" in s or "export control" in s for s in seeds)


def test_max_tool_calls_scales_down():
    assert _max_tool_calls(1) == 50
    assert _max_tool_calls(4) == 12
    assert _max_tool_calls(8) == 10  # floor


def test_single_shot_untouched(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    submitted = {"id": "resp_single", "status": "queued"}
    completed = {
        "id": "resp_single",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
    }

    with (
        patch("research._call_openai", return_value=submitted) as submit,
        patch("research._check_status", return_value=completed) as poll,
        patch("research.time.sleep") as sleep,
    ):
        rc = run_research(["single", "shot", "prompt"])
    assert rc == 0
    submit.assert_called_once()
    poll.assert_called_once_with("sk-test", "resp_single")
    sleep.assert_not_called()
    out = capsys.readouterr().out
    assert json.loads(out) == completed
    files = list((tmp_path / ".files" / "research").glob("*.json"))
    assert len(files) == 1
    assert not files[0].name.startswith("fanout")


def test_fanout_one_byte_identical_to_single_shot(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    submitted = {"id": "resp_single", "status": "queued"}
    completed = {
        "id": "resp_single",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
    }

    with (
        patch("research._call_openai", return_value=submitted),
        patch("research._check_status", return_value=completed),
        patch("research.time.sleep"),
    ):
        rc1 = run_research(["same", "prompt"])
    out1 = capsys.readouterr().out

    # Reset cache so second path submits again the same way.
    cache = tmp_path / ".files" / "research"
    for f in cache.glob("*.json"):
        f.unlink()

    with (
        patch("research._call_openai", return_value=submitted),
        patch("research._check_status", return_value=completed),
        patch("research.time.sleep"),
    ):
        rc2 = run_research_fanout(["same", "prompt"], 1)
    out2 = capsys.readouterr().out

    assert rc1 == rc2 == 0
    assert out1 == out2


def test_plan_only_submits_zero_research_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    plan = _msg(
        json.dumps(_plan_payload()),
        usage={"input_tokens": 50, "output_tokens": 80},
    )

    with (
        patch("research._call_cheap", return_value=plan) as cheap,
        patch("research._call_openai") as submit,
    ):
        rc = run_research_plan_only(["what", "is", "X"], 4)

    assert rc == 0
    submit.assert_not_called()
    cheap.assert_called_once()
    out = capsys.readouterr().out
    assert "# Research plan" in out
    assert "## Brief" in out
    assert "seed_query:" in out


def test_plan_only_via_fanout_kwarg(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    plan = _msg(json.dumps(_plan_payload()))

    with (
        patch("research._call_cheap", return_value=plan),
        patch("research._call_openai") as submit,
    ):
        rc = run_research_fanout(["q"], 3, plan_only=True)

    assert rc == 0
    submit.assert_not_called()


def test_fanout_produces_manifest_reports_and_merged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    plan_obj = _plan_payload()
    # Force MMR to pick the first 3 diverse angles from the payload.
    decomp = _msg(
        json.dumps(plan_obj),
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    merge_body = (
        "# Merged research\n\n## Summary\nX works.\n\n"
        "## Findings\n"
        "- Mechanism is Y [report-01.md | https://example.com/a]\n\n"
        "## Contradictions\n"
        "- none noted\n\n"
        "## Citations\n"
        "- https://example.com/a (corroboration=1)\n"
    )
    merge = _msg(merge_body, usage={"input_tokens": 200, "output_tokens": 80})
    cite_check = _msg(
        "## Citation check\n- OK: Mechanism is Y\n",
        usage={"input_tokens": 40, "output_tokens": 20},
    )

    cheap_calls: list[str] = []

    def fake_cheap(_key: str, prompt: str) -> dict:
        cheap_calls.append(prompt)
        if "planning a multi-angle" in prompt or "candidate" in prompt.lower():
            return decomp
        if "Citation check" in prompt:
            return cite_check
        return merge

    submit_count = {"n": 0}

    def fake_submit(_key: str, prompt: str, *, max_tool_calls: int | None = None) -> dict:
        submit_count["n"] += 1
        rid = f"resp_{submit_count['n']}"
        return {"id": rid, "status": "queued"}

    def fake_status(_key: str, response_id: str) -> dict:
        return {
            "id": response_id,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": f"Findings for {response_id}. https://example.com/{response_id}",
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

    with (
        patch("research._call_cheap", side_effect=fake_cheap),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=fake_status),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["what", "is", "X"], 3)

    assert rc == 0
    prompt_hash = _get_hash("what is X")
    run_dir = tmp_path / ".files" / "research" / f"fanout-{prompt_hash}"
    assert run_dir.is_dir()
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "merged.md").is_file()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["n"] == 3
    assert "brief" in manifest
    assert len(manifest["selected"]) == 3
    for i in range(1, 4):
        assert (run_dir / f"job-{i}.json").is_file()
        assert (run_dir / f"job-{i}-result.json").is_file()
        assert (run_dir / f"report-{i:02d}.md").is_file()
    merged = (run_dir / "merged.md").read_text()
    assert "## Contradictions" in merged
    assert "## Citation check" in merged
    out = capsys.readouterr()
    assert str(run_dir) in out.out
    assert "estimate" in out.err.lower()
    assert submit_count["n"] == 3
    # plan + merge + citation-check
    assert len(cheap_calls) == 3
    # Sequential submit — no threads; max_tool_calls scaled.
    assert _max_tool_calls(3) == 16


def test_fanout_resumes_without_resubmitting_completed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    selected = [
        {"angle": "Angle A", "seed_query": "seed a unique alpha"},
        {"angle": "Angle B", "seed_query": "seed b unique beta"},
    ]
    prompt = "topic question"
    prompt_hash = _get_hash(prompt)
    run_dir = tmp_path / ".files" / "research" / f"fanout-{prompt_hash}"
    run_dir.mkdir(parents=True)

    sub_prompts = [f"sub for {s['angle']}" for s in selected]
    completed_body = {
        "id": "resp_a",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "done A https://ex.test/a",
                    }
                ],
            }
        ],
    }
    (run_dir / "job-1.json").write_text(json.dumps({"id": "resp_a", "status": "queued"}))
    (run_dir / "job-1-result.json").write_text(json.dumps(completed_body))
    (run_dir / "job-2.json").write_text(json.dumps({"id": "resp_b", "status": "queued"}))

    manifest = {
        "prompt": prompt,
        "n": 2,
        "model": "gpt-5.6-sol",
        "brief": "brief",
        "selected": selected,
        "sub_prompts": sub_prompts,
        "max_tool_calls": 25,
        "jobs": [
            {
                "index": 1,
                "key": "k1",
                "id": "resp_a",
                "question": "Angle A",
                "state": "completed",
                "result_file": "job-1-result.json",
            },
            {
                "index": 2,
                "key": "k2",
                "id": "resp_b",
                "question": "Angle B",
                "state": "submitted",
            },
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest) + "\n")

    merge = _msg(
        "# Merged research\n\n## Summary\nok\n\n## Contradictions\n- none\n",
        usage={"input_tokens": 1, "output_tokens": 1},
    )
    cite = _msg("## Citation check\n- OK\n")

    def fake_cheap(_key: str, prompt: str) -> dict:
        if "Citation check" in prompt:
            return cite
        return merge

    def fake_status(_key: str, response_id: str) -> dict:
        return {
            "id": response_id,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": f"done {response_id} https://ex.test/{response_id}",
                        }
                    ],
                }
            ],
        }

    with (
        patch("research._call_openai") as submit_mock,
        patch("research._call_cheap", side_effect=fake_cheap),
        patch("research._check_status", side_effect=fake_status),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(prompt.split(), 2)

    assert rc == 0
    submit_mock.assert_not_called()
    err = capsys.readouterr().err
    assert "Resuming" in err
    assert "skip submit" in err or "already completed" in err


def test_partial_merge_on_timeout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)
    monkeypatch.setattr("research.JOB_TIMEOUT_S", 0)

    plan_obj = _plan_payload()
    decomp = _msg(json.dumps(plan_obj), usage={"input_tokens": 10, "output_tokens": 10})
    merge = _msg(
        "# Merged research\n\n## Summary\npartial\n\n## Contradictions\n- none\n",
        usage={"input_tokens": 10, "output_tokens": 10},
    )
    cite = _msg("## Citation check\n- OK\n")

    def fake_cheap(_key: str, prompt: str) -> dict:
        if "planning a multi-angle" in prompt or "candidate" in prompt.lower():
            return decomp
        if "Citation check" in prompt:
            return cite
        return merge

    submit_ids = []

    def fake_submit(_key: str, prompt: str, *, max_tool_calls: int | None = None) -> dict:
        rid = f"resp_{len(submit_ids) + 1}"
        submit_ids.append(rid)
        return {"id": rid, "status": "queued"}

    def fake_status(_key: str, response_id: str) -> dict:
        # First two complete; third stays queued → times out (JOB_TIMEOUT_S=0).
        if response_id in ("resp_1", "resp_2"):
            return {
                "id": response_id,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": f"ok {response_id} https://ex.test/{response_id}",
                            }
                        ],
                    }
                ],
            }
        return {"id": response_id, "status": "queued"}

    with (
        patch("research._call_cheap", side_effect=fake_cheap),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=fake_status),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["what", "is", "X"], 3)

    # Partial success → non-zero, but merge happened.
    assert rc == 1
    prompt_hash = _get_hash("what is X")
    run_dir = tmp_path / ".files" / "research" / f"fanout-{prompt_hash}"
    merged = (run_dir / "merged.md").read_text()
    assert "## Missing sub-questions" in merged
    assert "timed out" in merged
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert len(manifest["missing"]) == 1
    assert manifest["missing"][0]["index"] == 3


def test_contradictions_surfaced_not_resolved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    plan_obj = _plan_payload()
    decomp = _msg(json.dumps(plan_obj))
    merge_with_contradiction = _msg(
        "# Merged research\n\n## Summary\nDisagreement on safety.\n\n"
        "## Findings\n"
        "- Report 1 says X is safe [report-01.md | https://a.test]\n"
        "- Report 2 says X is unsafe [report-02.md | https://b.test]\n\n"
        "## Contradictions\n"
        "- Safety: report-01 says safe [report-01.md | https://a.test]; "
        "report-02 says unsafe [report-02.md | https://b.test]. "
        "Both sides retained; no winner chosen.\n"
    )
    cite = _msg("## Citation check\n- OK\n")

    def fake_cheap(_key: str, prompt: str) -> dict:
        if "planning a multi-angle" in prompt or "candidate" in prompt.lower():
            return decomp
        if "Citation check" in prompt:
            return cite
        assert "do NOT pick a winner" in prompt or "SURFACING" in prompt
        assert "Contradictions" in prompt
        return merge_with_contradiction

    n_sub = {"n": 0}

    def fake_submit(_key: str, prompt: str, *, max_tool_calls: int | None = None) -> dict:
        n_sub["n"] += 1
        return {"id": f"resp_{n_sub['n']}", "status": "queued"}

    def fake_status(_key: str, response_id: str) -> dict:
        texts = {
            "resp_1": "X is safe. https://a.test",
            "resp_2": "X is unsafe. https://b.test",
        }
        return {
            "id": response_id,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": texts.get(response_id, "ok https://c.test"),
                        }
                    ],
                }
            ],
        }

    with (
        patch("research._call_cheap", side_effect=fake_cheap),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=fake_status),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["safety", "of", "X"], 2)

    assert rc == 0
    prompt_hash = _get_hash("safety of X")
    merged = (
        tmp_path / ".files" / "research" / f"fanout-{prompt_hash}" / "merged.md"
    ).read_text()
    assert "## Contradictions" in merged
    assert "safe" in merged.lower() and "unsafe" in merged.lower()
    # Must not collapse to a single resolved verdict in the contradictions section.
    contra = merged.split("## Contradictions", 1)[1].split("##")[0]
    assert "safe" in contra.lower() and "unsafe" in contra.lower()


def test_malformed_decomposer_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    bad = _msg("sorry I cannot help with that")

    with (
        patch("research._call_cheap", return_value=bad),
        patch("research._call_openai") as submit,
    ):
        rc = run_research_fanout(["what", "is", "X"], 3)

    assert rc == 1
    submit.assert_not_called()
    err_obj = json.loads(capsys.readouterr().out)
    assert "error" in err_obj


def test_malformed_decomposer_too_few_candidates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    bad = _msg(
        json.dumps(
            {
                "brief": "tiny brief",
                "candidates": [
                    {"angle": "only one", "seed_query": "only one query"},
                ],
            }
        )
    )

    with patch("research._call_cheap", return_value=bad):
        rc = run_research_fanout(["q"], 3)

    assert rc == 1
    err_obj = json.loads(capsys.readouterr().out)
    assert "candidates" in err_obj["error"]


def test_cli_dispatches_fanout(tmp_path, monkeypatch):
    with patch("commands.ai_research_cmd.run_research_fanout", return_value=0) as fanout, patch(
        "commands.ai_research_cmd.run_research", return_value=0
    ) as single:
        rc = main(["ai", "research", "--fanout", "4", "alpha", "beta"])
    assert rc == 0
    fanout.assert_called_once()
    assert fanout.call_args[0] == (["alpha", "beta"], 4)
    assert fanout.call_args[1].get("plan_only") is False
    single.assert_not_called()


def test_cli_dispatches_plan_only():
    with patch("commands.ai_research_cmd.run_research_fanout", return_value=0) as fanout, patch(
        "commands.ai_research_cmd.run_research", return_value=0
    ) as single:
        rc = main(["ai", "research", "--fanout", "4", "--plan-only", "alpha"])
    assert rc == 0
    fanout.assert_called_once()
    assert fanout.call_args[1].get("plan_only") is True
    single.assert_not_called()


def test_cli_dispatches_single_shot():
    with patch("commands.ai_research_cmd.run_research", return_value=0) as single, patch(
        "commands.ai_research_cmd.run_research_fanout", return_value=0
    ) as fanout:
        rc = main(["ai", "research", "alpha", "beta"])
    assert rc == 0
    single.assert_called_once_with(["alpha", "beta"])
    fanout.assert_not_called()


def test_fanout_clamp_noted_on_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    plan = _msg(json.dumps(_plan_payload(16)))

    with (
        patch("research._call_cheap", return_value=plan),
        patch("research._call_openai") as submit,
    ):
        rc = run_research_plan_only(["q"], 99)

    assert rc == 0
    submit.assert_not_called()
    err = capsys.readouterr().err
    assert "Clamped" in err
    assert "8" in err
