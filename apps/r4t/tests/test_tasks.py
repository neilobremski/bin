from __future__ import annotations

import tasks
from tasks import (
    close_task,
    ensure_task,
    expire_tasks,
    format_header,
    load_task,
    new_task,
    normalize_content,
    parse_header,
    save_task,
)
from ulid import new as new_ulid

NODE = "acme"


class TestHeader:
    def test_roundtrip(self):
        task_id = new_ulid()
        header = format_header(task_id, 3)
        parsed_id, hop, auto, body = parse_header(f"{header} do the thing")
        assert parsed_id == task_id
        assert hop == 3
        assert not auto
        assert body == "do the thing"

    def test_auto_token_roundtrip(self):
        task_id = new_ulid()
        header = format_header(task_id, 2, auto=True)
        assert header.endswith(" auto]")
        parsed_id, hop, auto, body = parse_header(f"{header} released by r4t")
        assert parsed_id == task_id
        assert hop == 2
        assert auto
        assert body == "released by r4t"

    def test_strips_header_and_whitespace(self):
        task_id = new_ulid()
        _, _, _, body = parse_header(f"  {format_header(task_id, 0)}\n\nhello\n")
        assert body == "hello"

    def test_missing_header_is_new_task(self):
        task_id, hop, auto, body = parse_header("plain message")
        assert task_id is None
        assert hop == 0
        assert not auto
        assert body == "plain message"

    def test_malformed_header_treated_as_body(self):
        task_id, _, _, body = parse_header("[r4t task=short hop=1] hi")
        assert task_id is None
        assert body.startswith("[r4t")

    def test_header_mid_message_ignored(self):
        task_id, _, _, _ = parse_header(f"hi {format_header(new_ulid(), 1)}")
        assert task_id is None

    def test_case_insensitive_and_uppercased(self):
        raw = format_header(new_ulid(), 2, auto=True).lower()
        task_id, hop, auto, _ = parse_header(raw + " x")
        assert task_id is not None
        assert task_id == task_id.upper()
        assert hop == 2
        assert auto


class TestNormalization:
    def test_strips_header_lowercases_collapses(self):
        header = format_header(new_ulid(), 1, auto=True)
        assert normalize_content(f"{header}  Hello   WORLD\n\nagain ") == "hello world again"


class TestLedger:
    def test_ensure_creates_and_persists(self, r4t_home):
        task_id = new_ulid()
        task = ensure_task(NODE, task_id, "gerry")
        assert task["creator"] == "gerry"
        assert task["status"] == tasks.STATUS_OPEN
        assert not task["answered"]
        again = ensure_task(NODE, task_id, "someone-else")
        assert again["creator"] == "gerry"

    def test_close_marks_answered(self, r4t_home):
        task_id = new_ulid()
        ensure_task(NODE, task_id, "gerry")
        close_task(NODE, task_id)
        task = load_task(NODE, task_id)
        assert task["status"] == tasks.STATUS_CLOSED
        assert task["answered"]

    def test_close_missing_is_noop(self, r4t_home):
        close_task(NODE, new_ulid())  # no exception

    def test_corrupt_ledger_returns_none(self, r4t_home):
        task_id = new_ulid()
        path = tasks.task_path(NODE, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        assert load_task(NODE, task_id) is None


class TestExpiry:
    def test_expires_only_stale_tasks(self, r4t_home):
        stale = new_task(new_ulid(), "gerry")
        stale["updated_at"] = "2020-01-01T00:00:00Z"
        tasks.atomic_write_json(tasks.task_path(NODE, stale["id"]), stale)
        fresh = ensure_task(NODE, new_ulid(), "gerry")

        removed = expire_tasks(NODE, older_than_seconds=86400)
        assert removed == [stale["id"]]
        assert load_task(NODE, stale["id"]) is None
        assert load_task(NODE, fresh["id"]) is not None

    def test_save_refreshes_updated_at(self, r4t_home):
        task = new_task(new_ulid(), "gerry")
        task["updated_at"] = "2020-01-01T00:00:00Z"
        save_task(NODE, task)
        assert task["updated_at"] > "2020-01-01"
