# 03_ARCHITECTURE — What talks to what

## Domino Hub stack (Docker)

Browser (console UI) → domino-hub (FastAPI)
domino-hub → whisper (STT) via WHISPER_URL/transcribe
domino-hub → fish-speech-server (TTS) via FISH_TTS_BASE_URL/v1/tts
domino-hub → OpenAI (Penny) when OPENAI_API_KEY set
domino-hub → Gemini (Jimmy) when GEMINI_API_KEY set
domino-hub → Local OpenAI-compatible (LM Studio / “mistral path”) via MISTRAL_BASE_URL

Key contract change:

- Text + voice can be split: `/api/ask` may be text-only (`tts=false`), then `/api/tts` produces audio for that text.
- Optional pre-roll cue: `/api/pre_tts` returns a URL under `/static/pre_tts` (shared by PC app + other clients).

## Fish Voice Studio UI

Browser (Fish UI) → fish-ui (nginx)
fish-ui proxies `/api/*` → `fish-speech-server:8080/*`

## Wake Word PC App

Wake word PC app listens locally → records audio → calls domino-hub:

- STT: POST /api/stt
- ask: POST /api/ask

Then broadcasts overlay events over local WebSocket to overlay UI and plays audio locally on the PC (overlay is visual-only).
