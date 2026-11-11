"""HTTP and WebSocket layer for the live incident board."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .rooms import BOT_NAME, ROOM_ID_RE, BoardError, Registry, Room, new_room_id

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_MESSAGE_BYTES = 2048
SEND_TIMEOUT_SECONDS = 2
SWEEP_INTERVAL_SECONDS = 60

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

# The scripted outage the "Run drill" button plays. Each step waits, then acts.
DRILL = (
    ("create", "Login failures spiking in eu-west", "sev1"),
    ("note", "40% of sign-ins failing. Auth service pods restarting."),
    ("note", "Rolled back this morning's config change as a first step."),
    ("move", "identified"),
    ("note", "Root cause: connection pool exhausted after the config change."),
    ("move", "monitoring"),
    ("note", "Error rate back under 0.1%. Watching for regressions."),
    ("move", "resolved"),
)


class TokenBucket:
    """Per-connection rate limit: `burst` messages at once, refilled at `rate` per second."""

    def __init__(self, burst: float = 12, rate: float = 3, clock=time.monotonic):
        self.burst, self.rate, self.clock = burst, rate, clock
        self.tokens = burst
        self.stamp = clock()

    def take(self) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


def create_app(registry: Registry | None = None, drill_delay: float = 1.6) -> FastAPI:
    registry = registry or Registry()
    sockets: dict[str, dict[str, WebSocket]] = {}
    background: set[asyncio.Task] = set()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        async def sweeper():
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
                registry.sweep()

        app.state.loop = asyncio.get_running_loop()
        task = asyncio.create_task(sweeper())
        yield
        for t in (task, *background):
            t.cancel()

    app = FastAPI(title="Live incident board", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.registry = registry
    app.state.sockets = sockets  # exposed so tests can cut connections from the server side

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    async def broadcast(room: Room, payload: dict) -> None:
        """Send to everyone on the board. A slow or dead client never blocks the rest."""
        conns = sockets.get(room.id, {})

        async def send(person_id: str, ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(payload), SEND_TIMEOUT_SECONDS)
            except Exception:
                conns.pop(person_id, None)

        await asyncio.gather(*(send(pid, ws) for pid, ws in list(conns.items())))

    def presence(room: Room) -> dict:
        return {"type": "presence", "people": list(room.people.values())}

    def incident_event(incident: dict, entry: dict, removed: str | None = None) -> dict:
        return {"type": "incident", "incident": incident, "activity": entry, "removed": removed}

    async def run_drill(room: Room) -> None:
        try:
            incident = None
            for step in DRILL:
                await asyncio.sleep(drill_delay)
                try:
                    if step[0] == "create":
                        incident, entry, removed = room.create(step[1], step[2], BOT_NAME)
                    elif step[0] == "note":
                        incident, entry = room.note(incident["id"], step[1], BOT_NAME)
                        removed = None
                    else:
                        incident, entry = room.move(incident["id"], step[1], incident["version"], BOT_NAME)
                        removed = None
                except BoardError:
                    return  # a human moved the drill incident, or the board is full: stop quietly
                await broadcast(room, incident_event(incident, entry, removed))
        finally:
            room.drill_running = False
            await broadcast(room, {"type": "drill", "running": False})

    async def handle(room: Room, person: dict, ws: WebSocket, msg: dict) -> None:
        kind = msg.get("type")
        actor = person["name"]
        if kind == "create":
            incident, entry, removed = room.create(msg.get("title"), msg.get("severity"), actor)
            await broadcast(room, incident_event(incident, entry, removed))
        elif kind == "move":
            incident, entry = room.move(msg.get("id"), msg.get("status"), msg.get("version"), actor)
            await broadcast(room, incident_event(incident, entry))
        elif kind == "note":
            incident, entry = room.note(msg.get("id"), msg.get("text"), actor)
            await broadcast(room, incident_event(incident, entry))
        elif kind == "rename":
            room.rename(person["id"], msg.get("name"))
            await broadcast(room, presence(room))
        elif kind == "drill":
            if room.drill_running:
                raise BoardError("busy", "A drill is already running on this board.")
            room.drill_running = True
            await broadcast(room, {"type": "drill", "running": True})
            task = asyncio.create_task(run_drill(room))
            background.add(task)
            task.add_done_callback(background.discard)
        elif kind == "ping":
            await ws.send_json({"type": "pong"})
        else:
            raise BoardError("invalid", "Unknown message type.")

    @app.websocket("/ws/{room_id}")
    async def board_socket(ws: WebSocket, room_id: str):
        await ws.accept()
        try:
            room = registry.get_or_create(room_id)
            person = room.join()
        except BoardError as err:
            await ws.send_json({"type": "error", "code": err.code, "message": err.message, "fatal": True})
            await ws.close(code=4000)
            return

        sockets.setdefault(room.id, {})[person["id"]] = ws
        bucket = TokenBucket()
        try:
            await ws.send_json(
                {
                    "type": "snapshot",
                    "you": person,
                    "incidents": room.snapshot(),
                    "people": list(room.people.values()),
                    "activity": room.activity,
                    "drill": room.drill_running,
                }
            )
            await broadcast(room, presence(room))
            while True:
                raw = await ws.receive_text()
                ref = None
                try:
                    if len(raw.encode()) > MAX_MESSAGE_BYTES:
                        raise BoardError("too_large", "Message too large.")
                    if not bucket.take():
                        raise BoardError("rate_limited", "Slow down a little.")
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        raise BoardError("invalid", "Messages must be JSON.") from None
                    if not isinstance(msg, dict):
                        raise BoardError("invalid", "Messages must be JSON objects.")
                    ref = msg.get("ref")
                    await handle(room, person, ws, msg)
                except BoardError as err:
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": err.code,
                            "message": err.message,
                            "ref": ref if isinstance(ref, (str, int)) else None,
                            "incident": err.incident,
                        }
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sockets.get(room.id, {}).pop(person["id"], None)
            if not sockets.get(room.id):
                sockets.pop(room.id, None)
            room.leave(person["id"])
            await broadcast(room, presence(room))

    @app.get("/")
    async def new_board():
        return RedirectResponse(f"/b/{new_room_id()}", status_code=302)

    @app.get("/b/{room_id}")
    async def board_page(room_id: str):
        if not ROOM_ID_RE.match(room_id):
            raise HTTPException(status_code=404, detail="Board not found")
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "boards": len(registry.rooms)}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
