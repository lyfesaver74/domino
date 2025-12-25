## 01_PROJECT_ONE_LINER — What this is
Domino Hub is a Docker-orchestrated FastAPI “hub” that provides:
- a web console UI,
- persona-based LLM routing (Domino/Penny/Jimmy),
- memory (SQLite: promoted state + chat history + optional retrieval),
- Home Assistant actions execution,
- STT via Whisper microservice,
- TTS orchestration (Fish first, ElevenLabs fallback, else browser TTS).

It also includes:
- a separate Fish Voice Studio static UI served by Nginx that reverse-proxies to the Fish API server (CORS avoidance),
- a separate Wake Word PC app that listens for wake words, records audio, calls the hub for STT + ask, and broadcasts overlay events via WebSocket.
