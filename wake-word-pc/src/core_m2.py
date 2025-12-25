import asyncio
import base64
import json
import logging
import signal
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core_ws import CoreWSServer
from hub_client import HubClient, persona_display_name, wake_persona_to_hub
from overlay_events import assistant_reply, error, status
from recorder import record_command
from wake_vosk import VoskWakeListener


def _load_settings(settings_path: Path) -> Dict[str, Any]:
    if not settings_path.exists():
        raise FileNotFoundError(f"settings.json not found at: {settings_path}")
    return json.loads(settings_path.read_text(encoding="utf-8"))


async def _wake_record_loop(
    *,
    server: CoreWSServer,
    hub: HubClient,
    model_path: Path,
    audio_cfg: Dict[str, Any],
    wake_engine_cfg: Dict[str, Any],
    wake_words_cfg: Any,
    rec_cfg: Dict[str, Any],
    client_cfg: Dict[str, Any],
) -> None:
    while True:
        listener = VoskWakeListener(
            model_path=model_path,
            audio=audio_cfg,
            wake_engine=wake_engine_cfg,
            wake_words=wake_words_cfg,
        )

        await server.broadcast(status(state="listening", hint="Listening for wake words", color="#FFFFFF"))

        async for hit in listener.listen():
            # Wake hit
            print(
                f"[m3] WAKE HIT @ {time.strftime('%H:%M:%S')} "
                f"wake_word={getattr(hit, 'wake_word', '')} persona_mode={getattr(hit, 'persona_mode', '')}"
            )

            await server.broadcast(
                status(
                    state="recording",
                    hint=f"Recording… ({getattr(hit, 'wake_word', '')})",
                    color=getattr(hit, "color", "#FFFFFF"),
                )
            )

            # record_command is blocking; run it off the event loop.
            wav_bytes = await asyncio.to_thread(record_command, audio_cfg=audio_cfg, rec_cfg=rec_cfg)
            if not wav_bytes:
                await server.broadcast(error(stage="record", message="No speech detected"))
                await server.broadcast(status(state="listening", hint="Listening for wake words", color="#FFFFFF"))
                continue

            await server.broadcast(status(state="transcribing", hint="Transcribing…", color=getattr(hit, "color", "#FFFFFF")))
            stt = await hub.stt(wav_bytes=wav_bytes)
            if stt is None:
                await server.broadcast(error(stage="stt", message="STT failed"))
                await server.broadcast(status(state="listening", hint="Listening for wake words", color="#FFFFFF"))
                continue

            user_text = (stt.text or "").strip()
            print(f"[m4] stt.text={user_text!r}")

            await server.broadcast(status(state="thinking", hint="Thinking…", color=getattr(hit, "color", "#FFFFFF")))

            persona_mode = str(getattr(hit, "persona_mode", "auto"))
            persona = wake_persona_to_hub(persona_mode)
            session_id = (client_cfg.get("session_id") or client_cfg.get("device") or None)

            ask_res = await hub.ask(persona=persona, text=user_text, session_id=session_id)
            if ask_res is None:
                await server.broadcast(error(stage="ask", message="Ask failed"))
                await server.broadcast(status(state="listening", hint="Listening for wake words", color="#FFFFFF"))
                continue

            primary = ask_res.primary
            reply_text = (primary.reply or "").strip()
            await server.broadcast(
                assistant_reply(
                    persona=persona_display_name(persona_mode),
                    text=reply_text,
                    color=getattr(hit, "color", "#FFFFFF"),
                )
            )

            if primary.audio_b64:
                try:
                    audio_bytes = base64.b64decode((primary.audio_b64 or "").strip())
                    print(f"[m4] audio received: {len(audio_bytes)} bytes provider={primary.tts_provider!r}")
                except Exception as exc:
                    print(f"[m4] audio_b64 decode failed: {exc}")
            else:
                print(f"[m4] audio_b64 absent provider={primary.tts_provider!r}")

            await server.broadcast(status(state="listening", hint="Listening for wake words", color="#FFFFFF"))


async def _main() -> None:
    # Reduce noise from websockets internals.
    logging.getLogger("websockets").setLevel(logging.CRITICAL)

    repo_root = Path(__file__).resolve().parents[1]
    settings = _load_settings(repo_root / "settings.json")

    audio_cfg = settings.get("audio") or {}
    wake_engine_cfg = settings.get("wake_engine") or {}
    wake_words_cfg = settings.get("wake_words") or []
    hub_cfg = settings.get("hub") or {}
    overlay_ws_cfg = settings.get("overlay_ws") or {}
    rec_cfg = settings.get("recording") or {}
    client_cfg = settings.get("client") or {}

    model_rel = str(wake_engine_cfg.get("vosk_model_path", "models/vosk-model-small-en-us-0.15"))
    model_path = repo_root / model_rel

    hub = HubClient(
        base_url=str(hub_cfg.get("base_url", "http://127.0.0.1:2424")).rstrip("/"),
        stt_path=str(hub_cfg.get("stt_path", "/api/stt")),
        ask_path=str(hub_cfg.get("ask_path", "/api/ask")),
        timeout_s=float(hub_cfg.get("timeout_s", 60.0)),
    )

    server = CoreWSServer(
        host=str(overlay_ws_cfg.get("host", "127.0.0.1")),
        port=int(overlay_ws_cfg.get("port", 8765)),
        path=str(overlay_ws_cfg.get("path", "/ws")),
        demo_sequence=False,
    )

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

    wake_task = asyncio.create_task(
        _wake_record_loop(
            server=server,
            hub=hub,
            model_path=model_path,
            audio_cfg=audio_cfg,
            wake_engine_cfg=wake_engine_cfg,
            wake_words_cfg=wake_words_cfg,
            rec_cfg=rec_cfg,
            client_cfg=client_cfg,
        )
    )

    try:
        await stop_event.wait()
    finally:
        wake_task.cancel()
        try:
            await wake_task
        except asyncio.CancelledError:
            pass
        await server.stop()


if __name__ == "__main__":
    asyncio.run(_main())
