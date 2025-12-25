import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class WakeWordConfig:
    label: str
    wake_word: str
    persona_mode: str
    color: str


@dataclass(frozen=True)
class OverlayWSConfig:
    host: str
    port: int
    path: str


@dataclass(frozen=True)
class AudioConfig:
    sample_rate_hz: int
    channels: int
    sample_format: str
    input_device: Optional[object]
    output_device: Optional[object]


@dataclass(frozen=True)
class WakeEngineConfig:
    type: str
    vosk_model_path: str
    trigger_cooldown_ms: int
    min_partial_chars: int
    emit_on_partial: bool
    use_grammar: bool


@dataclass(frozen=True)
class RecordingVADConfig:
    silence_ms_to_stop: int
    energy_threshold: float
    no_speech_ms_to_stop: int


@dataclass(frozen=True)
class RecordingConfig:
    max_seconds: int
    vad: RecordingVADConfig


@dataclass(frozen=True)
class HubConfig:
    base_url: str
    stt_path: str
    ask_path: str
    timeout_s: float


@dataclass(frozen=True)
class ClientConfig:
    device: str
    session_id: Optional[str]


@dataclass(frozen=True)
class Settings:
    overlay_ws: OverlayWSConfig
    wake_words: List[WakeWordConfig]
    audio: AudioConfig
    wake_engine: WakeEngineConfig
    recording: RecordingConfig
    hub: HubConfig
    client: ClientConfig


def load_settings(settings_path: Path) -> Settings:
    raw = json.loads(settings_path.read_text(encoding="utf-8"))

    hub_raw = raw.get("hub") or {}
    hub = HubConfig(
        base_url=str(hub_raw.get("base_url", "http://127.0.0.1:2424")).rstrip("/"),
        stt_path=str(hub_raw.get("stt_path", "/api/stt")),
        ask_path=str(hub_raw.get("ask_path", "/api/ask")),
        timeout_s=float(hub_raw.get("timeout_s", 60.0)),
    )

    client_raw = raw.get("client") or {}
    client = ClientConfig(
        device=str(client_raw.get("device", "surface-pro")),
        session_id=client_raw.get("session_id", None),
    )

    overlay_ws_raw = raw.get("overlay_ws") or {}
    overlay_ws = OverlayWSConfig(
        host=str(overlay_ws_raw.get("host", "127.0.0.1")),
        port=int(overlay_ws_raw.get("port", 8765)),
        path=str(overlay_ws_raw.get("path", "/ws")),
    )

    audio_raw = raw.get("audio") or {}
    audio = AudioConfig(
        sample_rate_hz=int(audio_raw.get("sample_rate_hz", 16000)),
        channels=int(audio_raw.get("channels", 1)),
        sample_format=str(audio_raw.get("sample_format", "pcm16")),
        input_device=audio_raw.get("input_device", None),
        output_device=audio_raw.get("output_device", None),
    )

    wake_engine_raw = raw.get("wake_engine") or {}
    wake_engine = WakeEngineConfig(
        type=str(wake_engine_raw.get("type", "vosk_keyword")),
        vosk_model_path=str(wake_engine_raw.get("vosk_model_path", "models/vosk-model-small-en-us-0.15")),
        trigger_cooldown_ms=int(wake_engine_raw.get("trigger_cooldown_ms", 2500)),
        min_partial_chars=int(wake_engine_raw.get("min_partial_chars", 3)),
        emit_on_partial=bool(wake_engine_raw.get("emit_on_partial", True)),
        use_grammar=bool(wake_engine_raw.get("use_grammar", False)),
    )

    wake_words: List[WakeWordConfig] = []
    for item in (raw.get("wake_words") or []):
        wake_words.append(
            WakeWordConfig(
                label=str(item.get("label")),
                wake_word=str(item.get("wake_word")),
                persona_mode=str(item.get("persona_mode")),
                color=str(item.get("color")),
            )
        )

    recording_raw = raw.get("recording") or {}
    vad_raw = recording_raw.get("vad") or {}
    recording = RecordingConfig(
        max_seconds=int(recording_raw.get("max_seconds", 12)),
        vad=RecordingVADConfig(
            silence_ms_to_stop=int(vad_raw.get("silence_ms_to_stop", 900)),
            energy_threshold=float(vad_raw.get("energy_threshold", 0.01)),
            no_speech_ms_to_stop=int(vad_raw.get("no_speech_ms_to_stop", 1500)),
        ),
    )

    return Settings(
        overlay_ws=overlay_ws,
        wake_words=wake_words,
        audio=audio,
        wake_engine=wake_engine,
        recording=recording,
        hub=hub,
        client=client,
    )
