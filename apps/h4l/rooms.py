from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ulid import new as new_ulid

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
CHATROOMS_DIR = ".chatrooms"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_slug(raw: str) -> str:
    slug = raw.strip().lower().lstrip("#")
    if not slug or not SLUG_RE.match(slug):
        raise ValueError(f"invalid room slug: {raw!r}")
    return slug


def normalize_agent(name: str) -> str:
    value = name.strip()
    if not value:
        raise ValueError("agent name must not be empty")
    return value


class RoomStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.base = self.root / CHATROOMS_DIR

    def rooms_dir(self) -> Path:
        return self.base / "rooms"

    def room_dir(self, slug: str) -> Path:
        return self.rooms_dir() / slug

    def meta_path(self, slug: str) -> Path:
        return self.room_dir(slug) / "meta.json"

    def messages_dir(self, slug: str) -> Path:
        return self.room_dir(slug) / "messages"

    def ensure_room(self, slug: str) -> dict:
        slug = normalize_slug(slug)
        meta_p = self.meta_path(slug)
        if meta_p.is_file():
            return self._load_meta(slug)
        now = utc_now()
        meta = {
            "slug": slug,
            "created_at": now,
            "last_activity": now,
            "members": [],
        }
        self._write_meta(slug, meta)
        return meta

    def load_meta(self, slug: str) -> dict:
        slug = normalize_slug(slug)
        meta_p = self.meta_path(slug)
        if not meta_p.is_file():
            raise KeyError(slug)
        return self._load_meta(slug)

    def save_meta(self, slug: str, meta: dict) -> None:
        slug = normalize_slug(slug)
        self._write_meta(slug, meta)

    def touch_activity(self, slug: str, meta: dict) -> dict:
        meta["last_activity"] = utc_now()
        self.save_meta(slug, meta)
        return meta

    def member_names(self, meta: dict) -> list[str]:
        raw = meta.get("members")
        if not isinstance(raw, list):
            return []
        return [normalize_agent(str(m)) for m in raw if str(m).strip()]

    def has_member(self, meta: dict, agent: str) -> bool:
        key = agent.lower()
        return any(m.lower() == key for m in self.member_names(meta))

    def add_member(self, meta: dict, agent: str) -> tuple[dict, bool]:
        agent = normalize_agent(agent)
        members = self.member_names(meta)
        if any(m.lower() == agent.lower() for m in members):
            return meta, False
        members.append(agent)
        meta["members"] = members
        return meta, True

    def remove_member(self, meta: dict, agent: str) -> tuple[dict, bool]:
        agent = normalize_agent(agent)
        members = self.member_names(meta)
        kept = [m for m in members if m.lower() != agent.lower()]
        if len(kept) == len(members):
            return meta, False
        meta["members"] = kept
        return meta, True

    def mark_help_seen(self, meta: dict, agent: str) -> dict:
        agent = normalize_agent(agent)
        if self.has_seen_help(meta, agent):
            return meta
        seen = self.help_seen_names(meta)
        seen.append(agent)
        meta["help_seen"] = seen
        return meta

    def help_seen_names(self, meta: dict) -> list[str]:
        raw = meta.get("help_seen")
        if not isinstance(raw, list):
            return []
        return [normalize_agent(str(a)) for a in raw if str(a).strip()]

    def has_seen_help(self, meta: dict, agent: str) -> bool:
        key = agent.lower()
        return any(a.lower() == key for a in self.help_seen_names(meta))

    def append_message(
        self,
        slug: str,
        *,
        sender: str,
        content: str,
        kind: str = "post",
        files: list[dict] | None = None,
    ) -> dict:
        slug = normalize_slug(slug)
        msg_id = new_ulid()
        now = utc_now()
        payload: dict = {
            "id": msg_id,
            "date": now,
            "from": normalize_agent(sender),
            "kind": kind,
            "content": content,
        }
        if files:
            payload["files"] = list(files)
        msg_dir = self.messages_dir(slug)
        msg_dir.mkdir(parents=True, exist_ok=True)
        path = msg_dir / f"{msg_id}.json"
        self._atomic_write(path, payload)
        return payload

    def list_messages(self, slug: str) -> list[dict]:
        slug = normalize_slug(slug)
        msg_dir = self.messages_dir(slug)
        if not msg_dir.is_dir():
            return []
        out: list[dict] = []
        for path in sorted(msg_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append(data)
        out.sort(key=lambda m: m.get("date", ""))
        return out

    def list_rooms(self) -> list[dict]:
        rooms_root = self.rooms_dir()
        if not rooms_root.is_dir():
            return []
        out: list[dict] = []
        for entry in sorted(rooms_root.iterdir()):
            if not entry.is_dir():
                continue
            meta_p = entry / "meta.json"
            if not meta_p.is_file():
                continue
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(meta, dict):
                out.append(meta)
        out.sort(key=lambda m: m.get("slug", ""))
        return out

    def clear_older_than(self, seconds: float) -> list[str]:
        from time import time as _time

        cutoff = _time() - seconds
        removed: list[str] = []
        for meta in self.list_rooms():
            slug = meta.get("slug", "")
            if not slug:
                continue
            raw = meta.get("last_activity", "")
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.timestamp() >= cutoff:
                    continue
            except ValueError:
                pass
            self._delete_room(slug)
            removed.append(slug)
        return removed

    def clear_all(self) -> int:
        rooms = self.list_rooms()
        for meta in rooms:
            slug = meta.get("slug", "")
            if slug:
                self._delete_room(slug)
        return len(rooms)

    def _delete_room(self, slug: str) -> None:
        import shutil

        path = self.room_dir(slug)
        if path.is_dir():
            shutil.rmtree(path)

    def _load_meta(self, slug: str) -> dict:
        data = json.loads(self.meta_path(slug).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"corrupt meta for room {slug}")
        data["slug"] = normalize_slug(str(data.get("slug", slug)))
        data["members"] = self.member_names(data)
        return data

    def _write_meta(self, slug: str, meta: dict) -> None:
        room = self.room_dir(slug)
        room.mkdir(parents=True, exist_ok=True)
        self.messages_dir(slug).mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.meta_path(slug), meta)

    def _atomic_write(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
