## 12_FISH_TTS_SERVICE — Workspace snapshot (Fish Speech)

High-level:
- Wrapper workspace includes:
  - upstream Fish-Speech under fish-speech/
  - local model weights under checkpoints/
  - local reference voices under references/
  - custom static UI + Nginx proxy under ui/
- Two official entrypoints:
  - Gradio WebUI (port 7860)
  - HTTP API server (port 8080)
- Custom UI served by Nginx on port 80 proxies /api/* to API server.

Repo structure (as described):
Root wrapper layer:
- checkpoints/
  - openaudio-s1-mini contains model.pth, codec.pth, config.json, tokenizer.tiktoken, special_tokens.json
- references/
  - each reference is a folder like domino_v1/ with sample.wav + sample.lab
- ui/
  - index.html (entire frontend)
  - nginx.conf (reverse proxy)
- docker-compose.yml (currently empty)
- test1.wav, test2.wav (test inputs)

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

Important mismatches / spaghetti risks (as provided):
- Root docker-compose.yml is empty (no services).
- Upstream compose base mounts are relative; if running compose from fish-speech dir, mounts may not map to real root checkpoints/references in wrapper workspace.
- Nginx proxies to fish-speech-server:8080 but upstream compose service name is server (DNS mismatch unless container_name configured).
- Custom UI delete is broken in two ways:
  - uses undefined API_BASE in one place
  - calls DELETE with query param (?id=...), but server expects BODY reference_id
