# 14_WAKE_WORD_PC_APP — Project map (current truth on disk)

Main entrypoint:

- core_m2.py (wake → record → STT → ask → broadcast overlay events)

Other modules:

- core_ws.py (WebSocket server; also runnable standalone demo sequence)
- wake_vosk.py (wake listener)
- recorder.py (record_command)
- hub_client.py (stt, ask)
- overlay_events.py (helpers/schema)
- overlay UI: index.html + overlay.js + styles.css

Runtime contracts:

- Overlay WebSocket (local): <ws://127.0.0.1:8765/ws> (settings.json → overlay_ws)
- Hub base URL: <http://192.168.0.121:2424> (settings.json → hub.base_url)
- Hub endpoints: POST /api/stt (multipart file WAV) and POST /api/ask (JSON)

Happy path:

- core_m2.py creates CoreWSServer + HubClient + VoskWakeListener
- waits for wake hit (async for hit in listener.listen())
- records audio with record_command(...) in worker thread
- sends WAV bytes to hub via HubClient.stt()
- sends text to hub via HubClient.ask()
- broadcasts overlay messages: status, assistant_reply, error (no tts_audio currently)

Overlay event schema:

- overlay_events.py defines helpers/types: status, wake, user_utterance, assistant_reply, tts_audio, actions, error
- Current emissions from core_m2.py:
  - Emits: status, wake, user_utterance, assistant_reply, actions, tts_audio, error
  - Also plays TTS audio locally when present (browser/overlay audio is intentionally not used).

Overlay HTML (how to work with it):

- Files live under wake-word-pc/overlay/Content/
  - index.html loads styles.css + overlay.js
  - settings.html + settings.js is a local tuning page (writes to localStorage and generates a preview URL)
- WebSocket URL resolution (overlay.js):
  1) window.DOMINO_WS_URL (set in index.html)
  2) ?ws=<ws://HOST:PORT/PATH> querystring
  3) default <ws://127.0.0.1:8765/ws>
- Debug + simulation:
  - ?debug=1 enables a small debug HUD and keeps visuals slightly active.
  - ?sim=1 runs simulated subtitles/animation without needing the websocket.
  - settings.html preview uses index.html?sim=1&debug=1 plus additional style params.
- Style tuning params supported by overlay.js (also persisted via settings.html/localStorage):
  - style=blocks|kitt
  - colGap, segGap, glow, idleGlow
  - backdrop (0..0.55)
  - subSize, subOffset, subShadow
- Important behavior notes:
  - The overlay currently reacts to: status, assistant_reply, tts_audio, error.
    - wake / user_utterance / actions are broadcast by the app but not rendered by overlay.js today.
  - overlay.js includes a host scaling workaround for “tiny viewport” CEF/overlay-host bugs; it may apply a CSS scale on #root.

Config + deps:

- settings.json: hub URL/paths, WS host/port/path, wake words/colors, audio device, Vosk model path, VAD thresholds
- requirements.txt includes: websockets, sounddevice, vosk, numpy, httpx, miniaudio
- Vosk model expected at vosk-model-small-en-us-0.15

Legacy / unused by current core_m2 path:

- record_vad.py appears unreferenced
- settings.py only used by hub_smoke_test.py
- audio_playback.py only used by hub_smoke_test.py (--play-audio)

Redundancies:

- Settings loading duplicated in core_m2.py and core_ws.py and also exists in settings.py (dataclass loader)
- WS event schema duplicated: core_ws.py demo dataclasses vs overlay_events.py “real” schema

Config keys likely unused:

- hub.health_path, hub.version_path, and sound_cues section appear unreferenced in src right now.
