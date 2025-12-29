## 12_FISH_TTS_SERVICE — Workspace snapshot (Fish Speech)

High-level:

- Fish Speech upstream lives under `services/tts-fish/fish-speech/`.
- Domino runs Fish (API server) + Fish UI under `hub/docker-compose.yml` (Fish wrapper-level compose is intentionally a placeholder).
- Fish exposes an HTTP API server on port 8080 in-container; the nginx UI proxies `/api/*` to avoid CORS.

Wrapper structure (relevant bits):

- `services/tts-fish/references/` — reference voice folders.
- Host-mounted model/reference paths used by compose: `/opt/tts-fish/checkpoints` and `/opt/tts-fish/references`.
- `services/tts-fish/ui/` — nginx-served UI.

Upstream Fish Speech codebase:
- fish-speech/ (upstream project)
- compose.yml and compose.base.yml (compose for “server” and “webui”)
- Dockerfile (multi-stage builds: webui/server/dev)
- pyproject.toml + uv.lock
- tools/ scripts, fish_speech/ package

Runtime topology:
- WebUI: 7860 in container, mapped by compose
  - entrypoint: uv run tools/run_webui.py ...
- API server: 8080 in container, mapped by compose
  - entrypoint: uv run tools/api_server.py --listen "0.0.0.0:8080" ...
- Custom UI assumes API at /api/v1/... and relies on nginx proxy.

Compose details (upstream):
- compose.yml defines webui and server services, extends app-base from compose.base.yml
- compose.base.yml mounts:
  - ./checkpoints:/app/checkpoints
  - ./references:/app/references
- GPU reserved via deploy.resources.reservations.devices
- Env: COMPILE=${COMPILE:-0}

API surface (from views.py summary):
- /v1/health (GET/POST) → JSON {"status":"ok"}
- /v1/vqgan/encode (POST) → msgpack
- /v1/vqgan/decode (POST) → msgpack
- /v1/tts (POST) → audio bytes (wav/mp3/flac); streaming only wav
- /v1/references/add (POST multipart: id, audio, text)
- /v1/references/list (GET)
- /v1/references/delete (DELETE expects BODY field reference_id; not query param)
- /v1/references/update (POST expects BODY fields old_reference_id, new_reference_id)

Notes / pitfalls:

- `services/tts-fish/docker-compose.yml` is intentionally empty; use `hub/docker-compose.yml` to run the stack.
- If Fish can’t find weights, check the host mounts for `/opt/tts-fish/checkpoints` and `/opt/tts-fish/references`.
