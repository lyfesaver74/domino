from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel
import os
import tempfile

app = FastAPI(
    title="Whisper STT Service",
    description="Simple FastAPI wrapper around faster-whisper",
    version="0.1.0",
)

WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")


def _load_model() -> WhisperModel:
    try:
        return WhisperModel(
            WHISPER_MODEL_NAME,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    except Exception as e:
        # If CUDA init fails (common when cuDNN libs are missing/mismatched),
        # fall back to CPU so the service remains usable.
        if str(WHISPER_DEVICE).lower() == "cuda":
            print(f"[whisper-service] Failed to init CUDA model ({e}); falling back to CPU.")
            return WhisperModel(
                WHISPER_MODEL_NAME,
                device="cpu",
                compute_type="int8",
            )
        raise


# Load model once at startup
model = _load_model()


@app.get("/health")
async def health():
    return {"ok": True, "model": WHISPER_MODEL_NAME}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """
    Accepts an audio file and returns { "text": "..." }.
    """
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")

    # Save to a temp file because faster-whisper works with file paths
    try:
        suffix = os.path.splitext(file.filename or "")[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save temp file: {e}")

    try:
        segments, info = model.transcribe(tmp_path, beam_size=5)
        text_parts = [seg.text for seg in segments]
        text = " ".join(t.strip() for t in text_parts).strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return JSONResponse({"text": text})
