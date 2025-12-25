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


class Action(BaseModel):
    type: str
    data: Dict[str, Any]


class AskResponse(BaseModel):
    persona: str
    reply: str
    actions: List[Action] = Field(default_factory=list)
    audio_b64: Optional[str] = None
    tts_provider: Optional[str] = None
    responses: Optional[List[AskResponse]] = None


class STTResponse(BaseModel):
    text: str


class PromotedStatePatch(BaseModel):
    timezone: Optional[str] = None
    location: Optional[str] = None
    preferred_units: Optional[str] = None
    working_rules: Optional[str] = None
    tech_stack: Optional[str] = None
    tts_overrides: Optional[Dict[str, str]] = None
    base_urls: Optional[Dict[str, Optional[str]]] = None
    retrieval_enabled: Optional[bool] = None


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
