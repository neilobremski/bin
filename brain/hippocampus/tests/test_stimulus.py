"""Tests for stimulus.py: consume_stimulus_files, process_stimuli, handlers."""
import json
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import stimulus
import storage
from constants import TS_FMT


def _insert_memory(db, content, importance=5, category="general"):
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc).strftime(TS_FMT)
    db.execute(
        "INSERT INTO memories(content, importance, category, source, created_at, "
        "accessed_at, access_count, content_hash, stability_days, difficulty, tier, is_active) "
        "VALUES (?, ?, ?, '', ?, ?, 0, ?, 1.0, 5.0, 'hot', 1)",
        (content, importance, category, now, now, content_hash)
    )
    mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    return mid


def test_handle_store(db):
    stim = {"action": "store", "content": "Test memory stored via stimulus protocol",
            "importance": 7, "category": "test", "id": "corr-1", "from": "brain"}
    response = stimulus._handle_store(db, stim)
    db.commit()
    assert response["id"] == "corr-1"
    assert response["action"] == "store"
    assert response["status"] == "stored"
    assert response["memory_id"] is not None


def test_handle_store_rejected(db):
    stim = {"action": "store", "content": "ok",
            "importance": 5, "id": "corr-2", "from": "brain"}
    response = stimulus._handle_store(db, stim)
    assert response["status"] == "rejected"


def test_handle_search(db):
    _insert_memory(db, "The FSRS algorithm tracks memory stability over time", importance=7)
    stim = {"action": "search", "query": "FSRS", "limit": 5,
            "id": "corr-3", "from": "brain"}
    response = stimulus._handle_search(db, stim)
    assert response["id"] == "corr-3"
    assert response["action"] == "search"
    assert response["count"] >= 1


def test_handle_recall(db):
    _insert_memory(db, "Important architecture decision about memory organ", importance=9)
    _insert_memory(db, "Recent observation about test results passing", importance=5)
    stim = {"action": "recall", "limit": 5, "id": "corr-4", "from": "brain"}
    response = stimulus._handle_recall(db, stim)
    assert response["id"] == "corr-4"
    assert response["action"] == "recall"
    assert response["count"] >= 1


def test_handle_stats(db):
    _insert_memory(db, "A test memory for stats checking via stimulus", importance=5)
    stim = {"action": "stats", "id": "corr-5", "from": "brain"}
    response = stimulus._handle_stats(db, stim)
    assert response["id"] == "corr-5"
    assert response["total"] >= 1
    assert response["active"] >= 1


def test_process_stimuli_dispatches(db):
    stimuli = [
        {"action": "store", "content": "Memory stored during process_stimuli test run",
         "importance": 6, "id": "p-1", "from": "test"},
        {"action": "stats", "id": "p-2", "from": "test"},
    ]
    with patch.object(stimulus, '_send_response'):
        processed = stimulus.process_stimuli(db, stimuli)
    db.commit()
    assert processed == 2


def test_process_stimuli_skips_missing_action(db):
    stimuli = [{"id": "bad-1", "from": "test"}]
    processed = stimulus.process_stimuli(db, stimuli)
    assert processed == 0


def test_process_stimuli_handles_unknown_action(db):
    stimuli = [{"action": "unknown", "id": "bad-2", "from": "test"}]
    with patch.object(stimulus, '_send_response'):
        processed = stimulus.process_stimuli(db, stimuli)
    assert processed == 0


def test_consume_stimulus_files():
    """Test consuming JSON files from .stimulus/ directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stim_dir = os.path.join(tmpdir, ".stimulus")
        os.makedirs(stim_dir)

        # Write two stimulus files
        for i, data in enumerate([
            {"action": "stats", "id": f"file-{i}", "from": "test"}
            for i in range(2)
        ]):
            path = os.path.join(stim_dir, f"{i:04d}.json")
            with open(path, 'w') as f:
                json.dump(data, f)

        with patch.object(stimulus, 'STIMULUS_DIR', type(stimulus.STIMULUS_DIR)(stim_dir)):
            stimuli = stimulus.consume_stimulus_files()

        assert len(stimuli) == 2
        # Files should be deleted
        assert len(os.listdir(stim_dir)) == 0


def test_consume_stimulus_files_bad_json():
    """Bad JSON files should be logged and deleted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stim_dir = os.path.join(tmpdir, ".stimulus")
        os.makedirs(stim_dir)

        with open(os.path.join(stim_dir, "0001.json"), 'w') as f:
            f.write("not json{{{")

        with patch.object(stimulus, 'STIMULUS_DIR', type(stimulus.STIMULUS_DIR)(stim_dir)):
            stimuli = stimulus.consume_stimulus_files()

        assert len(stimuli) == 0
        assert len(os.listdir(stim_dir)) == 0
