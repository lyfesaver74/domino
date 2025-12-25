from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx

from settings import load_settings
from hub_client import HubClient
from audio_playback import play_audio_bytes, sniff_audio_format


async def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test Domino-Hub /api/ask and /api/stt")
    parser.add_argument(
        "--settings",
        default=str(Path(__file__).resolve().parents[1] / "settings.json"),
        help="Path to settings.json (default: repo root settings.json)",
    )
    parser.add_argument("--persona", default="auto", help="Persona key: domino|penny|jimmy|auto")
    parser.add_argument("--text", default="Hello from wake-word-pc smoke test.", help="Text to send to /api/ask")
    parser.add_argument("--wav", default=None, help="Optional path to a WAV file to send to /api/stt")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override hub.base_url (example: http://127.0.0.1:2424 or https://chat.lyfesaver.net)",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=None,
        help="Override hub.timeout_s for this test (example: 3)",
    )
    parser.add_argument(
        "--play-audio",
        action="store_true",
        help="If /api/ask returns audio_b64, decode and play it locally (requires output device).",
    )
    args = parser.parse_args()

    settings_path = Path(args.settings)
    settings = load_settings(settings_path)

    base_url = (args.base_url or settings.hub.base_url).rstrip("/")

    timeout_s = float(args.timeout_s) if args.timeout_s is not None else settings.hub.timeout_s

    hub = HubClient(
        base_url=base_url,
        stt_path=settings.hub.stt_path,
        ask_path=settings.hub.ask_path,
        timeout_s=timeout_s,
    )

    print(f"[hub_smoke_test] hub.base_url = {base_url}")
    print(f"[hub_smoke_test] stt_path     = {settings.hub.stt_path}")
    print(f"[hub_smoke_test] ask_path     = {settings.hub.ask_path}")

    try:
        if args.wav:
            wav_path = Path(args.wav)
            wav_bytes = wav_path.read_bytes()
            print(f"[hub_smoke_test] sending STT wav: {wav_path} ({len(wav_bytes)} bytes)")
            stt_res = await hub.stt(wav_bytes=wav_bytes, filename=wav_path.name)
            if stt_res is None:
                print("[hub_smoke_test] /api/stt -> FAILED")
                return 5
            print(f"[hub_smoke_test] /api/stt -> text={stt_res.text!r}")

        print(f"[hub_smoke_test] sending ASK persona={args.persona!r} text={args.text!r}")
        ask_res = await hub.ask(
            persona=args.persona,
            text=args.text,
            session_id=settings.client.device,
        )
        if ask_res is None:
            print("[hub_smoke_test] /api/ask -> FAILED")
            return 6
    except httpx.ConnectError as exc:
        print(f"[hub_smoke_test] CONNECT FAILED: {exc}")
        return 2
    except httpx.ReadTimeout as exc:
        print(f"[hub_smoke_test] TIMEOUT: {exc}")
        return 3
    except Exception as exc:
        print(f"[hub_smoke_test] ERROR: {exc}")
        return 4

    # Primary
    p = ask_res.primary
    print("[hub_smoke_test] /api/ask primary persona=", p.persona)
    print("[hub_smoke_test] /api/ask primary reply  =", p.reply)
    print("[hub_smoke_test] /api/ask primary tts_provider=", p.tts_provider)
    if p.actions:
        print("[hub_smoke_test] /api/ask primary actions=", p.actions)
    if p.audio_b64:
        print("[hub_smoke_test] /api/ask primary audio_b64=(present)")
        if args.play_audio:
            import base64

            try:
                audio_bytes = base64.b64decode((p.audio_b64 or "").strip())
            except Exception as exc:
                print(f"[hub_smoke_test] audio_b64 decode failed: {exc}")
                audio_bytes = b""
            fmt = sniff_audio_format(audio_bytes)
            print(f"[hub_smoke_test] decoded audio bytes={len(audio_bytes)} format={fmt}")
            if audio_bytes:
                try:
                    await play_audio_bytes(audio_bytes)
                except Exception as exc:
                    print(f"[hub_smoke_test] local playback failed: {exc}")

    # Multi-response
    if ask_res.responses:
        print(f"[hub_smoke_test] /api/ask responses: {len(ask_res.responses)}")
        for r in ask_res.responses:
            print("  - persona=", r.persona, "reply=", (r.reply or "")[:120])

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
