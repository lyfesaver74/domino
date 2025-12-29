# 15_KNOWN_BROKEN — Known mismatches / breakpoints (as stated)

## Fish workspace

- `services/tts-fish/docker-compose.yml` is intentionally a placeholder; the active stack is in `hub/docker-compose.yml`.
- If Fish fails to synth, the most common cause is missing/incorrect host mounts for `/opt/tts-fish/checkpoints` and `/opt/tts-fish/references`.

## Wake Word PC app

- ask payload in hub_client.py is {persona,text,room,session_id,context}; richer contract described in PROJECT.md not currently sent.
- NOTE: core_m2.py currently sends persona/text/session_id; room/context are available but not populated.
- TTS is now broadcast to the overlay as tts_audio and also played locally (overlay/browser audio is intentionally not used).
- Persona labeling now prefers hub-returned persona when present; collective fanout renders each persona reply with a best-effort persona→color map from settings.

- Desktop overlay host confusion: multiple copies of `HtmlWindowsOverlay.exe` may exist; ensure you’re running the Domino one.

## Domino Hub

- Local LM Studio base URL caveat when hub runs in Docker:
  - default 127.0.0.1 points to container, not host; host.docker.internal not guaranteed on Linux.

## VS Code / Copilot Chat

- If Copilot Chat starts failing with "Request Failed: 413" / "failed to parse request", it is typically an oversized context payload.
  - Start a new chat thread, avoid @workspace, avoid large selections/pastes, and retry with a small prompt.
  - VS Code: "Developer: Reload Window" often clears a stuck state.
