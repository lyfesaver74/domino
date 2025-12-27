# 99_CHANGELOG

- 2025-12-25 — Context Pack v1 created from user-provided snapshot text (Domino Hub + Fish TTS + Whisper STT + Wake Word PC App).

- 2025-12-25 — wake-word-pc cleanup + diagnostics fixes:
  - Removed unused legacy recorder module [src/record_vad.py](../wake-word-pc/src/record_vad.py).
  - Removed unused helper `pick_color_for_persona` from [src/hub_client.py](../wake-word-pc/src/hub_client.py).
  - Fixed `CoreWSServer` demo flag/method name collision in [src/core_ws.py](../wake-word-pc/src/core_ws.py) (renamed flag + demo coroutine).
  - Fixed Pylance/sounddevice typing issues in [src/wake_vosk.py](../wake-word-pc/src/wake_vosk.py) via safe `Any` casts (no runtime behavior change).
  - Updated [src/hub_smoke_test.py](../wake-word-pc/src/hub_smoke_test.py) to handle `None` results from `HubClient.stt()` / `HubClient.ask()`.

- 2025-12-25 — Local LLM migration + wake-word audio improvements:
  - Replaced legacy “Qwen” naming with “Mistral” naming across the hub and context-pack.
    - Local LLM env vars are now `MISTRAL_BASE_URL`, `MISTRAL_API_KEY`, `MISTRAL_MODEL`.
    - `/health` now reports `mistral_base_url`.
    - Domino persona routes through `llm="mistral"`.
  - Wake word PC app now tolerates longer generation times (default hub timeout increased) and broadcasts richer overlay events including `tts_audio` / `actions`.
  - Fish Voice Studio UI delete flow fixed to send `reference_id` in the DELETE request body.
  - Fish Voice Studio UI and Hub Console UI were modularized to remove inline CSS/JS; Fish UI now uses external `index.css`/`index.js`, and the `fish-ui` nginx container mounts the full UI directory to serve those assets.

- 2025-12-26 — Context-pack overlay notes + tooling recovery:
  - Updated wake-word app notes to reflect current overlay event emissions (wake/user_utterance/actions/tts_audio) and local TTS playback behavior.
  - Documented overlay HTML debugging/tuning workflow (WS URL precedence, ?debug=1, ?sim=1, settings.html tuning params, host scaling workaround).
  - Added a short Copilot Chat 413/"failed to parse request" recovery note under known-broken.

- 2025-12-26 — HtmlWindowsOverlay (CEF 75) overlay fixes + settings reliability:
  - Fixed `user-settings.js` typo (`flase`) that prevented settings from loading.
  - Hardened URL settings parsing so missing query params don't override user settings (`Number(null) -> 0` bug).
  - Made spacing settings work in CEF 75 by replacing unsupported flexbox `gap` with margin-based spacing (colGap/segGap).
  - Fixed backdrop dimming behavior so `backdrop` actually affects the dashboard behind the overlay (removed JS override + CSS specificity for connected/active).
  - Stabilized layout so 2-line subtitles don't push the KITT bar (reserved two-line subtitle block height).

- 2025-12-27 — Real `tts_building` + hub-hosted pre‑TTS vibe cues:
  - Split response generation into a two-step pipeline: `/api/ask` can return text-only via `tts=false`, and `/api/tts` generates audio for provided text.
  - Hub now includes a lightweight `pre_tts_vibe` hint on ask responses.
  - Added `/api/pre_tts` selector that returns a persona/vibe/variant cue URL served from `/static/pre_tts` (so ESP32 and PC app can share the same assets).
  - Wake-word PC app now does: ask(text-only) → overlay `tts_building` → (optional) fetch+play pre‑TTS cue → `/api/tts` → play final TTS.
  - Added dummy cue assets under `hub/static/pre_tts/` (45 silent WAVs) named `pre-tts-{D|P|J}-{vibe}-{1..3}.wav` for 5 vibes × 3 personas × 3 variants.
  - `/api/pre_tts` now auto-discovers available cue variants on disk (no hard-coded max like 3).
