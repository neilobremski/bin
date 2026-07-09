from __future__ import annotations

import json

import pytest

import tasks
from tasks import (
    approve,
    charge_turn,
    ensure_task,
    expire_tasks,
    format_header,
    load_task,
    new_task,
    park_message,
    parked_messages,
    parse_header,
    save_task,
)
from ulid import new as new_ulid

NODE = "s1l"


class TestHeader:
    def test_roundtrip(self):
        task_id = new_ulid()
        header = format_header(task_id, 3)
        parsed_id, hop, body = parse_header(f"{header} do the thing")
        assert parsed_id == task_id
        assert hop == 3
        assert body == "do the thing"

    def test_strips_header_and_whitespace(self):
        task_id = new_ulid()
        _, _, body = parse_header(f"  {format_header(task_id, 0)}\n\nhello\n")
        assert body == "hello"

    def test_missing_header_is_new_task(self):
        task_id, hop, body = parse_header("plain message")
        assert task_id is None
        assert hop == 0
        assert body == "plain message"

    def test_malformed_header_treated_as_body(self):
        task_id, hop, body = parse_header("[r4t task=short hop=1] hi")
        assert task_id is None
        assert body.startswith("[r4t")

    def test_header_mid_message_ignored(self):
        task_id, _, _ = parse_header(f"hi {format_header(new_ulid(), 1)}")
        assert task_id is None

    def test_case_insensitive_and_uppercased(self):
        raw = format_header(new_ulid(), 2).lower()
        task_id, hop, _ = parse_header(raw + " x")
        assert task_id is not None
        assert task_id == task_id.upper()
        assert hop == 2


class TestLedger:
    def test_ensure_creates_and_persists(self, r4t_home):
        task_id = new_ulid()
        task = ensure_task(NODE, task_id, "gerry")
        assert task["creator"] == "gerry"
        again = ensure_task(NODE, task_id, "someone-else")
        assert again["creator"] == "gerry"

    def test_charge_weighted_by_tier(self):
        task = new_task(new_ulid(), "gerry")
        for _ in range(4):
            assert charge_turn(task, 4)
        assert not charge_turn(task, 4)
        assert task["turns"] == 4

    def test_mixed_tier_weighting(self):
        task = new_task(new_ulid(), "gerry")
        assert charge_turn(task, 2)  # 0.5
        assert charge_turn(task, 4)  # 0.75
        assert charge_turn(task, 4)  # 1.0 exactly
        assert not charge_turn(task, 4)

    def test_approve_extends_budget(self, r4t_home):
        task = new_task(new_ulid(), "gerry")
        task["parked_tier_max"] = 4
        task["status"] = tasks.STATUS_PARKED
        task["park_notified"] = True
        while charge_turn(task, 4):
            pass
        save_task(NODE, task)
        updated = approve(NODE, task["id"], 2)
        assert updated["status"] == tasks.STATUS_OPEN
        assert not updated["park_notified"]
        assert charge_turn(updated, 4)
        assert charge_turn(updated, 4)
        assert not charge_turn(updated, 4)

    def test_approve_unknown_task(self, r4t_home):
        with pytest.raises(KeyError):
            approve(NODE, new_ulid(), 5)

    def test_corrupt_ledger_returns_none(self, r4t_home):
        task_id = new_ulid()
        path = tasks.task_path(NODE, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        assert load_task(NODE, task_id) is None


class TestParked:
    def test_park_and_list(self, r4t_home):
        task_id = new_ulid()
        park_message(NODE, task_id, {"from": "a", "to": "s1l:phil", "body": "x"})
        park_message(NODE, task_id, {"from": "b", "to": "s1l:phil", "body": "y"})
        parked = parked_messages(NODE, task_id)
        assert len(parked) == 2
        envelope = json.loads(parked[0].read_text(encoding="utf-8"))
        assert envelope["to"] == "s1l:phil"
        assert envelope["queued_at"]


class TestExpiry:
    def test_expires_only_stale_tasks(self, r4t_home):
        stale = new_task(new_ulid(), "gerry")
        stale["updated_at"] = "2020-01-01T00:00:00Z"
        tasks.atomic_write_json(tasks.task_path(NODE, stale["id"]), stale)
        park_message(NODE, stale["id"], {"from": "a", "to": "s1l:phil", "body": "x"})
        fresh = ensure_task(NODE, new_ulid(), "gerry")

        removed = expire_tasks(NODE, older_than_seconds=86400)
        assert removed == [stale["id"]]
        assert load_task(NODE, stale["id"]) is None
        assert not parked_messages(NODE, stale["id"])
        assert load_task(NODE, fresh["id"]) is not None
