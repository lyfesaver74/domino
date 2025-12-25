# 03_ARCHITECTURE — What talks to what

## Domino Hub stack (Docker)

Browser (console UI) → domino-hub (FastAPI)
domino-hub → whisper (STT) via WHISPER_URL/transcribe
domino-hub → fish-speech-server (TTS) via FISH_TTS_BASE_URL/v1/tts
domino-hub → OpenAI (Penny) when OPENAI_API_KEY set
domino-hub → Gemini (Jimmy) when GEMINI_API_KEY set
domino-hub → Local OpenAI-compatible (LM Studio / “mistral path”) via MISTRAL_BASE_URL

## Fish Voice Studio UI

Browser (Fish UI) → fish-ui (nginx)
fish-ui proxies `/api/*` → `fish-speech-server:8080/*`

## Wake Word PC App

Wake word PC app listens locally → records audio → calls domino-hub:

- STT: POST /api/stt
- ask: POST /api/ask

Then broadcasts overlay events over local WebSocket to overlay UI.
