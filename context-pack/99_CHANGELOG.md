# 99_CHANGELOG

Context pack notes: the detailed changes are now summarized inline in the relevant docs (Current State / API / TTS / Wake Word / Known Broken).

## Key milestones (keep)

- 2025-12-25 — Context Pack v1 created.
- 2025-12-25 — Local LLM naming standardized to `MISTRAL_*` env vars; `/health` reports `mistral_base_url`.
- 2025-12-26 — HtmlWindowsOverlay (CEF 75) compatibility work: settings parsing hardened, spacing works without flexbox `gap`, backdrop dimming fixed.
- 2025-12-27 — TTS pipeline split: `/api/ask` supports text-only (`tts=false`), `/api/tts` generates audio; `/api/pre_tts` added for vibe cues served from `/static/pre_tts`.
