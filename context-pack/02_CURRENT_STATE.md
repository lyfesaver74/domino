# 02_CURRENT_STATE — Project Snapshot (what’s running, what talks to what)

## Workspace layout (Domino Hub root app)

Root app (FastAPI hub):

- main.py, personas.py, schemas.py, memory_store.py, tts_fish.py
- requirements.txt, Dockerfile, docker-compose.yml

Web console UI served by hub:

- console.html + console.js + console.css

Fish Voice Studio UI (separate nginx container):

- index.html + nginx.conf

Backup snapshot directory (workspace rollback only; not built into the hub image):

- _fallback_20251221_160851

---

## Containers, networks, ports, volumes (docker-compose)

All orchestrated via docker-compose.yml.

### Services

#### domino-hub

- Purpose: FastAPI server + web console + LLM routing + memory + HA actions + TTS orchestration
- Host port mapping: 2424:2424
- Networks:
  - proxy (external; used by Traefik)
  - ai (internal bridge; used to talk to Fish + Whisper)
- Persistence:
  - Mount: /opt/domino_hub/data:/data
  - Env: MEMORY_DB_PATH=/data/memory.db (SQLite survives rebuilds)
- Traefik labels:
  - Router host: chat.lyfesaver.net (HTTPS via Cloudflare certresolver)
  - Service port: 2424 inside container

#### whisper (service name whisper; container name whisper-service)

- Purpose: Speech-to-text microservice (hub calls it at WHISPER_URL)
- Host port mapping: 9000:9000
- GPU enabled: gpus: all
- Volume: whisper_cache:/root/.cache (model cache persists)
- Env includes WHISPER_MODEL=base.en, WHISPER_DEVICE=cuda, etc.

#### fish-speech-server

- Purpose: Fish TTS engine (hub calls it at <http://fish-speech-server:8080/>)
- Host port mapping: 18080:8080 (optional debugging)
- GPU enabled: gpus: all
- Volumes (critical):
  - /opt/tts-fish/checkpoints:/app/checkpoints
  - /opt/tts-fish/references:/app/references

#### fish-ui (nginx)

- Purpose: Serves Fish Voice Studio UI and reverse-proxies Fish API to avoid CORS
- Host port mapping: 18081:80
- Volumes:
  - UI directory mounted read-only into nginx web root (serves index.html + index.css + index.js)
  - nginx.conf mounted read-only into nginx config
- Nginx behavior: any request to /api/ is proxied to <http://fish-speech-server:8080/>

Note:

- The host UI directory path is configurable via `DOMINO_UI_DIR` in docker-compose.
  - Default: `/opt/domino_hub/ui`
  - Local dev: set `DOMINO_UI_DIR=./ui` (relative to the compose file)

### Networks / connectivity

- ai is the internal “AI backbone” bridge network.
  - DNS names usable from other containers: fish-speech-server, whisper (service name); container names may resolve depending on Docker config.
- proxy is an external network (Traefik uses it).

---

## Build/entrypoint (hub image)

Dockerfile:

- Base image: python:3.11-slim
- Installs dependencies from requirements.txt
- Copies into /app: main.py, personas.py, schemas.py, memory_store.py, console.html, tts_fish.py, and static
- Exposes port 2424
- Runs: python main.py

In main.py:

- `__main__` starts uvicorn on 0.0.0.0:${DOMINO_HUB_PORT} (default 2424) with reload=True.

Important consequence:

- _fallback_... directory is not copied into the container; it’s purely a workspace rollback snapshot.
