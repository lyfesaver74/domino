# 06_MEMORY — Persistence, schema, and “memory” design

Memory engine: memory_store.py

SQLite DB path:

- MEMORY_DB_PATH (default memory.db beside code)
- In Docker Compose: persisted via /opt/domino_hub/data:/data and MEMORY_DB_PATH=/data/memory.db

Tables:

- sessions(session_id, created_at, last_seen)
- promoted_state(key, value_json, updated_at)
- chat_messages(id, session_id, persona, role, content, ts)
- chat_summaries(session_id, persona, summary, updated_at)

Retrieval:

- retrieval_meta(doc_id, updated_at, size_chars)
- retrieval_fts FTS5 virtual table: (doc_id UNINDEXED, title, content, tags)

FTS5 note:

- If SQLite build lacks FTS5, retrieval is silently disabled but app still runs.

Promoted state defaults (first run):

- timezone from TIMEZONE or TZ
- location from LOCATION
- preferred_units from PREFERRED_UNITS
- working_rules from WORKING_RULES
- tech_stack from TECH_STACK
- tts_overrides: per persona defaults from TTS_DOMINO, TTS_PENNY, TTS_JIMMY (default "auto")
- base_urls: snapshot of HA_BASE_URL, MISTRAL_BASE_URL, FISH_TTS_BASE_URL
- retrieval_enabled: default False

Chat history behavior:

- session_id is user-supplied and stored in browser localStorage by console UI.
- trim_history() creates a crude rolling summary string (no LLM summarization) and deletes older messages beyond CHAT_HISTORY_LAST_N while keeping a digest in chat_summaries.
