# 04_BACKEND — FastAPI Hub details

## Core module: main.py responsibilities

- HTTP API (FastAPI endpoints)
- LLM routing per persona (Domino/Penny/Jimmy)
- Actions execution (Home Assistant service calls)
- TTS (Fish first, ElevenLabs fallback, else browser)
- Memory (SQLite promoted state + chat history + optional retrieval/FTS)

## Persona routing

Persona definitions in personas.py:

- domino: llm="mistral"
- penny: llm="chatgpt"
- jimmy: llm="gemini"

Router logic in main.py:

- llm in ("mistral","lmstudio") uses local OpenAI-compatible backend (call_mistral()).

## LLM backends (main.py)

### Local OpenAI-compatible (“mistral/lmstudio path”)

- MISTRAL_BASE_URL default <http://127.0.0.1:1234/v1>
- MISTRAL_API_KEY default mistral-local
- MISTRAL_MODEL default mistral-nemo-base-2407
- Uses OpenAI(base_url=..., api_key=...) and chat.completions.create(...)

Note:

- inside Docker, 127.0.0.1 is the container itself. If LM Studio is on the host, set MISTRAL_BASE_URL to a host-reachable address (often LAN IP). On Linux, host.docker.internal is not guaranteed unless configured.

### OpenAI (Penny)

- OPENAI_API_KEY must be set to enable
- OPENAI_MODEL default gpt-4.1-mini

### Gemini (Jimmy)

- GEMINI_API_KEY must be set to enable
- GEMINI_MODEL default gemini-3-pro-preview

## “Context:” debug injection + cleaning

- Each LLM call helper appends “Context: user=…, room=…, noise_level=…” into model context.
- clean_reply_text() strips accidental echoes of those markers before returning to UI (and logs what was stripped).
