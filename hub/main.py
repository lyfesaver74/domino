from fastapi import FastAPI, HTTPException, Request, UploadFile, File
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

import logging

import httpx
from dotenv import load_dotenv
from openai import OpenAI
import google.generativeai as genai


logger = logging.getLogger("domino_hub")

from personas import PERSONAS
from schemas import (
    Context,
    AskRequest,
    Action,
    AskResponse,
    STTResponse,
    PromotedStatePatch,
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

app = FastAPI(
    title="Domino & Friends Hub",
    version="0.2.0",
    description="Routes requests to Domino (Qwen), Penny (ChatGPT), and Jimmy (Gemini).",
)

@app.post("/api/stt", response_model=STTResponse)
async def api_stt(file: UploadFile = File(...)) -> STTResponse:
    if not WHISPER_URL:
        raise HTTPException(status_code=500, detail="WHISPER_URL is not set")

    audio_bytes = await file.read()

    try:
        async with httpx.AsyncClient(timeout=WHISPER_TIMEOUT) as client:
            resp = await client.post(
                f"{WHISPER_URL}/transcribe",
                files={"file": (file.filename or "audio", audio_bytes, file.content_type or "application/octet-stream")},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Whisper STT failed: {e}")

    text = (data.get("text") or "").strip()
    return STTResponse(text=text)


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

WHISPER_URL = os.getenv("WHISPER_URL", "").rstrip("/")
WHISPER_TIMEOUT = float(os.getenv("WHISPER_TIMEOUT", "60"))


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

# Local OpenAI-compatible endpoint (LM Studio etc.). Historically named "qwen" in this codebase.
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "http://127.0.0.1:1234/v1")
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "qwen-local")
# Default updated to LM Studio model identifier for Mistral NeMo Base 2407.
QWEN_MODEL = os.getenv("QWEN_MODEL", "mistral-nemo-base-2407")

qwen_client = OpenAI(
    base_url=QWEN_BASE_URL,
    api_key=QWEN_API_KEY,
)

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

ACTIONS_RE = re.compile(r"<actions>\s*(\[.*?\])\s*</actions>", re.IGNORECASE | re.DOTALL)
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

    # 0) Strip any accidental debug/context echoes from the model output.
    # These should never be user-facing, but some models will occasionally repeat system context.
    stripped: list[str] = []
    kept_lines: list[str] = []

    for line in text.splitlines():
        lower = line.lower()
        cut_idx = -1
        for marker in ("context: user=", "noise_level=", "noiselevel="):
            idx = lower.find(marker)
            if idx != -1:
                cut_idx = idx if cut_idx == -1 else min(cut_idx, idx)

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

    # 2) Remove simple markdown decoration
    text = re.sub(r"(\*\*|\*|__|_|`)", "", text)

    # 3) Strip leading bullet markers and collapse lines
    lines: list[str] = []
    for line in text.splitlines():
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

    json_blob = match.group(1)
    try:
        raw_actions = json.loads(json_blob)
        if not isinstance(raw_actions, list):
            raw_actions = [raw_actions]
        actions = [Action(**a) for a in raw_actions]
    except Exception as e:  # noqa: BLE001
        print(f"[DominoHub] Failed to parse actions: {e}")
        return text, []

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

    fish_ref_map = {
        "domino": FISH_REF_DOMINO,
        "penny": FISH_REF_PENNY,
        "jimmy": FISH_REF_JIMMY,
    }

    tts_pref = (tts_pref or "auto").lower().strip()
    if tts_pref == "off":
        return None, None
    if tts_pref == "browser":
        return None, None

    # ---- 1) Fish first (all personas) ----
    if FISH_TTS_ENABLED and tts_pref in ("auto", "fish"):
        try:
            audio_b64, provider_name = await tts_with_fish(
                text,
                reference_id=fish_ref_map.get(persona_key),
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

async def call_qwen(system_prompt: str, user_text: str, context: Optional[Context]) -> str:
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
        resp = qwen_client.chat.completions.create(
            model=QWEN_MODEL,
            messages=cast(Any, messages),
            temperature=0.6,
        )
        return resp.choices[0].message.content or ""

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

    if llm in ("qwen", "lmstudio"):
        raw_reply = await call_qwen(system_prompt, user_text, ctx)
    elif llm == "chatgpt":
        raw_reply = await call_chatgpt(system_prompt, user_text, ctx)
    elif llm == "gemini":
        raw_reply = await call_gemini(system_prompt, user_text, ctx)
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported LLM type '{llm}'. Supported: qwen, lmstudio, chatgpt, gemini",
        )

    cleaned, actions = extract_actions(raw_reply)

    # Persist assistant reply + trim aggressively
    memory_store.add_chat_message(session_id, persona_key, "assistant", cleaned or "")
    memory_store.trim_history(session_id, persona_key, keep_last=CHAT_HISTORY_LAST_N)
    return cleaned, actions


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
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "qwen_base_url": QWEN_BASE_URL,
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

            audio_b64, provider = await generate_tts(
                persona_key,
                cleaned_reply,
                tts_pref=_pick_tts_pref(promoted, persona_key),
            )
            return AskResponse(
                persona=persona_key,
                reply=cleaned_reply,
                actions=actions,
                audio_b64=audio_b64,
                tts_provider=provider,
            )

        results = await asyncio.gather(*[one(p) for p in targets])
        return AskResponse(
            persona="collective",
            reply="",
            actions=[],
            audio_b64=None,
            tts_provider=None,
            responses=results,
        )

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

    # 4) Generate TTS from the cleaned text (not the messy raw one)
    audio_b64, provider = await generate_tts(
        effective_persona,
        cleaned_reply,
        tts_pref=_pick_tts_pref(promoted, effective_persona),
    )

    # 5) Return clean text + actions + audio metadata
    return AskResponse(
        persona=effective_persona,
        reply=cleaned_reply,
        actions=actions,
        audio_b64=audio_b64,
        tts_provider=provider,
    )


def _sse(event: str, data_obj: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data_obj, ensure_ascii=False)}\n\n"


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
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
