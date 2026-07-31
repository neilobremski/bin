"""Tests for n0b ai research + --fanout (HTTP stubbed; no live API)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

N0B_PY = Path(__file__).resolve().parents[1] / "n0b.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli import build_parser, main  # noqa: E402
from research import (  # noqa: E402
    FANOUT_AUTO,
    QPD_WARN_THRESHOLD,
    _citation_union,
    _get_hash,
    _max_tool_calls,
    _merge_quorum,
    _normalize_url,
    mmr_select,
    run_research,
    run_research_fanout,
    run_research_plan_only,
    select_with_adversarial,
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


def _plan_payload(
    n_candidates: int = 12,
    *,
    recommended_fanout: int = 4,
    adversarial: dict | None = None,
) -> dict:
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
    if adversarial is None:
        adversarial = {
            "angle": "adversarial-skeptic",
            "seed_query": "why X claims may be wrong or overstated",
        }
    return {
        "brief": "A short planning brief about researching X across dimensions.",
        "recommended_fanout": recommended_fanout,
        "candidates": candidates,
        "adversarial": adversarial,
    }


def _fake_status_factory(extra_output: list | None = None):
    def fake_status(_key: str, response_id: str) -> dict:
        output = list(extra_output or [])
        output.append(
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": f"Findings for {response_id}. https://example.com/{response_id}",
                    }
                ],
            }
        )
        return {
            "id": response_id,
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

    return fake_status


def _cheap_router(decomp, compose=None, attach=None, cite=None):
    """Route planner/sol text calls by prompt content."""
    compose = compose or _msg(
        "# Merged research\n\n## Summary\nok\n\n"
        "## Contradictions\n"
        "- A reports safe as of 2024-01-01; B reports unsafe as of 2025-06-01\n",
        usage={"input_tokens": 20, "output_tokens": 10},
    )
    attach = attach or _msg(
        "# Merged research\n\n## Summary\nok\n\n"
        "## Findings\n- claim [report-01.md | https://example.com/a]\n\n"
        "## Contradictions\n"
        "- A reports safe as of 2024-01-01; B reports unsafe as of 2025-06-01\n\n"
        "## Citations\n- https://example.com/a\n",
        usage={"input_tokens": 20, "output_tokens": 10},
    )
    cite = cite or _msg(
        "## Citation check\n- OK\n",
        usage={"input_tokens": 10, "output_tokens": 5},
    )

    def router(_key: str, prompt: str) -> dict:
        low = prompt.lower()
        if "planning a multi-angle" in low or (
            "recommended_fanout" in low and "candidates" in low
        ):
            return decomp
        if "citation check" in low:
            return cite
        if "citation re-attachment" in low or "re-attach" in low:
            return attach
        if "composition pass" in low or "compose one markdown" in low:
            return compose
        return compose

    return router


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


def test_fanout_bare_dispatches_auto_via_main():
    with patch("commands.ai_research_cmd.run_research_fanout", return_value=0) as fanout, patch(
        "commands.ai_research_cmd.run_research", return_value=0
    ) as single:
        rc = main(["ai", "research", "--fanout", "alpha", "beta"])
    assert rc == 0
    fanout.assert_called_once()
    assert fanout.call_args[0][1] == FANOUT_AUTO
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
    entanglementish = [s for s in seeds if "entanglement" in s]
    assert len(entanglementish) <= 1
    assert any("market cost" in s or "export control" in s for s in seeds)


def test_adversarial_always_present_even_when_lexically_near():
    # Adversarial seed is near the first topical seed — MMR would reject it.
    candidates = [
        {"angle": "safety-case", "seed_query": "is product X safe for consumers"},
        {"angle": "economics", "seed_query": "market cost of deploying product X"},
        {"angle": "regulation", "seed_query": "export control laws for product X"},
        {"angle": "history", "seed_query": "historical development of product X"},
        {"angle": "adoption", "seed_query": "who uses product X in practice today"},
        {"angle": "measurement", "seed_query": "how product X performance is measured"},
        {"angle": "alternatives", "seed_query": "alternatives compared to product X"},
        {"angle": "ecosystem", "seed_query": "tooling ecosystem around product X"},
        {"angle": "ethics", "seed_query": "ethical concerns around product X use"},
        {"angle": "scalability", "seed_query": "scalability limits of product X"},
        {"angle": "security", "seed_query": "security properties of product X"},
        {"angle": "ux", "seed_query": "user experience tradeoffs of product X"},
    ]
    adversarial = {
        "angle": "skeptic",
        "seed_query": "is product X safe for consumers really or overstated",
    }
    mmr_only = mmr_select(candidates + [adversarial], 3, lambda_=0.0)
    assert adversarial not in mmr_only or mmr_only[0] is adversarial
    # Even if MMR would skip the skeptic, the reserved slot keeps it.
    selected = select_with_adversarial(candidates, adversarial, 3)
    assert len(selected) == 3
    assert selected[-1] is adversarial
    assert selected[-1]["angle"] == "skeptic"
    assert adversarial not in selected[:-1]


def test_merge_quorum_ceil_067():
    assert _merge_quorum(4) == 3
    assert _merge_quorum(3) == math.ceil(0.67 * 3)
    assert _merge_quorum(2) == math.ceil(0.67 * 2)
    assert _merge_quorum(1) == 1
    assert _merge_quorum(8) == math.ceil(0.67 * 8)


def test_url_normalization_keeps_revisions_distinct():
    a = "https://example.com/paper.pdf?rev=1"
    b = "https://example.com/paper.pdf?rev=2"
    assert _normalize_url(a) != _normalize_url(b)
    union = _citation_union(
        [
            ("q1", "body", [a]),
            ("q2", "body", [b]),
        ]
    )
    norms = {row["norm"] for row in union}
    assert _normalize_url(a) in norms
    assert _normalize_url(b) in norms
    assert len(union) == 2


def test_max_tool_calls_scales_down():
    assert _max_tool_calls(1) == 50
    assert _max_tool_calls(4) == 12
    assert _max_tool_calls(8) == 10


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


def test_bare_fanout_honors_recommended(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    plan_obj = _plan_payload(recommended_fanout=3)
    decomp = _msg(json.dumps(plan_obj), usage={"input_tokens": 50, "output_tokens": 40})
    router = _cheap_router(decomp)

    submit_count = {"n": 0}

    def fake_submit(_key: str, prompt: str, *, max_tool_calls: int | None = None) -> dict:
        submit_count["n"] += 1
        return {"id": f"resp_{submit_count['n']}", "status": "queued"}

    with (
        patch("research._call_planner", side_effect=router),
        patch("research._call_sol_text", side_effect=router),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=_fake_status_factory()),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["what", "is", "X"], FANOUT_AUTO)

    assert rc == 0
    assert submit_count["n"] == 3
    err = capsys.readouterr().err
    assert "Using fanout N=3 (from Stage 0 recommendation)" in err
    prompt_hash = _get_hash("what is X")
    manifest = json.loads(
        (tmp_path / ".files" / "research" / f"fanout-{prompt_hash}" / "manifest.json").read_text()
    )
    assert manifest["n"] == 3
    assert manifest["n_source"] == "recommendation"
    assert manifest["selected"][-1]["angle"] == "adversarial-skeptic"


def test_explicit_fanout_overrides_recommendation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    plan_obj = _plan_payload(recommended_fanout=2)
    decomp = _msg(json.dumps(plan_obj), usage={"input_tokens": 10, "output_tokens": 10})
    router = _cheap_router(decomp)
    submit_count = {"n": 0}

    def fake_submit(_key: str, prompt: str, *, max_tool_calls: int | None = None) -> dict:
        submit_count["n"] += 1
        return {"id": f"resp_{submit_count['n']}", "status": "queued"}

    with (
        patch("research._call_planner", side_effect=router),
        patch("research._call_sol_text", side_effect=router),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=_fake_status_factory()),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["what", "is", "X"], 4)

    assert rc == 0
    assert submit_count["n"] == 4
    err = capsys.readouterr().err
    assert "Using fanout N=4 (from --fanout flag" in err
    assert "Stage 0 recommended 2" in err


def test_recommended_fanout_one_zero_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    plan_obj = _plan_payload(recommended_fanout=1)
    decomp = _msg(json.dumps(plan_obj), usage={"input_tokens": 10, "output_tokens": 10})
    submitted = {"id": "resp_single", "status": "queued"}
    completed = {
        "id": "resp_single",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
    }

    with (
        patch("research._call_planner", return_value=decomp),
        patch("research._call_openai", return_value=submitted) as submit,
        patch("research._check_status", return_value=completed),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["simple", "fact"], FANOUT_AUTO)

    assert rc == 0
    # Single-shot path: one research submit, zero fanout jobs on disk.
    submit.assert_called_once()
    err = capsys.readouterr().err
    assert "Using fanout N=1 (from Stage 0 recommendation)" in err
    assert "zero fanout jobs" in err
    research_dir = tmp_path / ".files" / "research"
    assert not any(p.name.startswith("fanout-") for p in research_dir.iterdir())


def test_plan_only_submits_zero_research_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    plan = _msg(
        json.dumps(_plan_payload()),
        usage={"input_tokens": 50, "output_tokens": 80},
    )

    with (
        patch("research._call_planner", return_value=plan) as planner,
        patch("research._call_openai") as submit,
    ):
        rc = run_research_plan_only(["what", "is", "X"], 4)

    assert rc == 0
    submit.assert_not_called()
    planner.assert_called_once()
    out = capsys.readouterr().out
    assert "# Research plan" in out
    assert "## Brief" in out
    assert "seed_query:" in out
    assert "[adversarial]" in out


def test_plan_only_via_fanout_kwarg(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    plan = _msg(json.dumps(_plan_payload()))

    with (
        patch("research._call_planner", return_value=plan),
        patch("research._call_openai") as submit,
    ):
        rc = run_research_fanout(["q"], 3, plan_only=True)

    assert rc == 0
    submit.assert_not_called()


def test_fanout_produces_manifest_reports_and_merged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    decomp = _msg(
        json.dumps(_plan_payload()),
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    router = _cheap_router(decomp)
    submit_count = {"n": 0}

    def fake_submit(_key: str, prompt: str, *, max_tool_calls: int | None = None) -> dict:
        submit_count["n"] += 1
        assert "Global objective" in prompt or "global objective" in prompt.lower()
        assert "Sibling assignments" in prompt or "sibling" in prompt.lower()
        assert "contradiction" in prompt.lower()
        return {"id": f"resp_{submit_count['n']}", "status": "queued"}

    with (
        patch("research._call_planner", side_effect=router),
        patch("research._call_sol_text", side_effect=router),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=_fake_status_factory()),
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
    assert manifest["selected"][-1]["angle"] == "adversarial-skeptic"
    assert "metrics" in manifest
    for i in range(1, 4):
        assert (run_dir / f"job-{i}.json").is_file()
        assert (run_dir / f"job-{i}-result.json").is_file()
        assert (run_dir / f"report-{i:02d}.md").is_file()
    merged = (run_dir / "merged.md").read_text()
    assert "## Contradictions" in merged
    assert "## Citation check" in merged
    out = capsys.readouterr()
    assert str(run_dir) in out.out
    assert "unverified" in out.err.lower()
    assert "fanout metrics" in out.err
    assert submit_count["n"] == 3


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
                    {"type": "output_text", "text": "done A https://ex.test/a"}
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

    router = _cheap_router(_msg("{}"))

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
        patch("research._call_planner", side_effect=router),
        patch("research._call_sol_text", side_effect=router),
        patch("research._check_status", side_effect=fake_status),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(prompt.split(), 2)

    assert rc == 0
    submit_mock.assert_not_called()
    err = capsys.readouterr().err
    assert "Resuming" in err
    assert "skip submit" in err or "already completed" in err
    manifest2 = json.loads((run_dir / "manifest.json").read_text())
    assert "metrics" in manifest2


def test_partial_merge_quorum_067_n4(tmp_path, monkeypatch, capsys):
    """N=4 → quorum 3: three completions merge; two do not."""
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)
    monkeypatch.setattr("research.JOB_TIMEOUT_S", 0)

    decomp = _msg(
        json.dumps(_plan_payload()),
        usage={"input_tokens": 10, "output_tokens": 10},
    )
    router = _cheap_router(decomp)
    submit_ids = []

    def fake_submit(_key: str, prompt: str, *, max_tool_calls: int | None = None) -> dict:
        rid = f"resp_{len(submit_ids) + 1}"
        submit_ids.append(rid)
        return {"id": rid, "status": "queued"}

    def fake_status_three(_key: str, response_id: str) -> dict:
        if response_id in ("resp_1", "resp_2", "resp_3"):
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
        patch("research._call_planner", side_effect=router),
        patch("research._call_sol_text", side_effect=router),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=fake_status_three),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["what", "is", "X"], 4)

    assert rc == 1
    prompt_hash = _get_hash("what is X")
    run_dir = tmp_path / ".files" / "research" / f"fanout-{prompt_hash}"
    merged = (run_dir / "merged.md").read_text()
    assert "## Missing sub-questions" in merged
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert len(manifest["missing"]) == 1
    assert manifest["missing"][0]["index"] == 4
    capsys.readouterr()  # drain stdout/stderr before the no-merge case

    # Two completions < quorum 3 → no merge.
    import shutil

    shutil.rmtree(run_dir)
    submit_ids.clear()

    def fake_status_two(_key: str, response_id: str) -> dict:
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
        patch("research._call_planner", side_effect=router),
        patch("research._call_sol_text", side_effect=router),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=fake_status_two),
        patch("research.time.sleep"),
    ):
        rc2 = run_research_fanout(["other", "question", "Y"], 4)

    assert rc2 == 1
    out = json.loads(capsys.readouterr().out)
    assert "need >= 3" in out["error"]
    run_dir2 = (
        tmp_path / ".files" / "research" / f"fanout-{_get_hash('other question Y')}"
    )
    assert not (run_dir2 / "merged.md").is_file()


def test_contradictions_carry_observation_dates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    decomp = _msg(json.dumps(_plan_payload()))
    compose = _msg(
        "# Merged research\n\n## Summary\nDisagreement on safety.\n\n"
        "## Contradictions\n"
        "- A reports X is safe as of 2023-03-01; "
        "B reports X is unsafe as of 2025-11-15\n"
    )
    attach = _msg(
        "# Merged research\n\n## Summary\nDisagreement on safety.\n\n"
        "## Findings\n"
        "- Report 1 says X is safe [report-01.md | https://a.test]\n"
        "- Report 2 says X is unsafe [report-02.md | https://b.test]\n\n"
        "## Contradictions\n"
        "- A reports X is safe as of 2023-03-01 [report-01.md | https://a.test]; "
        "B reports X is unsafe as of 2025-11-15 [report-02.md | https://b.test]\n\n"
        "## Citations\n"
        "- https://a.test\n- https://b.test\n"
    )
    cite = _msg("## Citation check\n- OK\n")
    router = _cheap_router(decomp, compose=compose, attach=attach, cite=cite)

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

    compose_prompts: list[str] = []

    def tracking_sol(_key: str, prompt: str) -> dict:
        compose_prompts.append(prompt)
        return router(_key, prompt)

    with (
        patch("research._call_planner", side_effect=router),
        patch("research._call_sol_text", side_effect=tracking_sol),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=fake_status),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["safety", "of", "X"], 2)

    assert rc == 0
    assert any("as of <date>" in p or "as of" in p for p in compose_prompts)
    assert any("COMPOSITION pass" in p or "composition pass" in p.lower() for p in compose_prompts)
    assert any("re-attachment" in p.lower() or "re-attach" in p.lower() for p in compose_prompts)
    prompt_hash = _get_hash("safety of X")
    merged = (
        tmp_path / ".files" / "research" / f"fanout-{prompt_hash}" / "merged.md"
    ).read_text()
    contra = merged.split("## Contradictions", 1)[1].split("##")[0]
    assert "as of 2023-03-01" in contra
    assert "as of 2025-11-15" in contra
    assert "safe" in contra.lower() and "unsafe" in contra.lower()


def test_metrics_block_and_qpd_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    decomp = _msg(json.dumps(_plan_payload(recommended_fanout=2)))
    router = _cheap_router(decomp)
    submit_count = {"n": 0}

    def fake_submit(_key: str, prompt: str, *, max_tool_calls: int | None = None) -> dict:
        submit_count["n"] += 1
        return {"id": f"resp_{submit_count['n']}", "status": "queued"}

    # No web_search calls in output → QPD = 0 / docs = 0.0 < 0.4 → warn.
    with (
        patch("research._call_planner", side_effect=router),
        patch("research._call_sol_text", side_effect=router),
        patch("research._call_openai", side_effect=fake_submit),
        patch("research._check_status", side_effect=_fake_status_factory()),
        patch("research.time.sleep"),
    ):
        rc = run_research_fanout(["what", "is", "X"], 2)

    assert rc == 0
    err = capsys.readouterr().err
    assert "fanout metrics" in err
    assert "mean QPD:" in err
    assert "citation overlap ratio:" in err
    assert "unique authoritative domains:" in err
    assert "contradiction count:" in err
    assert "total cost:" in err
    assert "WARNING: mean QPD" in err
    assert str(QPD_WARN_THRESHOLD) in err
    prompt_hash = _get_hash("what is X")
    manifest = json.loads(
        (tmp_path / ".files" / "research" / f"fanout-{prompt_hash}" / "manifest.json").read_text()
    )
    assert manifest["metrics"]["qpd_warning"] is True
    assert manifest["metrics"]["mean_qpd"] < QPD_WARN_THRESHOLD


def test_malformed_decomposer_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    bad = _msg("sorry I cannot help with that")

    with (
        patch("research._call_planner", return_value=bad),
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
                "recommended_fanout": 3,
                "candidates": [
                    {"angle": "only one", "seed_query": "only one query"},
                ],
                "adversarial": {
                    "angle": "skeptic",
                    "seed_query": "why this may be wrong",
                },
            }
        )
    )

    with patch("research._call_planner", return_value=bad):
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
        patch("research._call_planner", return_value=plan),
        patch("research._call_openai") as submit,
    ):
        rc = run_research_plan_only(["q"], 99)

    assert rc == 0
    submit.assert_not_called()
    err = capsys.readouterr().err
    assert "Clamped" in err
    assert "8" in err
