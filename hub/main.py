from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import Any, Dict, List, Optional, cast
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import os
import re
import json
import base64
import asyncio
import time
import uuid
import random

import logging

import httpx
from dotenv import load_dotenv
from openai import OpenAI
import google.generativeai as genai
from threading import Lock


class _BroadcastBus:
    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue[Dict[str, Any]]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
        q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Dict[str, Any]]) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    async def publish(self, payload: Dict[str, Any]) -> None:
        async with self._lock:
            subs = list(self._subscribers)
        if not subs:
            return
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the oldest item to keep the stream alive for slow clients.
                try:
                    _ = q.get_nowait()
                except Exception:
                    pass
                try:
                    q.put_nowait(payload)
                except Exception:
                    pass


_broadcast_bus = _BroadcastBus()


logger = logging.getLogger("domino_hub")

from personas import PERSONAS
from schemas import (
    Context,
    AskRequest,
    Action,
    AskResponse,
    STTResponse,
    PromotedStatePatch,
    TTSTestRequest,
    TTSRequest,
    TTSResponse,
    RetrievalUpsertRequest,
    RetrievalQueryRequest,
)

from memory_store import MemoryStore
# --- Fish TTS (optional local engine) ---
try:
    from tts_fish import (
        tts_with_fish,
        FISH_TTS_ENABLED,
        FISH_REF_DOMINO,
        FISH_REF_PENNY,
        FISH_REF_JIMMY,
    )
except ImportError:
    # Fish not installed or not copied into the container; treat as disabled
    FISH_TTS_ENABLED = False
    FISH_REF_DOMINO = None
    FISH_REF_PENNY = None
    FISH_REF_JIMMY = None

    async def tts_with_fish(*args: Any, **kwargs: Any) -> tuple[Optional[str], Optional[str]]:
        return None, None



# -------------------------
# Load environment
# -------------------------
load_dotenv()


def _running_in_docker() -> bool:
    try:
        return Path("/.dockerenv").exists()
    except Exception:
        return False

app = FastAPI(
    title="Domino & Friends Hub",
    version="0.2.0",
    description="Routes requests to Domino (Mistral), Penny (ChatGPT), and Jimmy (Gemini).",
)

@app.post("/api/stt", response_model=STTResponse)
async def api_stt(file: UploadFile = File(...)) -> STTResponse:
    promoted = memory_store.get_promoted_state()
    base_urls = promoted.get("base_urls") or {}
    whisper_url = (base_urls.get("whisper") or "").strip() or WHISPER_URL

    # If promoted state contains the Docker-only DNS name ("whisper") but the hub
    # is running outside Docker, rewrite to localhost so STT keeps working.
    if not _running_in_docker() and re.match(r"^https?://whisper(?::\d+)?\b", whisper_url, flags=re.IGNORECASE):
        whisper_url = "http://127.0.0.1:9000"

    whisper_cfg = promoted.get("whisper_stt") or {}
    whisper_timeout = whisper_cfg.get("timeout_sec")
    whisper_timeout = WHISPER_TIMEOUT if whisper_timeout is None else float(whisper_timeout)

    if not whisper_url:
        raise HTTPException(status_code=500, detail="WHISPER_URL is not set")

    audio_bytes = await file.read()

    try:
        async with httpx.AsyncClient(timeout=whisper_timeout) as client:
            resp = await client.post(
                f"{whisper_url.rstrip('/')}/transcribe",
                files={"file": (file.filename or "audio", audio_bytes, file.content_type or "application/octet-stream")},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Whisper STT failed ({whisper_url}): {e}")

    text = (data.get("text") or "").strip()
    return STTResponse(text=text)


def _fish_base_url_from_promoted(promoted: Dict[str, Any]) -> str:
    base_urls = promoted.get("base_urls") or {}
    fish_url = (base_urls.get("fish") or "").strip() or (os.getenv("FISH_TTS_BASE_URL") or "").strip()
    return fish_url.rstrip("/")


@app.get("/api/fish/references/list")
async def api_fish_references_list() -> Any:
    promoted = memory_store.get_promoted_state()
    fish_base = _fish_base_url_from_promoted(promoted)
    if not fish_base:
        raise HTTPException(status_code=500, detail="Fish base URL is not set")

    url = f"{fish_base}/v1/references/list"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Fish references list failed: {e}")


@app.post("/api/fish/references/add")
async def api_fish_references_add(
    id: str = Form(...),
    text: str = Form(...),
    audio: UploadFile = File(...),
) -> Dict[str, Any]:
    promoted = memory_store.get_promoted_state()
    fish_base = _fish_base_url_from_promoted(promoted)
    if not fish_base:
        raise HTTPException(status_code=500, detail="Fish base URL is not set")

    voice_id = (id or "").strip()
    script = (text or "").strip()
    if not voice_id:
        raise HTTPException(status_code=400, detail="id is required")
    if not script:
        raise HTTPException(status_code=400, detail="text is required")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="audio is empty")

    url = f"{fish_base}/v1/references/add"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                data={"id": voice_id, "text": script},
                files={
                    "audio": (
                        audio.filename or "audio.wav",
                        audio_bytes,
                        audio.content_type or "application/octet-stream",
                    )
                },
                headers={"Accept": "application/json"},
            )
            if resp.status_code >= 400:
                detail = resp.text
                raise HTTPException(status_code=resp.status_code, detail=detail)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Fish references add failed: {e}")

    return {"ok": True, "id": voice_id}


@app.delete("/api/fish/references/delete")
async def api_fish_references_delete(request: Request) -> Dict[str, Any]:
    promoted = memory_store.get_promoted_state()
    fish_base = _fish_base_url_from_promoted(promoted)
    if not fish_base:
        raise HTTPException(status_code=500, detail="Fish base URL is not set")

    try:
        body = await request.json()
    except Exception:
        body = {}
    ref_id = (body.get("reference_id") if isinstance(body, dict) else None) or ""
    ref_id = str(ref_id).strip()
    if not ref_id:
        raise HTTPException(status_code=400, detail="reference_id is required")

    url = f"{fish_base}/v1/references/delete"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try JSON first
            resp = await client.request(
                "DELETE",
                url,
                json={"reference_id": ref_id},
                headers={"Accept": "application/json"},
            )
            if resp.status_code >= 400:
                # Fallback: form-urlencoded
                resp = await client.request(
                    "DELETE",
                    url,
                    data={"reference_id": ref_id},
                    headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                )
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Fish references delete failed: {e}")

    return {"ok": True, "reference_id": ref_id}


# -------------------------
# Memory store
# -------------------------

MEMORY_DB_PATH = Path(os.getenv("MEMORY_DB_PATH", str(Path(__file__).resolve().parent / "memory.db")))
memory_store = MemoryStore(MEMORY_DB_PATH)

CHAT_HISTORY_LAST_N = int(os.getenv("CHAT_HISTORY_LAST_N", "16"))
CHAT_HISTORY_MAX_CHARS = int(os.getenv("CHAT_HISTORY_MAX_CHARS", "6000"))

SESSION_MAX_AGE_DAYS = int(os.getenv("SESSION_MAX_AGE_DAYS", "30"))

RETRIEVAL_MAX_DOC_CHARS = int(os.getenv("RETRIEVAL_MAX_DOC_CHARS", "40000"))
RETRIEVAL_MAX_TOTAL_CHARS = int(os.getenv("RETRIEVAL_MAX_TOTAL_CHARS", "200000"))
RETRIEVAL_MAX_INJECT_CHARS = int(os.getenv("RETRIEVAL_MAX_INJECT_CHARS", "8000"))

MEMORY_ADMIN_ENABLED = os.getenv("MEMORY_ADMIN_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
MEMORY_ADMIN_TOKEN = os.getenv("MEMORY_ADMIN_TOKEN", "")

AUTO_PROMOTE_STATE_DEFAULT = os.getenv("AUTO_PROMOTE_STATE_DEFAULT", "false").strip().lower() in ("1", "true", "yes", "on")

WHISPER_URL = os.getenv("WHISPER_URL", "").strip().rstrip("/")
if not WHISPER_URL:
    # Prefer Docker DNS when running inside a container; otherwise use localhost.
    # This keeps both Docker deployments and local-dev runs working without extra config.
    try:
        if _running_in_docker():
            WHISPER_URL = "http://whisper:9000"
        else:
            WHISPER_URL = "http://127.0.0.1:9000"
    except Exception:
        WHISPER_URL = "http://127.0.0.1:9000"
WHISPER_TIMEOUT = float(os.getenv("WHISPER_TIMEOUT", "60"))


# -------------------------
# Pre-TTS cue assets (served from /static/pre_tts)
# -------------------------

# Cue files are served from: hub/static/pre_tts/
# Naming convention: pre-tts-{D|P|J}-{label}-{N}.wav
#
# IMPORTANT: There is intentionally no hard-coded max variant count.
# If you add 6 normals, 3 throat-clears, 12 inhales, etc., the hub will
# discover and randomly select among whatever exists on disk.
PRE_TTS_PERSONA_LETTERS = {
    "domino": "D",
    "penny": "P",
    "jimmy": "J",
}


_PRE_TTS_LABEL_SAFE_RE = re.compile(r"[^a-z0-9_-]+")


def _normalize_pre_tts_label(v: Optional[str]) -> str:
    # Allow arbitrary labels (e.g. normal|unsure|humor|surprise|irked|throat_clear|inhale).
    s = (v or "").strip().lower()
    if not s:
        return "normal"
    s = re.sub(r"\s+", "-", s)
    s = _PRE_TTS_LABEL_SAFE_RE.sub("", s)
    return s or "normal"


def classify_pre_tts_vibe(text: str) -> str:
    """Very small heuristic classifier.

    This is intentionally simple and debuggable; if you want better accuracy,
    we can later switch to an explicit model-produced tag.
    """
    t = (text or "").strip().lower()
    if not t:
        return "normal"

    # Unsure / hedging
    unsure_markers = (
        "i'm not sure",
        "im not sure",
        "i'm unsure",
        "i dont know",
        "i don't know",
        "hard to say",
        "it depends",
        "might be",
        "may be",
        "possibly",
        "likely",
        "i can't confirm",
        "i cannot confirm",
    )
    if any(m in t for m in unsure_markers):
        return "unsure"

    # Humor (light)
    humor_markers = (
        "haha",
        "lol",
        "that's funny",
        "thats funny",
        "joke",
        "pun",
    )
    if any(m in t for m in humor_markers):
        return "humor"

    # Surprise
    surprise_markers = (
        "wow",
        "surprisingly",
        "unexpected",
        "plot twist",
        "didn't expect",
        "did not expect",
    )
    if any(m in t for m in surprise_markers):
        return "surprise"

    # Irked / annoyed
    irked_markers = (
        "ugh",
        "annoying",
        "that's frustrating",
        "thats frustrating",
        "seriously",
    )
    if any(m in t for m in irked_markers):
        return "irked"

    return "normal"


def _pre_tts_list_candidates(*, static_dir: Path, persona_key: str, label: str) -> List[tuple[int, Path]]:
    p = (persona_key or "domino").strip().lower()
    letter = PRE_TTS_PERSONA_LETTERS.get(p, "D")
    l = _normalize_pre_tts_label(label)

    pre_tts_dir = static_dir / "pre_tts"
    if not pre_tts_dir.exists() or not pre_tts_dir.is_dir():
        return []

    # Use a regex for robust parsing of the numeric variant.
    # Example: pre-tts-D-normal-12.wav -> variant=12
    pat = re.compile(rf"^pre-tts-{re.escape(letter)}-{re.escape(l)}-(\\d+)\\.wav$", re.IGNORECASE)
    out: List[tuple[int, Path]] = []
    for pth in pre_tts_dir.glob(f"pre-tts-{letter}-{l}-*.wav"):
        m = pat.match(pth.name)
        if not m:
            continue
        try:
            v = int(m.group(1))
        except Exception:
            continue
        if v < 1:
            continue
        out.append((v, pth))

    out.sort(key=lambda t: t[0])
    return out


@app.get("/api/pre_tts")
async def api_pre_tts(persona: str = "domino", vibe: Optional[str] = None, variant: Optional[int] = None) -> Dict[str, Any]:
    """Pick a pre-TTS cue audio file.

    Returns a URL under /static/pre_tts that any client (PC app, ESP32) can fetch.
    """
    persona_key = (persona or "domino").strip().lower()
    static_dir = Path(STATIC_DIR)
    label = _normalize_pre_tts_label(vibe)

    candidates = _pre_tts_list_candidates(static_dir=static_dir, persona_key=persona_key, label=label)
    # Fall back to normal if the requested label is missing.
    if not candidates and label != "normal":
        label = "normal"
        candidates = _pre_tts_list_candidates(static_dir=static_dir, persona_key=persona_key, label=label)

    if not candidates:
        raise HTTPException(status_code=404, detail="No pre-TTS cue files found")

    chosen_variant: int
    chosen_path: Path

    if variant is not None:
        want = int(variant)
        exact = next(((v, pth) for (v, pth) in candidates if v == want), None)
        if exact is not None:
            chosen_variant, chosen_path = exact
        else:
            chosen_variant, chosen_path = random.choice(candidates)
    else:
        chosen_variant, chosen_path = random.choice(candidates)

    rel = Path("pre_tts") / chosen_path.name
    return {
        "ok": True,
        "persona": persona_key,
        "vibe": label,
        "variant": chosen_variant,
        "url": f"/static/{rel.as_posix()}",
        "mime": "audio/wav",
    }


def _session_id_from_req(req: AskRequest) -> str:
    # Prefer explicit session_id, then context.extra.session_id, else single-user default.
    if getattr(req, "session_id", None):
        return str(req.session_id)
    if req.context and isinstance(req.context.extra, dict):
        sid = req.context.extra.get("session_id")
        if sid:
            return str(sid)
    return "default"


def _build_memory_block(promoted: dict) -> str:
    tz = promoted.get("timezone")
    location = promoted.get("location")
    units = promoted.get("preferred_units")
    rules = promoted.get("working_rules")
    tech_stack = promoted.get("tech_stack")

    lines: list[str] = []
    if tz:
        lines.append(f"User timezone: {tz}.")
    if location:
        lines.append(f"User location: {location}.")
    if units:
        lines.append(f"Preferred units: {units}.")
    if rules:
        lines.append(f"Working rules: {rules}")

    if tech_stack:
        tech_stack_s = str(tech_stack).strip()
        if tech_stack_s:
            # Prevent unbounded prompt bloat.
            if len(tech_stack_s) > 1400:
                tech_stack_s = tech_stack_s[:1400] + "...[TRUNCATED]"
            lines.append(f"Tech stack: {tech_stack_s}")

    if not lines:
        return ""
    return "\n".join(lines)


def _render_chat_context(summary: str, turns: list[dict]) -> str:
    # Keep plain text only (personas demand no markdown).
    parts: list[str] = []
    if summary:
        parts.append(f"Earlier context: {summary}")
    if turns:
        parts.append("Recent turns:")
        for m in turns:
            role = (m.get("role") or "").strip().lower()
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                parts.append(f"User: {content}")
            else:
                parts.append(f"Assistant: {content}")
    return "\n".join(parts)


def _pick_tts_pref(promoted: dict, persona_key: str) -> str:
    overrides = promoted.get("tts_overrides") or {}
    val = overrides.get(persona_key)
    if not val:
        return "auto"
    val = str(val).lower().strip()
    if val in ("auto", "fish", "elevenlabs", "browser", "off"):
        return val
    return "auto"


def _now_for_promoted_timezone(promoted: dict) -> tuple[datetime, str]:
    tzname = (promoted.get("timezone") or "").strip()
    if tzname:
        try:
            tz = ZoneInfo(tzname)
            return datetime.now(tz), tzname
        except Exception:
            pass
    # Fallback: server local timezone
    dt = datetime.now().astimezone()
    tzinfo = dt.tzinfo
    tz_key = getattr(tzinfo, "key", None) if tzinfo is not None else None
    return dt, (tz_key or (dt.tzname() or "local"))


def _build_time_block(promoted: dict) -> str:
    dt, tzname = _now_for_promoted_timezone(promoted)
    stamp = dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"Current server time: {stamp} ({tzname})."


_CLOCK_Q_RE = re.compile(
    r"\b(what\s*'?s\s+the\s+time|what\s+time\s+is\s+it|current\s+time|time\s+now|tell\s+me\s+the\s+time)\b",
    re.IGNORECASE,
)


def _is_clock_question(user_text: str) -> bool:
    return bool(_CLOCK_Q_RE.search(user_text or ""))


def _clock_skill_reply(promoted: dict) -> str:
    dt, tzname = _now_for_promoted_timezone(promoted)
    stamp = dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"Current time: {stamp} ({tzname})."


def _require_memory_admin(request: Request) -> None:
    if not MEMORY_ADMIN_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    expected = (MEMORY_ADMIN_TOKEN or "").strip()
    provided = (request.headers.get("X-Admin-Token") or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


def _get_auto_promote_flag(req: AskRequest) -> bool:
    try:
        if req.context and isinstance(req.context.extra, dict) and "auto_promote" in req.context.extra:
            return bool(req.context.extra.get("auto_promote"))
    except Exception:
        pass
    return bool(AUTO_PROMOTE_STATE_DEFAULT)


def _infer_promoted_patch_from_text(user_text: str) -> tuple[Dict[str, Any], List[str]]:
    """Heuristic promoted-state inference from explicit user clarifications."""
    text = (user_text or "").strip()
    if not text:
        return {}, []

    patch: Dict[str, Any] = {}
    reasons: List[str] = []
    lowered = text.lower()

    # Location (store verbatim)
    m = re.search(r"\b(?:i\s*am|i'm|im)\s+in\s+([a-zA-Z][\w .,'-]{1,80})\b", text, flags=re.IGNORECASE)
    if m:
        loc = m.group(1).strip().rstrip(".")
        if loc:
            patch["location"] = loc
            reasons.append(f"Detected location: {loc}")

    # Timezone (map common US zones to IANA)
    tz_map = {
        "central": "America/Chicago",
        "eastern": "America/New_York",
        "mountain": "America/Denver",
        "pacific": "America/Los_Angeles",
        "utc": "UTC",
        "gmt": "UTC",
    }
    for key, val in tz_map.items():
        if re.search(rf"\b{re.escape(key)}\s+time\b", lowered):
            patch["timezone"] = val
            reasons.append(f"Detected timezone: {key} time → {val}")
            break
    if "timezone" not in patch:
        if re.search(r"\b(cst|cdt)\b", lowered):
            patch["timezone"] = "America/Chicago"
            reasons.append("Detected timezone: CST/CDT → America/Chicago")
        elif re.search(r"\b(est|edt)\b", lowered):
            patch["timezone"] = "America/New_York"
            reasons.append("Detected timezone: EST/EDT → America/New_York")
        elif re.search(r"\b(pst|pdt)\b", lowered):
            patch["timezone"] = "America/Los_Angeles"
            reasons.append("Detected timezone: PST/PDT → America/Los_Angeles")

    # Units
    if re.search(r"\bmetric\b", lowered):
        patch["preferred_units"] = "metric"
        reasons.append("Detected preferred units: metric")
    elif re.search(r"\b(imperial|us\s+customary)\b", lowered):
        patch["preferred_units"] = "imperial"
        reasons.append("Detected preferred units: imperial")

    # TTS overrides (simple patterns)
    provider_map = {
        "fish": "fish",
        "elevenlabs": "elevenlabs",
        "eleven labs": "elevenlabs",
        "browser": "browser",
        "off": "off",
        "disable": "off",
        "disabled": "off",
    }
    for persona in ("domino", "penny", "jimmy"):
        m1 = re.search(rf"\buse\s+([a-z ]+)\s+for\s+{persona}\b", lowered)
        m2 = re.search(rf"\bturn\s+([a-z ]+)\s+(?:tts\s+)?for\s+{persona}\b", lowered)
        m3 = re.search(rf"\b{persona}\b[^\n\r]*\b(use|tts)\b[^\n\r]*\b([a-z ]+)\b", lowered)

        cand = None
        if m1:
            cand = (m1.group(1) or "").strip()
        elif m2:
            cand = (m2.group(1) or "").strip()
        elif m3:
            cand = (m3.group(2) or "").strip()

        if not cand:
            continue

        chosen = None
        for k, v in provider_map.items():
            if k in cand:
                chosen = v
                break
        if chosen:
            patch.setdefault("tts_overrides", {})
            patch["tts_overrides"][persona] = chosen
            reasons.append(f"Detected TTS override: {persona} → {chosen}")

    return patch, reasons


# -------------------------
# LLM client setup
# -------------------------

# Local OpenAI-compatible endpoint (LM Studio).
MISTRAL_BASE_URL = os.getenv("MISTRAL_BASE_URL", "http://127.0.0.1:1234/v1")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "mistral-local")
# LM Studio model identifier (also used as the mode name)
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-nemo-base-2407")

# Safety/perf guards for local models.
# Without an explicit cap, some OpenAI-compatible servers default to unlimited generation
# (e.g., n_predict=-1) which can lead to repeated "context full, shifting" loops.
MISTRAL_TIMEOUT_S = float(os.getenv("MISTRAL_TIMEOUT_S", "90"))
MISTRAL_MAX_TOKENS = int(os.getenv("MISTRAL_MAX_TOKENS", "256"))

_mistral_clients_lock = Lock()
_mistral_clients: Dict[str, OpenAI] = {}


def _get_mistral_client(base_url: str) -> OpenAI:
    url = (base_url or "").strip() or MISTRAL_BASE_URL
    with _mistral_clients_lock:
        client = _mistral_clients.get(url)
        if client is None:
            client = OpenAI(base_url=url, api_key=MISTRAL_API_KEY, timeout=MISTRAL_TIMEOUT_S)
            _mistral_clients[url] = client
        return client

# ChatGPT (Penny)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
openai_client: Optional[OpenAI] = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Gemini (Jimmy)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")
gemini_enabled = False
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_enabled = True

# -------------------------
# Home Assistant config
# -------------------------

HA_BASE_URL = os.getenv("HA_BASE_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
HA_TIMEOUT = float(os.getenv("HA_TIMEOUT", "5.0"))
ha_enabled = bool(HA_BASE_URL and HA_TOKEN)

# -------------------------
# TTS config (ElevenLabs / browser; Fish is stubbed)
# -------------------------

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "browser").lower()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
ELEVENLABS_VOICES = {
    "domino": os.getenv("ELEVENLABS_VOICE_DOMINO"),
    "penny": os.getenv("ELEVENLABS_VOICE_PENNY"),
    "jimmy": os.getenv("ELEVENLABS_VOICE_JIMMY"),
}

# -------------------------
# Helpers: actions / cleaning / TTS
# -------------------------

# Match anything inside <actions>...</actions>. We will validate the payload after parsing.
ACTIONS_RE = re.compile(r"<actions>\s*(.*?)\s*</actions>", re.IGNORECASE | re.DOTALL)
# Allow addressing like "Penny, ...", "Penny: ...", or "Penny ..." (no punctuation).
AUTO_PERSONA_RE = re.compile(
    r"^\s*(domino|penny|jimmy)\b(?:\s*[:,;—–-]\s+|\s+)",
    re.IGNORECASE,
)
# Accept both "the collective" and "collective".
COLLECTIVE_RE = re.compile(r"\b(?:the\s+)?collective\b", re.IGNORECASE)


# -------------------------
# Audio store (for streaming)
# -------------------------

_AUDIO_TTL_SECONDS = int(os.getenv("AUDIO_TTL_SECONDS", "600"))
_AUDIO_MAX_ITEMS = int(os.getenv("AUDIO_MAX_ITEMS", "50"))
_audio_store_lock = asyncio.Lock()
_audio_store: Dict[str, Dict[str, Any]] = {}


def _mime_from_provider(provider: Optional[str]) -> str:
    p = (provider or "").lower()
    if p == "elevenlabs":
        return "audio/mpeg"
    # Fish returns WAV in this stack
    return "audio/wav"


async def _audio_store_put(audio_b64: str, provider: Optional[str]) -> Optional[Dict[str, str]]:
    if not audio_b64:
        return None

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception:
        return None

    audio_id = uuid.uuid4().hex
    now = time.time()
    mime = _mime_from_provider(provider)

    async with _audio_store_lock:
        # purge expired
        expired = [k for k, v in _audio_store.items() if now - float(v.get("ts", 0.0)) > _AUDIO_TTL_SECONDS]
        for k in expired:
            _audio_store.pop(k, None)

        # cap size (drop oldest)
        if len(_audio_store) >= _AUDIO_MAX_ITEMS:
            oldest = sorted(_audio_store.items(), key=lambda kv: float(kv[1].get("ts", 0.0)))
            for k, _ in oldest[: max(1, len(_audio_store) - _AUDIO_MAX_ITEMS + 1)]:
                _audio_store.pop(k, None)

        _audio_store[audio_id] = {
            "ts": now,
            "bytes": audio_bytes,
            "mime": mime,
            "provider": provider or "",
        }

    return {"audio_id": audio_id, "mime": mime}


@app.get("/api/audio/{audio_id}")
async def api_get_audio(audio_id: str):
    now = time.time()
    async with _audio_store_lock:
        item = _audio_store.get(audio_id)
        if not item:
            raise HTTPException(status_code=404, detail="Audio not found")

        ts = float(item.get("ts", 0.0))
        if now - ts > _AUDIO_TTL_SECONDS:
            _audio_store.pop(audio_id, None)
            raise HTTPException(status_code=404, detail="Audio expired")

        audio_bytes: bytes = item["bytes"]
        mime: str = item.get("mime") or "application/octet-stream"

    return Response(content=audio_bytes, media_type=mime)


def _mentioned_personas_in_order(text: str) -> List[str]:
    if not text:
        return []

    t = text.lower()
    hits: List[tuple[int, str]] = []
    for name in ("domino", "penny", "jimmy"):
        m = re.search(rf"\b{name}\b", t)
        if m:
            hits.append((m.start(), name))

    hits.sort(key=lambda x: x[0])
    return [name for _, name in hits]


def _strip_collective_addressing(text: str) -> str:
    """Remove leading addressed names / 'the collective' so models don't parrot it back."""
    if not text:
        return text

    s = text.strip()
    # Remove any leading name list like "Jimmy, Domino, Penny..." in any order.
    # Also strip joiners/punctuators (commas, colons, dashes, ellipsis) between names.
    name_re = re.compile(r"^\s*(domino|penny|jimmy)\b", re.IGNORECASE)
    collective_re = re.compile(r"^\s*(?:the\s+)?collective\b", re.IGNORECASE)
    joiner_re = re.compile(r"^\s*(?:and|&|\+)\b\s*", re.IGNORECASE)
    punct_re = re.compile(r"^\s*(?:,|:|;|—|–|-|\.{3,}|…|\.)\s*")

    while True:
        before = s
        # Strip collective keyword
        s = collective_re.sub("", s, count=1)
        # Strip a persona name
        s = name_re.sub("", s, count=1)
        # Strip common separators repeatedly
        s = joiner_re.sub("", s)
        s = punct_re.sub("", s)
        s = s.lstrip()
        if s == before:
            break

    return s


def resolve_auto_persona(text: str) -> tuple[Optional[str], str]:
    """Resolve an explicit persona callout at the start of the user text.

    Examples:
      - "Penny, when was the Alamo built" -> ("penny", "when was the Alamo built")
      - "Domino: lights on" -> ("domino", "lights on")
      - no match -> (None, original_text)
    """
    if not text:
        return None, text

    match = AUTO_PERSONA_RE.match(text)
    if not match:
        return None, text

    persona = match.group(1).lower()
    stripped = text[match.end() :].lstrip()
    return persona, stripped


def clean_reply_text(text: str) -> str:
    """
    Clean model output for display / TTS:
    - Strip <think>...</think> reasoning blocks
    - Remove simple markdown markers (*, **, _, `)
    - Remove bullet prefixes and collapse to a single paragraph
    """
    if not text:
        return text

    # Some upstream OpenAI-compatible servers (or proxies) return *error dumps as
    # normal assistant text* (e.g. "### Error: ..." followed by a traceback).
    # Never show those to end users; truncate at the first sign of a dump.
    #
    # Note: We intentionally do this before any other transformations.
    dump_markers = (
        "### error:",
        "traceback (most recent call last)",
        "from langchain",
        "chatopenai",
        "asyncstreamingllmchain",
        "openai_api_key",
        "langchain.",
    )

    # Normalize HTML-ish line breaks that can appear from some clients.
    normalized = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    cut_at: Optional[int] = None
    lowered = normalized.lower()
    for marker in dump_markers:
        idx = lowered.find(marker)
        if idx != -1:
            cut_at = idx if cut_at is None else min(cut_at, idx)

    if cut_at is not None:
        removed = normalized[cut_at:]
        kept = normalized[:cut_at].rstrip()
        logger.warning(
            "[DominoHub] Stripped upstream error dump from reply (kept=%d chars, removed starts=%r)",
            len(kept),
            removed[:200],
        )
        text = kept
    else:
        text = normalized

    # 0) Strip any accidental debug/context echoes from the model output.
    # These should never be user-facing, but some models will occasionally repeat system context.
    # We strip even when embedded mid-sentence, and we tolerate variants like:
    #   "### Context: user = Lyfe, room=office" or "context:user=...".
    stripped: list[str] = []
    kept_lines: list[str] = []

    context_cut_re = re.compile(r"(?:#+\s*)?context\s*:\s*user\s*=", re.IGNORECASE)
    noise_cut_re = re.compile(r"noise\s*[_-]?\s*level\s*=", re.IGNORECASE)

    for line in text.splitlines():
        cut_idx = -1

        m_ctx = context_cut_re.search(line)
        if m_ctx:
            cut_idx = m_ctx.start()

        m_noise = noise_cut_re.search(line)
        if m_noise:
            cut_idx = m_noise.start() if cut_idx == -1 else min(cut_idx, m_noise.start())

        if cut_idx != -1:
            removed = line[cut_idx:].strip()
            if removed:
                stripped.append(removed)
            line = line[:cut_idx].rstrip()

        kept_lines.append(line)

    if stripped:
        for s in stripped:
            logger.info("[DominoHub] Stripped debug context from reply: %s", s[:400])

        text = "\n".join(kept_lines)

    # 1) Drop any <think>...</think> blocks (Domino / reasoning models)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 1b) Drop any <actions>...</actions> blocks (even if malformed)
    text = ACTIONS_RE.sub("", text)

    # 2) Remove simple markdown decoration
    text = re.sub(r"(\*\*|\*|__|_|`)", "", text)

    # 3) Strip transcript echoes and collapse lines.
    lines: list[str] = []
    for line in text.splitlines():
        # Some local models will echo a chat transcript or debug labels. Strip aggressively.
        if re.match(r"^\s*(context\s*:|user\s*:|assistant\s*:)", line, flags=re.IGNORECASE):
            continue
        if re.match(r"^\s*#+\s*action\s*:\s*$", line, flags=re.IGNORECASE):
            continue
        if re.match(r"^\s*action\s*:\s*$", line, flags=re.IGNORECASE):
            continue
        line = re.sub(r"^\s*[-•]\s+", "", line)
        if line.strip():
            lines.append(line.strip())

    # Single paragraph so TTS doesn't sound like it's reading a grocery list
    return " ".join(lines)


def extract_actions(text: str) -> tuple[str, List[Action]]:
    """
    Pull out <actions>[ ... ]</actions> JSON, return (cleaned_text, actions).
    If parsing fails, we just drop actions and return the original text.
    """
    if not text:
        return text, []

    match = ACTIONS_RE.search(text)
    if not match:
        return text, []

    json_blob = (match.group(1) or "").strip()

    actions: List[Action] = []
    if json_blob:
        try:
            raw = json.loads(json_blob)
            raw_items = raw if isinstance(raw, list) else [raw]
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                # Only accept valid Action objects.
                if "type" not in item or "data" not in item:
                    continue
                actions.append(Action(**item))
        except Exception as e:  # noqa: BLE001
            # Intentionally non-fatal: we still strip the <actions> block so it isn't shown/spoken.
            print(f"[DominoHub] Failed to parse actions: {e}")

    cleaned = ACTIONS_RE.sub("", text).strip()
    return cleaned, actions


async def execute_actions(actions: List[Action]) -> None:
    """
    Send actions to Home Assistant. Right now we only support ha_call_service.
    """
    if not ha_enabled or not actions:
        return

    base_url = HA_BASE_URL
    if not base_url:
        return

    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=HA_TIMEOUT) as client:
        for action in actions:
            if action.type != "ha_call_service":
                continue
            data = action.data or {}
            service = data.get("service")
            entity_id = data.get("entity_id")
            service_data = data.get("service_data") or {}

            if not service or not entity_id:
                continue

            try:
                domain, service_name = service.split(".", 1)
            except ValueError:
                print(f"[DominoHub] Bad service format: {service}")
                continue

            payload: Dict[str, Any] = {"entity_id": entity_id}
            if isinstance(service_data, dict):
                payload.update(service_data)

            url = f"{base_url.rstrip('/')}/api/services/{domain}/{service_name}"
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001
                print(f"[DominoHub] Error calling HA service {service} on {entity_id}: {e}")


async def generate_tts(persona: str, text: str, tts_pref: str = "auto") -> tuple[Optional[str], Optional[str]]:
    """
    Optionally generate TTS audio for the reply.
    Returns (audio_b64, provider_name) or (None, None) if disabled.

        POLICY:
            1) Try Fish first (if enabled)
            2) If Fish fails, try ElevenLabs (if configured)
            3) If ElevenLabs fails, return (None, None) and let the browser do TTS
    """
    if not text:
        return None, None

    persona_key = (persona or "").lower()

    promoted = memory_store.get_promoted_state()
    base_urls = promoted.get("base_urls") or {}
    fish_cfg = promoted.get("fish_tts") or {}
    fish_refs = (fish_cfg.get("refs") or {}) if isinstance(fish_cfg, dict) else {}

    fish_ref_map = {
        "domino": fish_refs.get("domino") or FISH_REF_DOMINO,
        "penny": fish_refs.get("penny") or FISH_REF_PENNY,
        "jimmy": fish_refs.get("jimmy") or FISH_REF_JIMMY,
    }

    tts_pref = (tts_pref or "auto").lower().strip()
    if tts_pref == "off":
        return None, None
    if tts_pref == "browser":
        return None, None

    # ---- 1) Fish first (all personas) ----
    if FISH_TTS_ENABLED and tts_pref in ("auto", "fish"):
        try:
            fish_base_url = (base_urls.get("fish") or "").strip() or None
            fish_kwargs: Dict[str, Any] = {}
            if isinstance(fish_cfg, dict):
                for key in ("chunk_length", "temperature", "top_p", "repetition_penalty", "max_new_tokens"):
                    if fish_cfg.get(key) is not None:
                        fish_kwargs[key] = fish_cfg.get(key)
                if fish_cfg.get("timeout_sec") is not None:
                    fish_kwargs["timeout_sec"] = fish_cfg.get("timeout_sec")
                if fish_cfg.get("format"):
                    fish_kwargs["audio_format"] = fish_cfg.get("format")
                if fish_cfg.get("normalize") is not None:
                    fish_kwargs["normalize"] = fish_cfg.get("normalize")

            audio_b64, provider_name = await tts_with_fish(
                text,
                reference_id=fish_ref_map.get(persona_key),
                base_url=fish_base_url,
                **fish_kwargs,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[DominoHub] Fish TTS error: {e}")
            audio_b64, provider_name = None, None

        if audio_b64:
            return audio_b64, provider_name or "fish"

    # ---- 2) ElevenLabs fallback (if configured) ----
    if tts_pref in ("auto", "elevenlabs"):
        api_key = ELEVENLABS_API_KEY
        voice_id = ELEVENLABS_VOICES.get(persona_key)
        if not api_key or not voice_id:
            return None, None

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": ELEVENLABS_MODEL_ID,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                audio_bytes = resp.content
        except Exception as e:  # noqa: BLE001
            print(f"[DominoHub] ElevenLabs TTS failed: {e}")
            return None, None

        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        return audio_b64, "elevenlabs"

    return None, None


@app.post("/api/tts/test")
async def api_tts_test(req: TTSTestRequest) -> Dict[str, Any]:
    provider = (req.provider or "fish").lower().strip()
    if provider != "fish":
        raise HTTPException(status_code=400, detail="Only provider='fish' is supported for /api/tts/test")

    persona = (req.persona or "domino").lower().strip()
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    promoted = memory_store.get_promoted_state()
    base_urls = promoted.get("base_urls") or {}
    fish_cfg = promoted.get("fish_tts") or {}
    fish_refs = (fish_cfg.get("refs") or {}) if isinstance(fish_cfg, dict) else {}

    reference_id = req.reference_id
    if not reference_id:
        reference_id = fish_refs.get(persona)
    if not reference_id:
        if persona == "domino":
            reference_id = FISH_REF_DOMINO
        elif persona == "penny":
            reference_id = FISH_REF_PENNY
        elif persona == "jimmy":
            reference_id = FISH_REF_JIMMY

    fish_base_url = (base_urls.get("fish") or "").strip() or None
    fish_kwargs: Dict[str, Any] = {}
    if isinstance(fish_cfg, dict):
        for key in ("chunk_length", "temperature", "top_p", "repetition_penalty", "max_new_tokens"):
            if fish_cfg.get(key) is not None:
                fish_kwargs[key] = fish_cfg.get(key)
        if fish_cfg.get("timeout_sec") is not None:
            fish_kwargs["timeout_sec"] = fish_cfg.get("timeout_sec")
        if fish_cfg.get("format"):
            fish_kwargs["audio_format"] = fish_cfg.get("format")
        if fish_cfg.get("normalize") is not None:
            fish_kwargs["normalize"] = fish_cfg.get("normalize")

    try:
        audio_b64, provider_name = await tts_with_fish(
            text,
            reference_id=reference_id,
            base_url=fish_base_url,
            **fish_kwargs,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Fish TTS failed: {e}")

    if not audio_b64:
        raise HTTPException(status_code=502, detail="Fish TTS returned no audio")

    stored = await _audio_store_put(audio_b64, provider_name or "fish")
    if not stored:
        raise HTTPException(status_code=500, detail="Failed to store audio")

    return {
        "ok": True,
        "tts_provider": provider_name or "fish",
        "audio_id": stored["audio_id"],
        "mime": stored["mime"],
    }


@app.post("/api/tts", response_model=TTSResponse)
async def api_tts(req: TTSRequest) -> TTSResponse:
    persona = (req.persona or "domino").lower().strip()
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    session_id = (req.session_id or "default").strip() or "default"
    try:
        memory_store.touch_session(session_id, max_age_days=SESSION_MAX_AGE_DAYS)
    except Exception:
        pass

    promoted = memory_store.get_promoted_state()
    tts_pref = (req.tts_pref or "").strip() or _pick_tts_pref(promoted, persona)

    audio_b64, provider = await generate_tts(persona, text, tts_pref=tts_pref)
    return TTSResponse(audio_b64=audio_b64, tts_provider=provider)


@app.get("/api/memory/promoted")
async def api_get_promoted_state() -> Dict[str, Any]:
    return memory_store.get_promoted_state()


@app.patch("/api/memory/promoted")
async def api_patch_promoted_state(patch: PromotedStatePatch, session_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        memory_store.touch_session(session_id or "default", max_age_days=SESSION_MAX_AGE_DAYS)
    except Exception:
        pass
    return memory_store.patch_promoted_state(patch.model_dump(exclude_unset=True))


@app.post("/api/memory/retrieval/upsert")
async def api_retrieval_upsert(req: RetrievalUpsertRequest) -> Dict[str, Any]:
    try:
        memory_store.touch_session(getattr(req, "session_id", None) or "default", max_age_days=SESSION_MAX_AGE_DAYS)
    except Exception:
        pass
    if not memory_store.retrieval_available():
        raise HTTPException(status_code=400, detail="Retrieval store is unavailable (FTS5 not enabled in SQLite)")
    content = req.content or ""
    if RETRIEVAL_MAX_DOC_CHARS > 0 and len(content) > RETRIEVAL_MAX_DOC_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Retrieval doc too large (chars={len(content)} > max={RETRIEVAL_MAX_DOC_CHARS})",
        )

    memory_store.upsert_retrieval_doc(req.doc_id, req.title or "", content, req.tags)
    removed = 0
    if RETRIEVAL_MAX_TOTAL_CHARS > 0:
        removed = memory_store.prune_retrieval_to_max_chars(RETRIEVAL_MAX_TOTAL_CHARS)
    return {"ok": True, "doc_id": req.doc_id, "pruned_docs": removed}


@app.delete("/api/memory/retrieval/{doc_id}")
async def api_retrieval_delete(doc_id: str, request: Request) -> Dict[str, Any]:
    _require_memory_admin(request)
    memory_store.delete_retrieval_doc(doc_id)
    return {"ok": True, "doc_id": doc_id}


@app.post("/api/memory/retrieval/purge")
async def api_retrieval_purge(request: Request) -> Dict[str, Any]:
    _require_memory_admin(request)
    memory_store.purge_retrieval()
    return {"ok": True}


@app.post("/api/memory/retrieval/query")
async def api_retrieval_query(req: RetrievalQueryRequest) -> Dict[str, Any]:
    try:
        memory_store.touch_session(getattr(req, "session_id", None) or "default", max_age_days=SESSION_MAX_AGE_DAYS)
    except Exception:
        pass
    hits = memory_store.query_retrieval(req.query, limit=req.limit)
    return {
        "ok": True,
        "hits": [
            {
                "doc_id": h.doc_id,
                "title": h.title,
                "content": h.content,
                "tags": h.tags,
                "score": h.score,
                "updated_at": h.updated_at,
            }
            for h in hits
        ],
    }


@app.post("/api/memory/history/clear")
async def api_history_clear(session_id: Optional[str] = None) -> Dict[str, Any]:
    memory_store.clear_history(session_id or "default")
    return {"ok": True}


# -------------------------
# LLM call helpers
# -------------------------

async def call_mistral(system_prompt: str, user_text: str, context: Optional[Context], *, base_url: str) -> str:
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    if context:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"Context: user={context.user}, room={context.room}, "
                    f"noise_level={context.noise_level}, extra={context.extra}"
                ),
            }
        )
    messages.append({"role": "user", "content": user_text})

    def _do_call() -> str:
        client = _get_mistral_client(base_url)
        try:
            max_tokens = int(MISTRAL_MAX_TOKENS)
            if max_tokens < 1:
                max_tokens = 1
            if max_tokens > 4096:
                max_tokens = 4096

            resp = client.chat.completions.create(
                model=MISTRAL_MODEL,
                messages=cast(Any, messages),
                temperature=0.6,
                max_tokens=max_tokens,
                # Light stop strings to reduce the chance of the model continuing forever
                # in OpenAI-compatible servers that don't enforce good defaults.
                stop=["\n\nUser:", "\n\nSystem:", "\n\nAssistant:"] ,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            msg = str(e) or type(e).__name__
            raise RuntimeError(f"Mistral call failed (base_url={base_url}): {msg}") from e

    # OpenAI-compatible clients are synchronous; run in a thread so SSE can flush.
    return await asyncio.to_thread(_do_call)


async def call_chatgpt(system_prompt: str, user_text: str, context: Optional[Context]) -> str:
    if not openai_client:
        raise HTTPException(
            status_code=500,
            detail="Penny (ChatGPT) is not configured. Set OPENAI_API_KEY in your environment.",
        )

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    if context:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"Context: user={context.user}, room={context.room}, "
                    f"noise_level={context.noise_level}, extra={context.extra}"
                ),
            }
        )
    messages.append({"role": "user", "content": user_text})

    client = openai_client

    def _do_call() -> str:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=cast(Any, messages),
            temperature=0.5,
        )
        return resp.choices[0].message.content or ""

    return await asyncio.to_thread(_do_call)


async def call_gemini(system_prompt: str, user_text: str, context: Optional[Context]) -> str:
    if not gemini_enabled:
        raise HTTPException(
            status_code=500,
            detail="Jimmy (Gemini) is not configured. Set GEMINI_API_KEY in your environment.",
        )

    parts = [system_prompt]
    if context:
        parts.append(
            f"Context: user={context.user}, room={context.room}, "
            f"noise_level={context.noise_level}, extra={context.extra}"
        )
    parts.append(f"User: {user_text}")
    prompt = "\\n\\n".join(parts)

    def _do_call() -> str:
        model = genai.GenerativeModel(GEMINI_MODEL)
        resp = model.generate_content(prompt)
        return resp.text or ""

    return await asyncio.to_thread(_do_call)


async def route_one_persona(persona_key: str, user_text: str, ctx: Context, session_id: str = "default") -> tuple[str, List[Action]]:
    if persona_key not in PERSONAS:
        raise HTTPException(status_code=400, detail=f"Unknown persona '{persona_key}'")

    persona = PERSONAS[persona_key]
    llm = persona["llm"]
    base_system_prompt = persona["system_prompt"]

    # ---- Tier B: promoted state (persistent) ----
    promoted = memory_store.get_promoted_state()
    time_block = _build_time_block(promoted)
    memory_block = _build_memory_block(promoted)

    # ---- Tier A-ish: rolling history (non-persistent) ----
    summary, turns = memory_store.get_chat_context(
        session_id=session_id,
        persona=persona_key,
        last_n=CHAT_HISTORY_LAST_N,
        max_chars=CHAT_HISTORY_MAX_CHARS,
    )
    chat_block = _render_chat_context(summary, turns)

    # ---- Tier C: retrieval (opt-in) ----
    retrieval_block = ""
    if promoted.get("retrieval_enabled") and memory_store.retrieval_available():
        hits = memory_store.query_retrieval(user_text or "", limit=3)
        if hits:
            top_k = len(hits)
            lines: list[str] = [
                "BEGIN_RETRIEVED_NOTES",
                "Non-authoritative. May be stale. Verify against live state and code.",
                f"Query: {json.dumps((user_text or '').strip(), ensure_ascii=False)}",
                f"Returned: {len(hits)} docs (top {top_k} shown)",
            ]

            budget = max(500, int(RETRIEVAL_MAX_INJECT_CHARS))
            used = sum(len(x) + 1 for x in lines)
            truncated = False

            def _append(s: str) -> bool:
                nonlocal used, truncated
                if truncated:
                    return False
                if used + len(s) + 1 <= budget:
                    lines.append(s)
                    used += len(s) + 1
                    return True
                remaining = max(0, budget - used)
                marker = "...[TRUNCATED]"
                if remaining <= len(marker):
                    lines.append(marker)
                else:
                    lines.append(s[: remaining - len(marker)] + marker)
                truncated = True
                return False

            for h in hits:
                if truncated:
                    break
                updated = "unknown" if h.updated_at is None else str(int(h.updated_at))
                _append(f"DOC {h.doc_id} (score={h.score}, updated={updated}):")
                _append((h.content or "").strip() or "(empty)")
                _append("---")

            lines.append("END_RETRIEVED_NOTES")
            retrieval_block = "\n".join(lines)

    blocks = [b for b in [time_block, memory_block, retrieval_block, chat_block] if b]
    if blocks:
        system_prompt = base_system_prompt + "\n\n" + "\n\n".join(blocks)
    else:
        system_prompt = base_system_prompt

    # Persist this turn (store AFTER computing context so we don't echo the current user msg twice)
    memory_store.add_chat_message(session_id, persona_key, "user", user_text or "")

    if llm in ("mistral", "lmstudio"):
        base_urls = promoted.get("base_urls") or {}
        mistral_url = (base_urls.get("mistral") or "").strip() or MISTRAL_BASE_URL
        raw_reply = await call_mistral(system_prompt, user_text, ctx, base_url=mistral_url)
    elif llm == "chatgpt":
        raw_reply = await call_chatgpt(system_prompt, user_text, ctx)
    elif llm == "gemini":
        raw_reply = await call_gemini(system_prompt, user_text, ctx)
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported LLM type '{llm}'. Supported: mistral, lmstudio, chatgpt, gemini",
        )

    cleaned, actions = extract_actions(raw_reply)
    cleaned_reply = clean_reply_text(cleaned)

    # Persist assistant reply + trim aggressively (store the cleaned text so we don't
    # poison history with debug/context echoes or action payload remnants).
    memory_store.add_chat_message(session_id, persona_key, "assistant", cleaned_reply or "")
    memory_store.trim_history(session_id, persona_key, keep_last=CHAT_HISTORY_LAST_N)
    return cleaned_reply, actions


async def route_to_persona(req: AskRequest) -> tuple[str, str, List[Action]]:
    persona_key = req.persona.lower()
    user_text = req.text

    ctx = req.context or Context()
    if req.room and not ctx.room:
        ctx.room = req.room

    if persona_key == "auto":
        detected, cleaned = resolve_auto_persona(req.text)
        if detected in PERSONAS:
            persona_key = detected
            user_text = cleaned
        else:
            persona_key = "domino"

    cleaned, actions = await route_one_persona(persona_key, user_text, ctx, session_id=_session_id_from_req(req))
    return persona_key, cleaned, actions


# -------------------------
# Routes
# -------------------------

BASE_DIR = Path(__file__).resolve().parent

# Serve static assets (CSS/JS)
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    html_path = BASE_DIR / "console.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    promoted = memory_store.get_promoted_state()
    base_urls = promoted.get("base_urls") or {}
    mistral_url_promoted = (base_urls.get("mistral") or "").strip() or None
    mistral_url_effective = mistral_url_promoted or MISTRAL_BASE_URL

    whisper_url_promoted = (base_urls.get("whisper") or "").strip() or None
    whisper_url_effective = whisper_url_promoted or (WHISPER_URL or None)

    fish_url_promoted = (base_urls.get("fish") or "").strip() or None
    fish_url_effective = fish_url_promoted or (os.getenv("FISH_TTS_BASE_URL") or None)
    return {
        "status": "ok",
        # Effective URL used for Domino (may be overridden by promoted-state)
        "mistral_base_url": mistral_url_effective,
        "mistral_base_url_env": MISTRAL_BASE_URL,
        "mistral_base_url_promoted": mistral_url_promoted,
        "whisper_base_url": whisper_url_effective,
        "fish_base_url": fish_url_effective,
        "has_openai": bool(openai_client),
        "gemini_enabled": gemini_enabled,
        "ha_enabled": ha_enabled,
        "tts_provider": TTS_PROVIDER,
        "fish_enabled": FISH_TTS_ENABLED,
    }


@app.get("/api/time")
async def api_time(session_id: Optional[str] = None) -> Dict[str, Any]:
    sid = session_id or "default"
    try:
        memory_store.touch_session(sid, max_age_days=SESSION_MAX_AGE_DAYS)
    except Exception:
        pass
    promoted = memory_store.get_promoted_state()
    dt, tzname = _now_for_promoted_timezone(promoted)
    return {
        "ok": True,
        "timezone": tzname,
        "unix": dt.timestamp(),
        "iso": dt.isoformat(),
        "display": dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


@app.post("/api/ask", response_model=AskResponse)
async def api_ask(req: AskRequest, execute: bool = True) -> AskResponse:
    req_persona = req.persona.lower()
    session_id = _session_id_from_req(req)
    try:
        memory_store.touch_session(session_id, max_age_days=SESSION_MAX_AGE_DAYS)
    except Exception:
        pass

    # Automatic promoted-state application (non-stream path has no UI for suggestions)
    try:
        patch, _reasons = _infer_promoted_patch_from_text(req.text or "")
        if patch and _get_auto_promote_flag(req):
            memory_store.patch_promoted_state(patch)
    except Exception:
        pass
    promoted = memory_store.get_promoted_state()
    do_tts = bool(getattr(req, "tts", True))

    # 1) "Collective" mode: Auto can fan-out to all three
    if req_persona == "auto" and (COLLECTIVE_RE.search(req.text or "") or len(_mentioned_personas_in_order(req.text or "")) >= 2):
        ctx = req.context or Context()
        if req.room and not ctx.room:
            ctx.room = req.room

        fanout_text = _strip_collective_addressing(req.text or "")

        if COLLECTIVE_RE.search(req.text or ""):
            targets = ["domino", "penny", "jimmy"]
        else:
            # Two or three names mentioned => respond only as those personas.
            targets = _mentioned_personas_in_order(req.text or "")
            if not targets:
                targets = ["domino"]

        async def one(persona_key: str) -> AskResponse:
            if _is_clock_question(fanout_text or ""):
                cleaned_reply = _clock_skill_reply(promoted)
                actions: List[Action] = []
                memory_store.add_chat_message(session_id, persona_key, "user", fanout_text or "")
                memory_store.add_chat_message(session_id, persona_key, "assistant", cleaned_reply)
                memory_store.trim_history(session_id, persona_key, keep_last=CHAT_HISTORY_LAST_N)
            else:
                raw_reply, actions = await route_one_persona(persona_key, fanout_text, ctx, session_id=session_id)
                cleaned_reply = clean_reply_text(raw_reply)

            if execute:
                await execute_actions(actions)

            pre_tts_vibe = classify_pre_tts_vibe(cleaned_reply)

            if do_tts:
                audio_b64, provider = await generate_tts(
                    persona_key,
                    cleaned_reply,
                    tts_pref=_pick_tts_pref(promoted, persona_key),
                )
            else:
                audio_b64, provider = None, None
            return AskResponse(
                persona=persona_key,
                reply=cleaned_reply,
                actions=actions,
                audio_b64=audio_b64,
                tts_provider=provider,
                pre_tts_vibe=pre_tts_vibe,
            )

        results = await asyncio.gather(*[one(p) for p in targets])
        res = AskResponse(
            persona="collective",
            reply="",
            actions=[],
            audio_b64=None,
            tts_provider=None,
            pre_tts_vibe=None,
            responses=results,
        )

        # Broadcast collective replies to any open chat windows.
        try:
            for r in results:
                await _broadcast_bus.publish(
                    {
                        "kind": "assistant_reply",
                        "source": "ask",
                        "session_id": session_id,
                        "persona": r.persona,
                        "reply": r.reply,
                        "has_audio": bool(r.audio_b64),
                        "tts_provider": r.tts_provider,
                        "ts": time.time(),
                    }
                )
        except Exception:
            pass

        return res

    # 2) Normal single-persona path
    # Fast path: clock questions (no LLM)
    if _is_clock_question(req.text or ""):
        effective_persona = (req_persona if req_persona in PERSONAS else "domino")
        if req_persona == "auto":
            detected, _cleaned = resolve_auto_persona(req.text)
            if detected in PERSONAS:
                effective_persona = detected
        actions = []
        cleaned_reply = _clock_skill_reply(promoted)
        memory_store.add_chat_message(session_id, effective_persona, "user", req.text or "")
        memory_store.add_chat_message(session_id, effective_persona, "assistant", cleaned_reply)
        memory_store.trim_history(session_id, effective_persona, keep_last=CHAT_HISTORY_LAST_N)
    else:
        effective_persona, raw_reply, actions = await route_to_persona(req)

        # 2) Clean the text that will go to the UI / TTS
        cleaned_reply = clean_reply_text(raw_reply)

    # 3) Optionally execute HA actions
    if execute:
        await execute_actions(actions)

    pre_tts_vibe = classify_pre_tts_vibe(cleaned_reply)

    # 4) Generate TTS from the cleaned text (not the messy raw one)
    if do_tts:
        audio_b64, provider = await generate_tts(
            effective_persona,
            cleaned_reply,
            tts_pref=_pick_tts_pref(promoted, effective_persona),
        )
    else:
        audio_b64, provider = None, None

    # 5) Return clean text + actions + audio metadata
    res = AskResponse(
        persona=effective_persona,
        reply=cleaned_reply,
        actions=actions,
        audio_b64=audio_b64,
        tts_provider=provider,
        pre_tts_vibe=pre_tts_vibe,
    )

    # Broadcast /api/ask replies (used by the PC wake-word voice flow)
    try:
        await _broadcast_bus.publish(
            {
                "kind": "assistant_reply",
                "source": "ask",
                "session_id": session_id,
                "persona": effective_persona,
                "reply": cleaned_reply,
                "has_audio": bool(audio_b64),
                "tts_provider": provider,
                "ts": time.time(),
            }
        )
    except Exception:
        pass

    return res


def _sse(event: str, data_obj: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data_obj, ensure_ascii=False)}\n\n"


@app.get("/api/broadcast/stream")
async def api_broadcast_stream():
    q = await _broadcast_bus.subscribe()

    async def _gen():
        try:
            # Initial comment helps some clients establish the stream promptly.
            yield ":ok\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield _sse("broadcast", payload)
                except asyncio.TimeoutError:
                    # Keep-alive
                    yield ":ping\n\n"
        finally:
            await _broadcast_bus.unsubscribe(q)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ask_stream")
async def api_ask_stream(req: AskRequest, execute: bool = True):
    """Server-sent events stream.

    Emits:
      - event: message  data: {persona, reply, actions}
      - event: audio    data: {persona, audio_b64, tts_provider}
      - event: error    data: {persona, error}
      - event: done     data: {persona: 'collective'|'domino'|...}
    """

    req_persona = (req.persona or "domino").lower()
    session_id = _session_id_from_req(req)
    try:
        memory_store.touch_session(session_id, max_age_days=SESSION_MAX_AGE_DAYS)
    except Exception:
        pass

    ctx = req.context or Context()
    if req.room and not ctx.room:
        ctx.room = req.room

    # Determine targets
    if req_persona == "auto" and (COLLECTIVE_RE.search(req.text or "") or len(_mentioned_personas_in_order(req.text or "")) >= 2):
        if COLLECTIVE_RE.search(req.text or ""):
            targets = ["domino", "penny", "jimmy"]
        else:
            targets = _mentioned_personas_in_order(req.text or "")
            if not targets:
                targets = ["domino"]
        user_text = _strip_collective_addressing(req.text or "")
        top_persona = "collective"
    else:
        # single target: reuse existing Auto single-target inference
        if req_persona == "auto":
            detected, cleaned = resolve_auto_persona(req.text)
            persona_key = detected if detected in PERSONAS else "domino"
            user_text = cleaned if detected in PERSONAS else req.text
        else:
            persona_key = req_persona
            user_text = req.text

        if persona_key not in PERSONAS:
            raise HTTPException(status_code=400, detail=f"Unknown persona '{req.persona}'")

        targets = [persona_key]
        top_persona = persona_key

    # Automatic promoted-state suggestion / optional auto-apply
    memory_event: Optional[Dict[str, Any]] = None
    try:
        patch, reasons = _infer_promoted_patch_from_text(user_text or "")
        if patch:
            auto_apply = _get_auto_promote_flag(req)
            applied = False
            if auto_apply:
                memory_store.patch_promoted_state(patch)
                applied = True
            event_ts = time.time()
            memory_event = {
                "event_id": uuid.uuid4().hex,
                "event_ts": event_ts,
                "kind": "promoted_state",
                "mode": "applied" if applied else "suggested",
                "source": "auto_promote",
                "applied_at": event_ts if applied else None,
                "patch": patch,
                "reasons": reasons,
            }
    except Exception as e:
        event_ts = time.time()
        memory_event = {
            "event_id": uuid.uuid4().hex,
            "event_ts": event_ts,
            "kind": "promoted_state",
            "mode": "error",
            "source": "auto_promote",
            "applied_at": None,
            "error": str(e),
        }

    promoted = memory_store.get_promoted_state()

    event_q: asyncio.Queue[str] = asyncio.Queue()
    active = len(targets)

    async def worker(persona_key: str):
        nonlocal active
        try:
            if _is_clock_question(user_text or ""):
                actions: List[Action] = []
                cleaned_reply = _clock_skill_reply(promoted)
                memory_store.add_chat_message(session_id, persona_key, "user", user_text or "")
                memory_store.add_chat_message(session_id, persona_key, "assistant", cleaned_reply)
                memory_store.trim_history(session_id, persona_key, keep_last=CHAT_HISTORY_LAST_N)
            else:
                raw_reply, actions = await route_one_persona(persona_key, user_text, ctx, session_id=session_id)
                cleaned_reply = clean_reply_text(raw_reply)
            await event_q.put(_sse("message", {"persona": persona_key, "reply": cleaned_reply, "actions": [a.model_dump() for a in actions]}))

            if execute:
                # Execute actions, but don't block the user from seeing text already streamed.
                try:
                    await execute_actions(actions)
                except Exception as e:
                    await event_q.put(_sse("error", {"persona": persona_key, "error": f"action_error: {e}"}))

            audio_b64, provider = await generate_tts(
                persona_key,
                cleaned_reply,
                tts_pref=_pick_tts_pref(promoted, persona_key),
            )
            if audio_b64 and provider:
                stored = await _audio_store_put(audio_b64, provider)
                if stored:
                    await event_q.put(
                        _sse(
                            "audio",
                            {
                                "persona": persona_key,
                                "audio_id": stored["audio_id"],
                                "mime": stored["mime"],
                                "tts_provider": provider,
                            },
                        )
                    )
        except Exception as e:
            await event_q.put(_sse("error", {"persona": persona_key, "error": str(e)}))
        finally:
            active -= 1

    for p in targets:
        asyncio.create_task(worker(p))

    async def gen():
        # Initial meta event so client can prep UI
        yield _sse("meta", {"persona": top_persona, "targets": targets})

        if memory_event:
            yield _sse("memory", memory_event)

        while True:
            if active == 0 and event_q.empty():
                break
            try:
                item = await asyncio.wait_for(event_q.get(), timeout=15)
                yield item
            except asyncio.TimeoutError:
                # Keep the connection alive through intermediaries
                yield ": keep-alive\n\n"

        yield _sse("done", {"persona": top_persona})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # If behind reverse proxies (nginx), this helps prevent response buffering.
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("DOMINO_HUB_PORT", "2424"))
    # Dev convenience: auto-reload locally; off by default in Docker.
    # Override with DOMINO_RELOAD=1/true/yes/on if needed.
    reload_env = os.getenv("DOMINO_RELOAD")
    if reload_env is None:
        reload_flag = not _running_in_docker()
    else:
        reload_flag = reload_env.strip().lower() in ("1", "true", "yes", "on")

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload_flag)
