## 14_WAKE_WORD_PC_APP — Project map (current truth on disk)

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
- Overlay WebSocket (local): ws://127.0.0.1:8765/ws (settings.json → overlay_ws)
- Hub base URL: http://192.168.0.121:2424 (settings.json → hub.base_url)
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
  - Emits: status, assistant_reply, error
  - Does NOT emit: wake, user_utterance, tts_audio, actions

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
