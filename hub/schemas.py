from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Context(BaseModel):
    user: Optional[str] = None
    room: Optional[str] = None
    noise_level: Optional[float] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class AskRequest(BaseModel):
    persona: str
    text: str
    room: Optional[str] = None
    context: Optional[Context] = None
    session_id: Optional[str] = None
    # If false, /api/ask returns text/actions only (no audio generation).
    tts: bool = True


class Action(BaseModel):
    type: str
    data: Dict[str, Any]


class AskResponse(BaseModel):
    persona: str
    reply: str
    actions: List[Action] = Field(default_factory=list)
    audio_b64: Optional[str] = None
    tts_provider: Optional[str] = None
    # Optional hint for selecting a short "pre-TTS" cue during synthesis.
    # Expected values: unsure|humor|surprise|irked|normal
    pre_tts_vibe: Optional[str] = None
    responses: Optional[List[AskResponse]] = None


class STTResponse(BaseModel):
    text: str


class FishTtsSettings(BaseModel):
    timeout_sec: Optional[float] = None
    format: Optional[str] = None
    normalize: Optional[bool] = None
    chunk_length: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    repetition_penalty: Optional[float] = None
    max_new_tokens: Optional[int] = None
    refs: Optional[Dict[str, Optional[str]]] = None


class WhisperSttSettings(BaseModel):
    timeout_sec: Optional[float] = None


class TTSTestRequest(BaseModel):
    persona: str = "domino"
    text: str
    provider: str = "fish"
    reference_id: Optional[str] = None


class TTSRequest(BaseModel):
    persona: str = "domino"
    text: str
    # Optional override (auto|fish|elevenlabs|browser|off). If omitted, hub uses promoted-state preference.
    tts_pref: Optional[str] = None
    session_id: Optional[str] = None


class TTSResponse(BaseModel):
    audio_b64: Optional[str] = None
    tts_provider: Optional[str] = None


class PromotedStatePatch(BaseModel):
    timezone: Optional[str] = None
    location: Optional[str] = None
    preferred_units: Optional[str] = None
    working_rules: Optional[str] = None
    tech_stack: Optional[str] = None
    tts_overrides: Optional[Dict[str, str]] = None
    base_urls: Optional[Dict[str, Optional[str]]] = None
    retrieval_enabled: Optional[bool] = None
    fish_tts: Optional[FishTtsSettings] = None
    whisper_stt: Optional[WhisperSttSettings] = None


class RetrievalUpsertRequest(BaseModel):
    doc_id: str
    title: Optional[str] = None
    content: str
    tags: Optional[str] = None
    session_id: Optional[str] = None


class RetrievalQueryRequest(BaseModel):
    query: str
    limit: int = 3
    session_id: Optional[str] = None
