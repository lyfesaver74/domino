from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


@dataclass(frozen=True)
class HubSTTResult:
    text: str


@dataclass(frozen=True)
class HubAskSingle:
    persona: str
    reply: str
    actions: List[Dict[str, Any]]
    audio_b64: Optional[str]
    tts_provider: Optional[str]


@dataclass(frozen=True)
class HubAskResult:
    primary: HubAskSingle
    responses: List[HubAskSingle]


def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _parse_ask_single(data: Dict[str, Any]) -> HubAskSingle:
    return HubAskSingle(
        persona=str(data.get("persona", "")),
        reply=str(data.get("reply", "")),
        actions=[a for a in _as_list(data.get("actions")) if isinstance(a, dict)],
        audio_b64=data.get("audio_b64"),
        tts_provider=data.get("tts_provider"),
    )


class HubClient:
    def __init__(self, *, base_url: str, stt_path: str, ask_path: str, timeout_s: float = 60.0):
        self._base_url = (base_url or "").rstrip("/")
        self._stt_path = stt_path or "/api/stt"
        self._ask_path = ask_path or "/api/ask"
        self._timeout_s = float(timeout_s or 60.0)

    def _url(self, path: str) -> str:
        if not path:
            return self._base_url
        if not path.startswith("/"):
            path = "/" + path
        return self._base_url + path

    async def stt(self, *, wav_bytes: bytes, filename: str = "utterance.wav") -> Optional[HubSTTResult]:
        if not wav_bytes:
            return HubSTTResult(text="")

        timeout = httpx.Timeout(timeout=self._timeout_s, connect=min(10.0, self._timeout_s))
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    self._url(self._stt_path),
                    files={"file": (filename, wav_bytes, "audio/wav")},
                )
                resp.raise_for_status()
                data = resp.json()

            return HubSTTResult(text=str((data or {}).get("text", "")))

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            print(f"[HubClient] STT Connection Failed: {e}")
            return None
        except httpx.HTTPStatusError as e:
            print(f"[HubClient] STT Server Error ({e.response.status_code}): {e}")
            return None
        except Exception as e:
            print(f"[HubClient] STT Unexpected Error: {e}")
            return None

    async def ask(
        self,
        *,
        persona: str,
        text: str,
        room: Optional[str] = None,
        session_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[HubAskResult]:
        payload: Dict[str, Any] = {
            "persona": persona,
            "text": text,
            "room": room,
            "session_id": session_id,
            "context": context,
        }

        timeout = httpx.Timeout(timeout=self._timeout_s, connect=min(10.0, self._timeout_s))

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._url(self._ask_path), json=payload)
                resp.raise_for_status()
                data = resp.json() or {}

            primary = _parse_ask_single(data)
            responses_raw = data.get("responses")
            responses: List[HubAskSingle] = []
            if isinstance(responses_raw, list):
                for item in responses_raw:
                    if isinstance(item, dict):
                        responses.append(_parse_ask_single(item))

            return HubAskResult(primary=primary, responses=responses)

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            # Some httpx exceptions stringify to an empty message; include repr for diagnostics.
            msg = str(e).strip() or repr(e)
            print(f"[HubClient] Ask Connection Failed: {msg}")
            return None
        except httpx.HTTPStatusError as e:
            print(f"[HubClient] Ask Server Error ({e.response.status_code}): {e}")
            return None
        except Exception as e:
            print(f"[HubClient] Ask Unexpected Error: {e}")
            return None


def persona_display_name(persona_key: str) -> str:
    p = (persona_key or "").strip().casefold()
    if p == "domino":
        return "Domino"
    if p == "penny":
        return "Penny"
    if p == "jimmy":
        return "Jimmy"
    if p == "auto":
        return "Auto"
    return persona_key or ""


def wake_persona_to_hub(persona_mode: str) -> str:
    """Map wake config persona_mode (Domino/Penny/Jimmy/Collective) to hub persona keys.

    - Domino/Penny/Jimmy -> lowercase
    - Collective -> auto (hub will infer from text addressing)
    """

    pm = (persona_mode or "").strip().casefold()
    if pm in {"domino", "penny", "jimmy"}:
        return pm
    if pm in {"collective", "friends"}:
        return "auto"
    return pm or "auto"
