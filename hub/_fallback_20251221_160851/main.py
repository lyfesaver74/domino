from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Any, Dict, List, Optional
from pathlib import Path

import os
import re
import json
import base64
import asyncio

import logging

import httpx
from dotenv import load_dotenv
from openai import OpenAI
import google.generativeai as genai


logger = logging.getLogger("domino_hub")

from personas import PERSONAS
from schemas import Context, AskRequest, Action, AskResponse
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

    async def tts_with_fish(*args, **kwargs):
        return None
  # <-- Fish stub


# -------------------------
# Load environment
# -------------------------
load_dotenv()

app = FastAPI(
    title="Domino & Friends Hub",
    version="0.2.0",
    description="Routes requests to Domino (Qwen), Penny (ChatGPT), and Jimmy (Gemini).",
)


# -------------------------
# LLM client setup
# -------------------------

# Qwen (Domino) via OpenAI-compatible endpoint (LM Studio etc.)
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "http://127.0.0.1:1234/v1")
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "qwen-local")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen/qwen3-14b")

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
AUTO_PERSONA_RE = re.compile(r"^\s*(domino|penny|jimmy)\s*[:,]\s+", re.IGNORECASE)
COLLECTIVE_RE = re.compile(r"\bthe\s+collective\b", re.IGNORECASE)


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
    collective_re = re.compile(r"^\s*the\s+collective\b", re.IGNORECASE)
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

            url = f"{HA_BASE_URL.rstrip('/')}/api/services/{domain}/{service_name}"
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001
                print(f"[DominoHub] Error calling HA service {service} on {entity_id}: {e}")


async def generate_tts(persona: str, text: str) -> tuple[Optional[str], Optional[str]]:
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

    # ---- 1) Fish first (all personas) ----
    if FISH_TTS_ENABLED:
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
    if True:
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

    # Future: more engines can go here.
    return None, None


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

    resp = qwen_client.chat.completions.create(
        model=QWEN_MODEL,
        messages=messages,
        temperature=0.6,
    )
    return resp.choices[0].message.content


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

    resp = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.5,
    )
    return resp.choices[0].message.content


async def call_gemini(system_prompt: str, user_text: str, context: Optional[Context]) -> str:
    if not gemini_enabled:
        raise HTTPException(
            status_code=500,
            detail="Jimmy (Gemini) is not configured. Set GEMINI_API_KEY in your environment.",
        )

    model = genai.GenerativeModel(GEMINI_MODEL)
    parts = [system_prompt]
    if context:
        parts.append(
            f"Context: user={context.user}, room={context.room}, "
            f"noise_level={context.noise_level}, extra={context.extra}"
        )
    parts.append(f"User: {user_text}")

    resp = model.generate_content("\\n\\n".join(parts))
    return resp.text or ""


async def route_one_persona(persona_key: str, user_text: str, ctx: Context) -> tuple[str, List[Action]]:
    if persona_key not in PERSONAS:
        raise HTTPException(status_code=400, detail=f"Unknown persona '{persona_key}'")

    persona = PERSONAS[persona_key]
    llm = persona["llm"]
    system_prompt = persona["system_prompt"]

    if llm == "qwen":
        raw_reply = await call_qwen(system_prompt, user_text, ctx)
    elif llm == "chatgpt":
        raw_reply = await call_chatgpt(system_prompt, user_text, ctx)
    elif llm == "gemini":
        raw_reply = await call_gemini(system_prompt, user_text, ctx)
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported LLM type '{llm}'")

    cleaned, actions = extract_actions(raw_reply)
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

    cleaned, actions = await route_one_persona(persona_key, user_text, ctx)
    return persona_key, cleaned, actions


# -------------------------
# Routes
# -------------------------

BASE_DIR = Path(__file__).resolve().parent


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


@app.post("/api/ask", response_model=AskResponse)
async def api_ask(req: AskRequest, execute: bool = True) -> AskResponse:
    req_persona = req.persona.lower()

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
            raw_reply, actions = await route_one_persona(persona_key, fanout_text, ctx)
            cleaned_reply = clean_reply_text(raw_reply)

            if execute:
                await execute_actions(actions)

            audio_b64, provider = await generate_tts(persona_key, cleaned_reply)
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
    effective_persona, raw_reply, actions = await route_to_persona(req)

    # 2) Clean the text that will go to the UI / TTS
    cleaned_reply = clean_reply_text(raw_reply)

    # 3) Optionally execute HA actions
    if execute:
        await execute_actions(actions)

    # 4) Generate TTS from the cleaned text (not the messy raw one)
    audio_b64, provider = await generate_tts(effective_persona, cleaned_reply)

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

    event_q: asyncio.Queue[str] = asyncio.Queue()
    active = len(targets)
    messages_sent: set[str] = set()
    pending_audio: dict[str, tuple[str, Optional[str]]] = {}
    flush_lock = asyncio.Lock()

    async def flush_audio_if_ready():
        # Avoid streaming huge base64 audio before all text has been delivered.
        if len(messages_sent) != len(targets):
            return
        async with flush_lock:
            if len(messages_sent) != len(targets):
                return
            if not pending_audio:
                return
            items = list(pending_audio.items())
            pending_audio.clear()
            for persona_key, (audio_b64, provider) in items:
                if audio_b64 and provider:
                    await event_q.put(
                        _sse(
                            "audio",
                            {"persona": persona_key, "audio_b64": audio_b64, "tts_provider": provider},
                        )
                    )

    async def worker(persona_key: str):
        nonlocal active
        try:
            raw_reply, actions = await route_one_persona(persona_key, user_text, ctx)
            cleaned_reply = clean_reply_text(raw_reply)
            await event_q.put(_sse("message", {"persona": persona_key, "reply": cleaned_reply, "actions": [a.model_dump() for a in actions]}))
            messages_sent.add(persona_key)
            await flush_audio_if_ready()

            if execute:
                # Execute actions, but don't block the user from seeing text already streamed.
                try:
                    await execute_actions(actions)
                except Exception as e:
                    await event_q.put(_sse("error", {"persona": persona_key, "error": f"action_error: {e}"}))

            audio_b64, provider = await generate_tts(persona_key, cleaned_reply)
            if audio_b64 and provider:
                # Defer emitting audio until all text responses have been streamed.
                pending_audio[persona_key] = (audio_b64, provider)
                await flush_audio_if_ready()
        except Exception as e:
            await event_q.put(_sse("error", {"persona": persona_key, "error": str(e)}))
        finally:
            active -= 1

    for p in targets:
        asyncio.create_task(worker(p))

    async def gen():
        # Initial meta event so client can prep UI
        yield _sse("meta", {"persona": top_persona, "targets": targets})

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
