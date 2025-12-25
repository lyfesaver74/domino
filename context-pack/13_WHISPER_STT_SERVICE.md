## 13_WHISPER_STT_SERVICE — Workspace snapshot

Files present:
- app.py, Dockerfile, requirements.txt

What it is:
- FastAPI wrapper around faster-whisper
- Model loads at startup (global singleton)

Endpoints:
- GET /health → {"ok": true, "model": "<WHISPER_MODEL_NAME>"}
- POST /transcribe (multipart field file) → {"text": "<transcribed text>"}

Behavior:
- Saves upload to temp file; runs model.transcribe(tmp_path, beam_size=5); concatenates segment text; deletes temp file best-effort.
- If WHISPER_DEVICE=cuda init fails, falls back to CPU int8 and continues.

Docker:
- Base: pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
- Installs ffmpeg
- Installs cudnn>=9,<10 via conda
- Env defaults:
  - WHISPER_MODEL=base.en
  - WHISPER_DEVICE=cpu
  - WHISPER_COMPUTE_TYPE=int8
- Runs uvicorn app:app --host 0.0.0.0 --port 9000
- Exposes 9000

Requirements:
- fastapi
- uvicorn[standard]
- faster-whisper
- python-multipart

Operational notes:
- No persistent storage by default (unless volume mount added); model caching is container-local unless mounted.
