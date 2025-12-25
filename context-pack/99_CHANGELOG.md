# 99_CHANGELOG

- 2025-12-25 — Context Pack v1 created from user-provided snapshot text (Domino Hub + Fish TTS + Whisper STT + Wake Word PC App).

- 2025-12-25 — wake-word-pc cleanup + diagnostics fixes:
  - Removed unused legacy recorder module [src/record_vad.py](../wake-word-pc/src/record_vad.py).
  - Removed unused helper `pick_color_for_persona` from [src/hub_client.py](../wake-word-pc/src/hub_client.py).
  - Fixed `CoreWSServer` demo flag/method name collision in [src/core_ws.py](../wake-word-pc/src/core_ws.py) (renamed flag + demo coroutine).
  - Fixed Pylance/sounddevice typing issues in [src/wake_vosk.py](../wake-word-pc/src/wake_vosk.py) via safe `Any` casts (no runtime behavior change).
  - Updated [src/hub_smoke_test.py](../wake-word-pc/src/hub_smoke_test.py) to handle `None` results from `HubClient.stt()` / `HubClient.ask()`.
