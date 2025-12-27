import asyncio
import json
import logging
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set

import websockets


@dataclass(frozen=True)
class OverlayEvent:
    type: str


@dataclass(frozen=True)
class StatusEvent(OverlayEvent):
    state: str
    hint: str
    color: str


@dataclass(frozen=True)
class AssistantReplyEvent(OverlayEvent):
    persona: str
    color: str
    text: str


@dataclass(frozen=True)
class ErrorEvent(OverlayEvent):
    stage: str
    message: str


def _load_settings(settings_path: Path) -> Dict[str, Any]:
    if not settings_path.exists():
        raise FileNotFoundError(f"settings.json not found at: {settings_path}")
    return json.loads(settings_path.read_text(encoding="utf-8"))


def _now_ms() -> int:
    return int(time.time() * 1000)


class CoreWSServer:
    def __init__(self, *, host: str, port: int, path: str, demo_sequence: bool = False):
        self._host = host
        self._port = port
        self._path = path
        self._demo_sequence_enabled = bool(demo_sequence)
        self._clients: Set[Any] = set()
        self._server: Optional[Any] = None

    @property
    def url(self) -> str:
        return f"ws://{self._host}:{self._port}{self._path}"

    async def start(self) -> None:
        self._server = await websockets.serve(self._handler, self._host, self._port)

    async def stop(self) -> None:
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(payload, ensure_ascii=False)
        dead: Set[Any] = set()
        for ws in self._clients:
            try:
                await ws.send(message)
            except Exception:
                dead.add(ws)
        self._clients.difference_update(dead)

    async def _handler(self, ws: Any) -> None:
        # Best-effort path validation across websockets versions.
        actual_path = getattr(ws, "path", None)
        if actual_path is None:
            request = getattr(ws, "request", None)
            actual_path = getattr(request, "path", None)

        # If we can determine the path and it's wrong, reject.
        # Some embedded overlay hosts connect without a path ("/") even if a path is expected.
        # Accept both the configured path and root for compatibility.
        if self._path and actual_path is not None:
            allowed = {self._path, "/", ""}
            if actual_path not in allowed:
                print(f"[ws] reject: expected_path={self._path!r} actual_path={actual_path!r}")
                await ws.close(code=1008, reason="Invalid path")
                return

        peer = getattr(ws, "remote_address", None)
        print(f"[ws] client connected peer={peer!r} path={actual_path!r} clients={len(self._clients) + 1}")

        self._clients.add(ws)
        try:
            await self.broadcast(
                asdict(
                    StatusEvent(
                        type="status",
                        state="listening",
                        hint="ONLINE",
                        color="#FFFFFF",
                    )
                )
            )

            if self._demo_sequence_enabled:
                # Demo sequence for Milestone 1 acceptance.
                asyncio.create_task(self._run_demo_sequence())

            async for _ in ws:
                # Overlay is dumb renderer; ignore incoming messages.
                pass
        finally:
            self._clients.discard(ws)
            print(f"[ws] client disconnected peer={peer!r} clients={len(self._clients)}")

    async def _run_demo_sequence(self) -> None:
        # Small delay to ensure overlay has rendered initial ONLINE.
        await asyncio.sleep(0.35)
        await self.broadcast(
            asdict(StatusEvent(type="status", state="recording", hint="(demo) recording", color="#FFFFFF"))
        )
        await asyncio.sleep(0.5)
        await self.broadcast(
            asdict(StatusEvent(type="status", state="transcribing", hint="(demo) transcribing", color="#FFFFFF"))
        )
        await asyncio.sleep(0.5)
        await self.broadcast(
            asdict(StatusEvent(type="status", state="thinking", hint="(demo) thinking", color="#FFFFFF"))
        )
        await asyncio.sleep(0.5)
        await self.broadcast(
            asdict(
                AssistantReplyEvent(
                    type="assistant_reply",
                    persona="Domino",
                    color="#00FFAA",
                    text=f"Milestone 1 websocket OK @ {_now_ms()}",
                )
            )
        )
        await asyncio.sleep(0.35)
        await self.broadcast(
            asdict(StatusEvent(type="status", state="listening", hint="ONLINE", color="#FFFFFF"))
        )


async def _main() -> None:
    # Suppress noisy handshake tracebacks from non-WebSocket probes (e.g. port tests)
    # so Milestone 1 runs cleanly in normal usage.
    logging.getLogger("websockets").setLevel(logging.CRITICAL)

    repo_root = Path(__file__).resolve().parents[1]
    settings = _load_settings(repo_root / "settings.json")

    overlay_ws = settings.get("overlay_ws") or {}
    host = str(overlay_ws.get("host", "127.0.0.1"))
    port = int(overlay_ws.get("port", 8765))
    path = str(overlay_ws.get("path", "/ws"))

    # Keep the demo behavior when running core_ws.py directly.
    server = CoreWSServer(host=host, port=port, path=path, demo_sequence=True)

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGTERM, _request_stop)
    except Exception:
        pass

    await server.start()
    print(f"Core websocket listening: {server.url}")

    try:
        await stop_event.wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(_main())
