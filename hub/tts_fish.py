import base64
import os
from typing import Any, Dict, Optional, Tuple

import httpx


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


FISH_TTS_ENABLED: bool = _env_bool("FISH_TTS_ENABLED", False)

FISH_TTS_BASE_URL: str = os.getenv("FISH_TTS_BASE_URL", "http://fish-speech-server:8080").rstrip("/")
FISH_TTS_TIMEOUT: float = float(os.getenv("FISH_TTS_TIMEOUT", "120"))
FISH_TTS_FORMAT: str = os.getenv("FISH_TTS_FORMAT", "wav").lower()
FISH_TTS_NORMALIZE: bool = _env_bool("FISH_TTS_NORMALIZE", True)

# Optional per-persona reference IDs
FISH_REF_DOMINO: Optional[str] = os.getenv("FISH_REF_DOMINO")
FISH_REF_PENNY: Optional[str] = os.getenv("FISH_REF_PENNY")
FISH_REF_JIMMY: Optional[str] = os.getenv("FISH_REF_JIMMY")


async def tts_with_fish(
    text: str,
    reference_id: Optional[str] = None,
    *,
    chunk_length: int = 200,
    temperature: float = 0.8,
    top_p: float = 0.8,
    repetition_penalty: float = 1.1,
    max_new_tokens: int = 1024,
) -> Tuple[Optional[str], Optional[str]]:
    """Generate speech audio via Fish.

    Returns (audio_b64, provider_name). If generation fails, returns (None, None).
    """
    if not text:
        return None, None

    url = f"{FISH_TTS_BASE_URL}/v1/tts"

    payload: Dict[str, Any] = {
        "text": text,
        "chunk_length": chunk_length,
        "format": FISH_TTS_FORMAT,
        "references": [],
        "reference_id": reference_id,
        "seed": None,
        "use_memory_cache": "off",
        "normalize": FISH_TTS_NORMALIZE,
        "streaming": False,
        "max_new_tokens": max_new_tokens,
        "top_p": top_p,
        "repetition_penalty": repetition_penalty,
        "temperature": temperature,
    }

    timeout = httpx.Timeout(timeout=FISH_TTS_TIMEOUT, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        audio_bytes = resp.content

    if not audio_bytes:
        return None, None

    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    return audio_b64, "fish"
