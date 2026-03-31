"""Tests for ear stimulus handling."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import stimulus as mod


def test_handle_transcribe_success():
    with patch("stimulus.transcribe") as mock_t:
        mock_t.return_value = {"text": "hello from the test", "provider": "groq"}
        stim = {"action": "transcribe", "audio_path": "/tmp/test.mp3",
                "id": "corr-1", "from": "brain"}
        response = mod.handle_transcribe(stim)
        assert response["status"] == "ok"
        assert response["text"] == "hello from the test"
        assert response["id"] == "corr-1"
        assert response["provider"] == "groq"


def test_handle_transcribe_error():
    with patch("stimulus.transcribe", side_effect=RuntimeError("file not found")):
        stim = {"action": "transcribe", "audio_path": "/nonexistent.mp3",
                "id": "corr-2", "from": "brain"}
        response = mod.handle_transcribe(stim)
        assert response["status"] == "error"
        assert "file not found" in response["error"]


def test_handle_transcribe_circ_hash():
    mock_circ = MagicMock()
    mock_circ.returncode = 0
    mock_circ.stdout = "/tmp/resolved_audio.mp3"

    with patch("subprocess.run", return_value=mock_circ):
        with patch("stimulus.transcribe") as mock_t:
            mock_t.return_value = {"text": "circ audio"}
            stim = {"action": "transcribe", "audio_hash": "abc123",
                    "id": "corr-3", "from": "brain"}
            response = mod.handle_transcribe(stim)
            assert response["status"] == "ok"
            assert response["text"] == "circ audio"


def test_process_stimuli_dispatches():
    with patch("stimulus.handle_transcribe") as mock_h:
        mock_h.return_value = {"id": "t-1", "action": "transcribe",
                               "status": "ok", "text": "test"}
        with patch.object(mod, '_send_response'):
            count = mod.process_stimuli([
                {"action": "transcribe", "audio_path": "/tmp/a.mp3",
                 "id": "t-1", "from": "test"},
            ])
            assert count == 1


def test_process_stimuli_unknown_action():
    with patch.object(mod, '_send_response'):
        count = mod.process_stimuli([
            {"action": "unknown", "id": "t-2", "from": "test"},
        ])
        assert count == 0


def test_consume_stimulus_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        stim_dir = os.path.join(tmpdir, ".stimulus")
        os.makedirs(stim_dir)
        for i in range(2):
            path = os.path.join(stim_dir, f"{i:04d}.json")
            with open(path, 'w') as f:
                json.dump({"action": "transcribe", "audio_path": f"/tmp/{i}.mp3",
                           "id": f"f-{i}", "from": "test"}, f)

        with patch.object(mod, 'STIMULUS_DIR', Path(stim_dir)):
            stimuli = mod.consume_stimulus_files()
        assert len(stimuli) == 2
        assert len(os.listdir(stim_dir)) == 0
