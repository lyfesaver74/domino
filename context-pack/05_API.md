# 05_API — Hub endpoints (main.py)

## STT

POST /api/stt → JSON { text }

- Sends audio file bytes to WHISPER_URL + /transcribe
- WHISPER_URL not set in compose by default; expected via .env

## Health + time

GET /health → JSON with:

- mistral_base_url, has_openai, gemini_enabled, ha_enabled, tts_provider, fish_enabled

GET /api/time → JSON with timezone + timestamps

- Uses promoted timezone if set; else server timezone.

## Chat (non-stream)

POST /api/ask → JSON response (schemas.py AskResponse)

Handles:

- single persona
- “auto” routing
- “collective” fan-out when multiple personas are mentioned or “the collective” appears

Includes optional audio_b64 + tts_provider in response

## Chat (streaming SSE)

POST /api/ask_stream → text/event-stream

Emits events:

- meta: { persona, targets }
- memory: promoted-state “suggested/applied/error” events (auto-promote)
- message: { persona, reply, actions }
- audio: { persona, audio_id, mime, tts_provider }
- error: { persona, error }
- done: { persona }

Audio retrieval:

GET /api/audio/{audio_id} → audio bytes

- Fish: audio/wav
- ElevenLabs: audio/mpeg

## Memory admin/user endpoints

GET /api/memory/promoted → promoted state dict
PATCH /api/memory/promoted → shallow merge + deep merge for tts_overrides and base_urls

Retrieval (FTS5-backed, optional):

- POST /api/memory/retrieval/upsert
- POST /api/memory/retrieval/query
- DELETE /api/memory/retrieval/{doc_id} (admin-gated)
- POST /api/memory/retrieval/purge (admin-gated)

Chat history:

- POST /api/memory/history/clear

Admin gate:

- MEMORY_ADMIN_ENABLED + header X-Admin-Token matching MEMORY_ADMIN_TOKEN
