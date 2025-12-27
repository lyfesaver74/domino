import asyncio
import base64
import json
import logging
import signal
import time
from pathlib import Path
from typing import Any, Dict, Optional

import audio_playback
import httpx
from core_ws import CoreWSServer
from hub_client import HubClient, persona_display_name, wake_persona_to_hub
from overlay_events import actions, assistant_reply, error, status, tts_audio, user_utterance, wake
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
    output_device = audio_cfg.get("output_device", None)
    if output_device is not None:
        try:
            output_device = int(output_device)
        except Exception:
            print(f"[m4] invalid audio.output_device={output_device!r}; using default")
            output_device = None

    async def _play_tts_audio_b64(audio_b64: str) -> None:
        try:
            audio_bytes = base64.b64decode((audio_b64 or "").strip())
        except Exception as exc:
            print(f"[m4] audio_b64 decode failed: {exc}")
            return

        if not audio_bytes:
            return

        try:
            print(
                f"[m4] audio playback start: bytes={len(audio_bytes)} "
                f"fmt={audio_playback.sniff_audio_format(audio_bytes)!r} "
                f"output_device={output_device!r}"
            )
            await audio_playback.play_audio_bytes(audio_bytes, output_device=output_device)
            print("[m4] audio playback done")
        except Exception as exc:
            print(f"[m4] audio playback failed: {exc}")

    def _should_play_pre_tts(reply_text: str) -> bool:
        # Only pre-roll for responses that are likely to take longer to synth.
        t = (reply_text or "").strip()
        return len(t) >= 90 or len(t.split()) >= 18

    async def _play_pre_tts_cue(*, persona_key: str, vibe: str, output_device: Optional[int]) -> None:
        try:
            cue = await hub.pre_tts(persona=persona_key, vibe=vibe)
            if not cue:
                return

            url = cue.url
            if not url.startswith("/"):
                url = "/" + url

            timeout = httpx.Timeout(timeout=20.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(hub.base_url.rstrip("/") + url)
                resp.raise_for_status()
                audio_bytes = resp.content
            if not audio_bytes:
                return

            await audio_playback.play_audio_bytes(audio_bytes, output_device=output_device)
        except Exception as exc:
            print(f"[m4] pre-tts cue failed: {exc}")

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
                wake(
                    wake_word=str(getattr(hit, "wake_word", "") or ""),
                    persona_mode=str(getattr(hit, "persona_mode", "") or ""),
                    color=getattr(hit, "color", "#FFFFFF"),
                )
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

            if user_text:
                await server.broadcast(user_utterance(text=user_text))

            await server.broadcast(status(state="thinking", hint="Thinking…", color=getattr(hit, "color", "#FFFFFF")))

            persona_mode = str(getattr(hit, "persona_mode", "auto"))
            persona = wake_persona_to_hub(persona_mode)
            session_id = (client_cfg.get("session_id") or client_cfg.get("device") or None)

            # Text first (fast), audio second (explicit) so we can represent a real TTS-building phase.
            ask_res = await hub.ask(persona=persona, text=user_text, session_id=session_id, tts=False)
            if ask_res is None:
                await server.broadcast(error(stage="ask", message="Ask failed"))
                await server.broadcast(status(state="listening", hint="Listening for wake words", color="#FFFFFF"))
                continue

            def _format_from_provider(provider: Optional[str]) -> str:
                p = (provider or "").strip().lower()
                if p == "elevenlabs":
                    return "mp3"
                return "wav"

            # Build a quick persona->color map from settings for collective fan-out.
            persona_color_map: Dict[str, str] = {}
            try:
                for ww in (wake_words_cfg or []):
                    if not isinstance(ww, dict):
                        continue
                    mode = str(ww.get("persona_mode") or "").strip().casefold()
                    c = str(ww.get("color") or "").strip() or "#FFFFFF"
                    if mode in {"domino", "penny", "jimmy", "collective"}:
                        persona_color_map[mode] = c
            except Exception:
                pass

            primary = ask_res.primary
            # If the hub responded with collective fanout, render each persona response.
            if str(primary.persona).strip().casefold() == "collective" and ask_res.responses:
                await server.broadcast(status(state="tts_building", hint="Building voice…", color=getattr(hit, "color", "#FFFFFF")))
                for r in ask_res.responses:
                    persona_key = (r.persona or "").strip().casefold()
                    reply_text = (r.reply or "").strip()
                    color = persona_color_map.get(persona_key, "#FFFFFF")

                    await server.broadcast(
                        assistant_reply(
                            persona=persona_display_name(persona_key),
                            text=reply_text,
                            color=color,
                        )
                    )

                    if r.actions:
                        await server.broadcast(actions(items=r.actions))

                    # Request TTS explicitly so we can represent the build phase.
                    if reply_text:
                        vibe = (getattr(r, "pre_tts_vibe", None) or "normal")
                        cue_task = None
                        if _should_play_pre_tts(reply_text):
                            cue_task = asyncio.create_task(
                                _play_pre_tts_cue(
                                    persona_key=persona_key or "domino",
                                    vibe=vibe,
                                    output_device=output_device,
                                )
                            )

                        tts_task = asyncio.create_task(hub.tts(persona=persona_key or "domino", text=reply_text, session_id=session_id))
                        if cue_task is not None:
                            await cue_task
                        tts_res = await tts_task
                        if tts_res and tts_res.audio_b64:
                            await server.broadcast(
                                tts_audio(
                                    persona=persona_display_name(persona_key),
                                    color=color,
                                    format=_format_from_provider(tts_res.tts_provider),
                                    audio_b64=tts_res.audio_b64,
                                )
                            )
                            await server.broadcast(status(state="speaking", hint="Speaking…", color=color))
                            await _play_tts_audio_b64(tts_res.audio_b64)

                await server.broadcast(status(state="listening", hint="Listening for wake words", color="#FFFFFF"))
                continue
            else:
                # Normal single-persona response.
                reply_text = (primary.reply or "").strip()
                # Prefer hub-returned persona key for labeling when present.
                persona_key = (primary.persona or "").strip().casefold()
                label = persona_display_name(persona_key) if persona_key else persona_display_name(persona_mode)
                color = getattr(hit, "color", "#FFFFFF")

                await server.broadcast(
                    assistant_reply(
                        persona=label,
                        text=reply_text,
                        color=color,
                    )
                )

                if primary.actions:
                    await server.broadcast(actions(items=primary.actions))

                # Explicit TTS step (real build phase)
                await server.broadcast(status(state="tts_building", hint="Building voice…", color=color))
                tts_persona = persona_key or persona
                vibe = (getattr(primary, "pre_tts_vibe", None) or "normal")
                cue_task = None
                if _should_play_pre_tts(reply_text):
                    cue_task = asyncio.create_task(
                        _play_pre_tts_cue(
                            persona_key=tts_persona,
                            vibe=vibe,
                            output_device=output_device,
                        )
                    )

                tts_task = asyncio.create_task(hub.tts(persona=tts_persona, text=reply_text, session_id=session_id))
                if cue_task is not None:
                    await cue_task
                tts_res = await tts_task
                if tts_res and tts_res.audio_b64:
                    await server.broadcast(
                        tts_audio(
                            persona=label,
                            color=color,
                            format=_format_from_provider(tts_res.tts_provider),
                            audio_b64=tts_res.audio_b64,
                        )
                    )
                    await server.broadcast(status(state="speaking", hint="Speaking…", color=color))
                    await _play_tts_audio_b64(tts_res.audio_b64)
                else:
                    print(f"[m4] tts returned no audio persona={tts_persona!r}")

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
