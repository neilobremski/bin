"""Tests for PFC stimulus handling."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import stimulus as mod


def test_parse_llm_response_valid_json():
    text = '{"reply": "done", "signals": [{"to": "hippocampus", "body": {"action": "store"}}]}'
    result = mod._parse_llm_response(text)
    assert result["reply"] == "done"
    assert len(result["signals"]) == 1


def test_parse_llm_response_markdown_code_block():
    text = 'Here is my response:\n```json\n{"reply": "ok", "signals": []}\n```'
    result = mod._parse_llm_response(text)
    assert result["reply"] == "ok"
    assert result["signals"] == []


def test_parse_llm_response_plain_text_fallback():
    text = "I don't know how to respond to that."
    result = mod._parse_llm_response(text)
    assert result["reply"] == text
    assert result["signals"] == []


def test_think_sends_to_llm():
    mock_response = '{"reply": "thinking complete", "signals": []}'
    with patch("stimulus.invoke", return_value=mock_response):
        result = mod.think({"action": "test", "id": "t-1", "from": "brain"})
        assert result["reply"] == "thinking complete"


def test_process_stimuli_sends_reply():
    mock_response = '{"reply": "got it", "signals": []}'
    sent = []

    def mock_send(target, data):
        sent.append((target, data))

    with patch("stimulus.invoke", return_value=mock_response):
        with patch.object(mod, '_send_response', side_effect=mock_send):
            count = mod.process_stimuli([
                {"action": "plan", "content": "test", "id": "p-1", "from": "brain"}
            ])
            assert count == 1
            assert len(sent) == 1
            assert sent[0][0] == "brain"
            assert sent[0][1]["reply"] == "got it"


def test_process_stimuli_sends_signals():
    mock_response = json.dumps({
        "reply": "stored it",
        "signals": [{"to": "hippocampus", "body": {"action": "store", "content": "test"}}]
    })
    sent = []

    def mock_send(target, data):
        sent.append((target, data))

    with patch("stimulus.invoke", return_value=mock_response):
        with patch.object(mod, '_send_response', side_effect=mock_send):
            mod.process_stimuli([
                {"action": "remember", "content": "test", "id": "p-2", "from": "brain"}
            ])
            # Should have sent reply + signal
            assert len(sent) == 2
            targets = [s[0] for s in sent]
            assert "brain" in targets
            assert "hippocampus" in targets


def test_process_stimuli_empty_returns_zero():
    assert mod.process_stimuli([]) == 0


def test_process_stimuli_handles_llm_error():
    sent = []

    def mock_send(target, data):
        sent.append((target, data))

    with patch("stimulus.invoke", side_effect=RuntimeError("no provider")):
        with patch.object(mod, '_send_response', side_effect=mock_send):
            count = mod.process_stimuli([
                {"action": "test", "id": "e-1", "from": "brain"}
            ])
            assert count == 0
            assert len(sent) == 1
            assert "error" in sent[0][1]


def test_consume_stimulus_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        stim_dir = os.path.join(tmpdir, ".stimulus")
        os.makedirs(stim_dir)
        for i in range(2):
            path = os.path.join(stim_dir, f"{i:04d}.json")
            with open(path, 'w') as f:
                json.dump({"action": "test", "id": f"f-{i}", "from": "test"}, f)

        with patch.object(mod, 'STIMULUS_DIR', Path(stim_dir)):
            stimuli = mod.consume_stimulus_files()
        assert len(stimuli) == 2
        assert len(os.listdir(stim_dir)) == 0


def test_no_stimulus_no_action():
    """PFC does nothing without stimulus — this is the core contract."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stim_dir = os.path.join(tmpdir, ".stimulus")
        os.makedirs(stim_dir)
        with patch.object(mod, 'STIMULUS_DIR', Path(stim_dir)):
            stimuli = mod.consume_stimulus_files()
        assert stimuli == []


def test_parse_llm_response_unclosed_fence():
    """Unclosed code fence should not crash."""
    text = 'Here is my answer:\n```json\n{"reply": "partial"'
    result = mod._parse_llm_response(text)
    assert result["signals"] == []


def test_parse_llm_response_signals_not_array():
    """Non-array signals should be coerced to empty list."""
    text = '{"reply": "ok", "signals": "not-an-array"}'
    result = mod._parse_llm_response(text)
    assert result["reply"] == "ok"
    assert result["signals"] == []
