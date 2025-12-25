# 11_ENV_VARS — Important knobs (Hub)

## Hub runtime

- DOMINO_HUB_PORT (default 2424)
- MEMORY_DB_PATH (compose sets /data/memory.db)
- CHAT_HISTORY_LAST_N (default 16)
- CHAT_HISTORY_MAX_CHARS (default 6000)
- SESSION_MAX_AGE_DAYS (default 30)

## Auto-promote

- AUTO_PROMOTE_STATE_DEFAULT (default false) — used when UI doesn’t send context.extra.auto_promote

## Whisper

- WHISPER_URL (required to enable /api/stt)
- WHISPER_TIMEOUT (default 60)

## Local LLM (LM Studio / OpenAI-compatible)

- MISTRAL_BASE_URL (default <http://127.0.0.1:1234/v1>)
- MISTRAL_API_KEY (default mistral-local)
- MISTRAL_MODEL (default mistral-nemo-base-2407)

## OpenAI

- OPENAI_API_KEY (required to enable Penny)
- OPENAI_MODEL (default gpt-4.1-mini)

## Gemini

- GEMINI_API_KEY (required to enable Jimmy)
- GEMINI_MODEL (default gemini-3-pro-preview)

## Home Assistant actions

- HA_BASE_URL, HA_TOKEN, HA_TIMEOUT (default 5.0)

## Fish TTS

- FISH_TTS_ENABLED (default false unless set)
- FISH_TTS_BASE_URL (default <http://fish-speech-server:8080/>)
- FISH_TTS_TIMEOUT (default 120)
- FISH_TTS_FORMAT (default wav)
- FISH_TTS_NORMALIZE (default true)
- FISH_REF_DOMINO, FISH_REF_PENNY, FISH_REF_JIMMY

## ElevenLabs TTS

- ELEVENLABS_API_KEY
- ELEVENLABS_MODEL_ID (default eleven_multilingual_v2)
- ELEVENLABS_VOICE_DOMINO, ELEVENLABS_VOICE_PENNY, ELEVENLABS_VOICE_JIMMY

## Promoted-state seeding (only on first DB init)

- TIMEZONE/TZ, LOCATION, PREFERRED_UNITS, WORKING_RULES, TECH_STACK
- TTS_DOMINO, TTS_PENNY, TTS_JIMMY
