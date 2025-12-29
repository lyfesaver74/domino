# 05_API — Hub endpoints (main.py)

## STT

POST /api/stt → JSON { text }

- Sends audio file bytes to WHISPER_URL + /transcribe
- WHISPER_URL not set in compose by default; expected via .env

Typical docker value: `WHISPER_URL=http://whisper:9000`

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

Supports a two-step voice pipeline:

- If request includes `tts=true` (or omits it depending on client), the response may include `audio_b64` + `tts_provider`.
- If request includes `tts=false`, response is text-only; client can later call `/api/tts`.

Response may include `pre_tts_vibe` hints for clients that want a short “pre-roll” cue.

## TTS

POST /api/tts → JSON { audio_b64, tts_provider, mime }

- Generates audio for provided text + persona.

GET /api/pre_tts → JSON { url, persona, vibe, variant }

- Returns a URL under `/static/pre_tts` that clients can fetch (optional cue / pre-roll).

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
