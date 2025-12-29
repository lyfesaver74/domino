from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional


OverlayState = Literal[
    "listening",
    "recording",
    "transcribing",
    "thinking",
    "tts_building",
    "speaking",
    "error",
]


@dataclass(frozen=True)
class StatusEvent:
    type: Literal["status"]
    state: OverlayState
    hint: str
    color: str


@dataclass(frozen=True)
class WakeEvent:
    type: Literal["wake"]
    wake_word: str
    persona_mode: str
    color: str


@dataclass(frozen=True)
class UserUtteranceEvent:
    type: Literal["user_utterance"]
    text: str


@dataclass(frozen=True)
class AssistantReplyEvent:
    type: Literal["assistant_reply"]
    persona: str
    color: str
    text: str


@dataclass(frozen=True)
class TTSAudioEvent:
    type: Literal["tts_audio"]
    persona: str
    color: str
    format: str
    audio_b64: str


@dataclass(frozen=True)
class ActionsEvent:
    type: Literal["actions"]
    items: List[Dict[str, Any]]


@dataclass(frozen=True)
class ErrorEvent:
    type: Literal["error"]
    stage: str
    message: str


def to_payload(event: Any) -> Dict[str, Any]:
    return asdict(event)


def status(*, state: OverlayState, hint: str, color: str) -> Dict[str, Any]:
    return to_payload(StatusEvent(type="status", state=state, hint=hint, color=color))


def wake(*, wake_word: str, persona_mode: str, color: str) -> Dict[str, Any]:
    return to_payload(WakeEvent(type="wake", wake_word=wake_word, persona_mode=persona_mode, color=color))


def user_utterance(*, text: str) -> Dict[str, Any]:
    return to_payload(UserUtteranceEvent(type="user_utterance", text=text))


def assistant_reply(*, persona: str, text: str, color: str) -> Dict[str, Any]:
    return to_payload(AssistantReplyEvent(type="assistant_reply", persona=persona, text=text, color=color))


def tts_audio(*, persona: str, color: str, format: str, audio_b64: str) -> Dict[str, Any]:
    return to_payload(TTSAudioEvent(type="tts_audio", persona=persona, color=color, format=format, audio_b64=audio_b64))


def actions(*, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return to_payload(ActionsEvent(type="actions", items=items))


def error(*, stage: str, message: str) -> Dict[str, Any]:
    return to_payload(ErrorEvent(type="error", stage=stage, message=message))
