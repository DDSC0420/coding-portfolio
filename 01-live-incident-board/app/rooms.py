"""Board state and rules.

Pure Python with no web framework imports, so every rule can be unit-tested
without a server. The web layer in main.py only moves JSON in and out.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable

STATUSES = ("investigating", "identified", "monitoring", "resolved")
SEVERITIES = ("sev1", "sev2", "sev3")

MAX_TITLE = 120
MAX_NOTE = 280
MAX_NAME = 24
MAX_INCIDENTS = 40
MAX_UPDATES = 40
MAX_ACTIVITY = 30
MAX_PEOPLE = 20
MAX_ROOMS = 300
ROOM_TTL_SECONDS = 6 * 60 * 60

ROOM_ID_RE = re.compile(r"^[a-z0-9]{6,16}$")
_ROOM_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

BOT_NAME = "PagerBot"
_ADJECTIVES = ("Calm", "Swift", "Bright", "Steady", "Quiet", "Bold", "Sharp", "Kind")
_ANIMALS = ("Otter", "Falcon", "Lynx", "Heron", "Badger", "Fox", "Owl", "Hare")
_COLOURS = ("#4cc2ff", "#ffb020", "#5bd19a", "#c792ea", "#ff8a65", "#f06292", "#9ccc65", "#4dd0e1")


class BoardError(Exception):
    """A rule was broken. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str, incident: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.incident = incident


def clean_text(value: object, limit: int, field: str) -> str:
    """Normalise user text: one line, no control characters, bounded length."""
    if not isinstance(value, str):
        raise BoardError("invalid", f"{field} must be text.")
    text = _WHITESPACE.sub(" ", _CONTROL_CHARS.sub(" ", value)).strip()
    if not text:
        raise BoardError("invalid", f"{field} can't be empty.")
    if len(text) > limit:
        raise BoardError("too_long", f"{field} is limited to {limit} characters.")
    return text


def new_room_id() -> str:
    return "".join(secrets.choice(_ROOM_ALPHABET) for _ in range(8))


def random_name() -> str:
    return f"{secrets.choice(_ADJECTIVES)} {secrets.choice(_ANIMALS)}"


class Room:
    """One shared board. All mutations are synchronous and return JSON-ready dicts."""

    def __init__(self, room_id: str, clock: Callable[[], float] = time.time):
        self.id = room_id
        self.clock = clock
        self.incidents: dict[str, dict] = {}
        self.people: dict[str, dict] = {}
        self.activity: list[dict] = []
        self.seq = 0
        self.last_active = clock()
        self.drill_running = False
        self._joined = 0

    # -- people ---------------------------------------------------------

    def join(self, name: object = None) -> dict:
        if len(self.people) >= MAX_PEOPLE:
            raise BoardError("room_full", "This board is full. Open a new one to try it.")
        try:
            display = clean_text(name, MAX_NAME, "Name")
        except BoardError:
            display = random_name()
        person = {
            "id": secrets.token_hex(6),
            "name": display,
            "colour": _COLOURS[self._joined % len(_COLOURS)],
        }
        self._joined += 1
        self.people[person["id"]] = person
        self.last_active = self.clock()
        return person

    def leave(self, person_id: str) -> None:
        self.people.pop(person_id, None)
        self.last_active = self.clock()

    def rename(self, person_id: str, name: object) -> dict:
        person = self.people[person_id]
        person["name"] = clean_text(name, MAX_NAME, "Name")
        return person

    # -- incidents ------------------------------------------------------

    def snapshot(self) -> list[dict]:
        return sorted(self.incidents.values(), key=lambda i: i["created_at"])

    def create(self, title: object, severity: object, actor: str) -> tuple[dict, dict, str | None]:
        """Returns (incident, activity entry, id of an incident pruned to make room)."""
        title = clean_text(title, MAX_TITLE, "Title")
        if severity not in SEVERITIES:
            raise BoardError("invalid", "Unknown severity.")
        removed = self._make_room()
        now = self.clock()
        incident = {
            "id": secrets.token_hex(5),
            "title": title,
            "severity": severity,
            "status": STATUSES[0],
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "updates": [],
        }
        self.incidents[incident["id"]] = incident
        self._add_update(incident, "status", f"Declared {severity.upper()}", actor)
        entry = self._log(actor, f"declared {severity.upper()}: {title}", incident["id"])
        return incident, entry, removed

    def move(self, incident_id: object, status: object, version: object, actor: str) -> tuple[dict, dict]:
        """Change status. `version` is the version the caller last saw.

        If someone else changed the incident in the meantime the move is refused
        and the caller is handed the current state, so two people dragging the
        same card never silently overwrite each other.
        """
        incident = self._get(incident_id)
        if status not in STATUSES:
            raise BoardError("invalid", "Unknown status.")
        if not isinstance(version, int) or isinstance(version, bool):
            raise BoardError("invalid", "Version must be a number.")
        if version != incident["version"]:
            raise BoardError("conflict", "Someone else updated this incident first.", incident)
        if status == incident["status"]:
            raise BoardError("no_change", "The incident is already in that column.", incident)
        incident["status"] = status
        self._bump(incident)
        self._add_update(incident, "status", f"Moved to {status}", actor)
        entry = self._log(actor, f"moved “{incident['title']}” to {status}", incident["id"])
        return incident, entry

    def note(self, incident_id: object, text: object, actor: str) -> tuple[dict, dict]:
        incident = self._get(incident_id)
        text = clean_text(text, MAX_NOTE, "Update")
        self._bump(incident)
        self._add_update(incident, "note", text, actor)
        entry = self._log(actor, f"posted an update on “{incident['title']}”", incident["id"])
        return incident, entry

    # -- internals ------------------------------------------------------

    def _get(self, incident_id: object) -> dict:
        incident = self.incidents.get(incident_id) if isinstance(incident_id, str) else None
        if incident is None:
            raise BoardError("not_found", "That incident no longer exists.")
        return incident

    def _bump(self, incident: dict) -> None:
        incident["version"] += 1
        incident["updated_at"] = self.clock()

    def _add_update(self, incident: dict, kind: str, text: str, actor: str) -> None:
        self.seq += 1
        incident["updates"].append(
            {"id": self.seq, "kind": kind, "text": text, "by": actor, "at": self.clock()}
        )
        del incident["updates"][:-MAX_UPDATES]

    def _log(self, actor: str, text: str, incident_id: str) -> dict:
        self.seq += 1
        self.last_active = self.clock()
        entry = {"id": self.seq, "by": actor, "text": text, "incident_id": incident_id, "at": self.last_active}
        self.activity.append(entry)
        del self.activity[:-MAX_ACTIVITY]
        return entry

    def _make_room(self) -> str | None:
        """At the cap, drop the oldest resolved incident; refuse if none is resolved."""
        if len(self.incidents) < MAX_INCIDENTS:
            return None
        resolved = [i for i in self.incidents.values() if i["status"] == "resolved"]
        if not resolved:
            raise BoardError("board_full", "This board is full. Resolve an incident first.")
        oldest = min(resolved, key=lambda i: i["updated_at"])
        del self.incidents[oldest["id"]]
        return oldest["id"]


def seed(room: Room) -> None:
    """Demo data so a first-time visitor lands on a board that looks alive."""
    a, _, _ = room.create("Elevated 5xx on checkout API", "sev1", BOT_NAME)
    room.note(a["id"], "Error rate at 12% and climbing. Paging payments on-call.", BOT_NAME)
    b, _, _ = room.create("Search latency p95 above 2s", "sev2", BOT_NAME)
    room.note(b["id"], "Traced to a slow index rebuild on shard 4.", BOT_NAME)
    room.move(b["id"], "identified", b["version"], BOT_NAME)
    c, _, _ = room.create("Delayed email receipts", "sev3", BOT_NAME)
    room.move(c["id"], "monitoring", c["version"], BOT_NAME)
    room.note(c["id"], "Queue drained. Watching for 15 minutes before closing.", BOT_NAME)


class Registry:
    """All live boards. Boards are created on first visit and expire when idle."""

    def __init__(self, clock: Callable[[], float] = time.time, seed_rooms: bool = True):
        self.clock = clock
        self.seed_rooms = seed_rooms
        self.rooms: dict[str, Room] = {}

    def get_or_create(self, room_id: str) -> Room:
        if not ROOM_ID_RE.match(room_id):
            raise BoardError("invalid", "Invalid board id.")
        room = self.rooms.get(room_id)
        if room is None:
            if len(self.rooms) >= MAX_ROOMS:
                self.sweep()
            if len(self.rooms) >= MAX_ROOMS:
                self._evict_one()
            room = Room(room_id, self.clock)
            if self.seed_rooms:
                seed(room)
            self.rooms[room_id] = room
        return room

    def sweep(self) -> int:
        """Remove boards that are empty and have been idle past the TTL."""
        cutoff = self.clock() - ROOM_TTL_SECONDS
        stale = [r.id for r in self.rooms.values() if not r.people and r.last_active < cutoff]
        for room_id in stale:
            del self.rooms[room_id]
        return len(stale)

    def _evict_one(self) -> None:
        empty = [r for r in self.rooms.values() if not r.people]
        if not empty:
            raise BoardError("busy", "The demo is at capacity. Try again in a few minutes.")
        del self.rooms[min(empty, key=lambda r: r.last_active).id]
