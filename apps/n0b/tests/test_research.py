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
    _get_hash,
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


def test_research_help_shows_fanout():
    proc = run_n0b("ai", "research", "--help")
    assert proc.returncode == 0
    assert "--fanout" in proc.stdout
    assert "N" in proc.stdout


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


def test_ai_research_requires_prompt():
    proc = run_n0b("ai", "research")
    assert proc.returncode == 2
    assert "Usage: n0b ai research" in proc.stderr


def test_fanout_requires_prompt():
    proc = run_n0b("ai", "research", "--fanout", "3")
    assert proc.returncode == 2
    assert "Usage: n0b ai research" in proc.stderr


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
    # Cache file written at the single-shot path, not under fanout-*.
    files = list((tmp_path / ".files" / "research").glob("*.json"))
    assert len(files) == 1
    assert not files[0].name.startswith("fanout")


def test_fanout_produces_reports_and_merged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    questions = [
        "What is the mechanism of X?",
        "What evidence supports X?",
        "What are the risks of X?",
    ]
    decomp = _msg(
        json.dumps(questions),
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    merge_body = (
        "# Merged research\n\n## Summary\nX works.\n\n"
        "## Findings\n"
        "- Mechanism is Y [report-01.md | https://example.com/a]\n"
        "- Evidence is Z [report-02.md | https://example.com/b]\n\n"
        "## Conflicts\n"
        "- report-01 says safe [report-01.md | https://example.com/a]; "
        "report-03 says risky [report-03.md | https://example.com/c]\n"
    )
    merge = _msg(merge_body, usage={"input_tokens": 200, "output_tokens": 80})

    cheap_calls: list[str] = []

    def fake_cheap(_key: str, prompt: str) -> dict:
        cheap_calls.append(prompt)
        if "Decompose" in prompt or "complementary" in prompt:
            return decomp
        return merge

    submit_ids = {1: "resp_1", 2: "resp_2", 3: "resp_3"}
    bodies = {
        "resp_1": "Mechanism detail. https://example.com/a",
        "resp_2": "Evidence detail. https://example.com/b",
        "resp_3": "Risk detail. https://example.com/c",
    }

    def fake_submit(_key: str, prompt: str) -> dict:
        for i, q in enumerate(questions, start=1):
            if prompt == q:
                return {"id": submit_ids[i], "status": "queued"}
        raise AssertionError(f"unexpected submit prompt: {prompt!r}")

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
                            "text": bodies[response_id],
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
    assert (run_dir / "meta.json").is_file()
    assert (run_dir / "merged.md").is_file()
    for i in range(1, 4):
        assert (run_dir / f"job-{i}.json").is_file()
        report = (run_dir / f"report-{i:02d}.md").read_text()
        assert questions[i - 1] in report
        assert f"https://example.com/{'abc'[i - 1]}" in report
    merged = (run_dir / "merged.md").read_text()
    assert "## Conflicts" in merged
    assert "report-01.md" in merged
    out = capsys.readouterr()
    assert str(run_dir) in out.out
    assert "Jobs submitted" in out.err or "submitted" in out.err
    assert "estimate" in out.err.lower()
    assert len(cheap_calls) == 2


def test_fanout_rejoins_cached_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    monkeypatch.setattr("research.POLL_INTERVAL_S", 0)

    questions = ["Angle A on topic?", "Angle B on topic?"]
    prompt = "topic question"
    prompt_hash = _get_hash(prompt)
    run_dir = tmp_path / ".files" / "research" / f"fanout-{prompt_hash}"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps({"prompt": prompt, "n": 2, "sub_questions": questions}) + "\n"
    )
    for i, rid in enumerate(["resp_a", "resp_b"], start=1):
        (run_dir / f"job-{i}.json").write_text(
            json.dumps({"id": rid, "status": "queued"})
        )

    submit = patch("research._call_openai")
    decomp = patch("research._call_cheap")

    def fake_status(_key: str, response_id: str) -> dict:
        return {
            "id": response_id,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": f"done {response_id} https://ex.test/{response_id}"}
                    ],
                }
            ],
        }

    merge = _msg(
        "# Merged research\n\n## Summary\nok\n\n## Conflicts\n- none\n",
        usage={"input_tokens": 1, "output_tokens": 1},
    )

    with (
        submit as submit_mock,
        decomp as cheap_mock,
        patch("research._check_status", side_effect=fake_status),
        patch("research.time.sleep"),
    ):
        cheap_mock.return_value = merge
        rc = run_research_fanout(prompt.split(), 2)

    assert rc == 0
    submit_mock.assert_not_called()
    # Only the merge cheap call — decompose skipped via meta cache.
    assert cheap_mock.call_count == 1
    err = capsys.readouterr().err
    assert "Rejoining" in err
    assert "rejoined" in err


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
    assert "JSON list" in err_obj["error"] or "decompose" in err_obj["error"]


def test_malformed_decomposer_wrong_count(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.BIN_ROOT", tmp_path)
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")

    bad = _msg(json.dumps(["only one"]))

    with patch("research._call_cheap", return_value=bad):
        rc = run_research_fanout(["q"], 3)

    assert rc == 1
    err_obj = json.loads(capsys.readouterr().out)
    assert "expected 3" in err_obj["error"]


def test_fanout_n_less_than_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("research.resolve", lambda _k: "sk-test")
    rc = run_research_fanout(["q"], 1)
    assert rc == 1
    assert "N >= 2" in capsys.readouterr().out


def test_cli_dispatches_fanout(tmp_path, monkeypatch):
    with patch("commands.ai_research_cmd.run_research_fanout", return_value=0) as fanout, patch(
        "commands.ai_research_cmd.run_research", return_value=0
    ) as single:
        rc = main(["ai", "research", "--fanout", "4", "alpha", "beta"])
    assert rc == 0
    fanout.assert_called_once_with(["alpha", "beta"], 4)
    single.assert_not_called()


def test_cli_dispatches_single_shot():
    with patch("commands.ai_research_cmd.run_research", return_value=0) as single, patch(
        "commands.ai_research_cmd.run_research_fanout", return_value=0
    ) as fanout:
        rc = main(["ai", "research", "alpha", "beta"])
    assert rc == 0
    single.assert_called_once_with(["alpha", "beta"])
    fanout.assert_not_called()
