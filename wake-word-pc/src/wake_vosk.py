from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence, Tuple, cast

import numpy as np
import sounddevice as sd
from vosk import KaldiRecognizer, Model


@dataclass(frozen=True)
class WakeHit:
    wake_word: str
    persona_mode: str
    color: str


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _norm(s: str) -> str:
    return " ".join((s or "").strip().casefold().split())


def _iter_wake_configs(wake_words: Any) -> Iterable[Any]:
    if wake_words is None:
        return []
    if isinstance(wake_words, dict):
        out = []
        for k, v in wake_words.items():
            if isinstance(v, dict):
                vv = dict(v)
                vv.setdefault("wake_word", k)
                out.append(vv)
            else:
                out.append(v)
        return out
    if isinstance(wake_words, list):
        return wake_words
    return []


def _build_wake_map(wake_words: Any) -> Dict[str, WakeHit]:
    wake_map: Dict[str, WakeHit] = {}
    for w in _iter_wake_configs(wake_words):
        if isinstance(w, str):
            ww = w
            pm = w
            color = "#FFFFFF"
        else:
            ww = _get(w, "wake_word", None)
            if not ww:
                continue
            pm = _get(w, "persona_mode", ww) or ww
            color = _get(w, "color", "#FFFFFF") or "#FFFFFF"

        wake_map[_norm(str(ww))] = WakeHit(wake_word=str(ww), persona_mode=str(pm), color=str(color))
    return wake_map


class VoskWakeListener:
    """
    Always-on wake word listener (Windows-safe).
    Uses sd.InputStream (float32) + converts to pcm16 bytes for Vosk.
    This avoids RawInputStream start failures on some Windows devices (WDM-KS -9999).
    """

    def __init__(
        self,
        *,
        model_path: Path,
        audio: Any,
        wake_engine: Any,
        wake_words: Any,
    ):
        self._model_path = Path(model_path)
        self._audio = audio
        self._wake_engine = wake_engine
        self._wake_words = wake_words

        self._device = _get(audio, "input_device", None)
        self._sr = int(_get(audio, "sample_rate_hz", 16000))
        self._channels = int(_get(audio, "channels", 1))

        cooldown_ms = _get(wake_engine, "trigger_cooldown_ms", None)
        self._cooldown_s = float(cooldown_ms) / 1000.0 if cooldown_ms is not None else float(_get(wake_engine, "cooldown_s", 1.0))
        self._min_partial_chars = int(_get(wake_engine, "min_partial_chars", 3))
        # Default ON: required to detect wake words inside a longer sentence without waiting for a pause.
        self._emit_on_partial = bool(_get(wake_engine, "emit_on_partial", True))
        # Default off: constraining to a grammar list often forces arbitrary speech to map
        # to one of the phrases, causing constant wake triggers.
        self._use_grammar = bool(_get(wake_engine, "use_grammar", False))

        self._last_text_norm: str = ""
        self._last_emit_ts: float = 0.0
        self._last_partial_hit_ts: float = 0.0

    def _should_emit(self, text: str, wake_map: Dict[str, WakeHit]) -> Optional[WakeHit]:
        tnorm = _norm(text)
        if not tnorm:
            return None

        if len(tnorm) < self._min_partial_chars:
            return None

        if tnorm == self._last_text_norm:
            return None
        self._last_text_norm = tnorm

        now = time.time()
        if now - self._last_emit_ts < self._cooldown_s:
            return None

        tokens = tnorm.split()
        attention = {"hey", "hi", "yo", "okay", "ok"}

        for phrase_norm, hit in wake_map.items():
            ptoks = phrase_norm.split()

            # If we got here from partial decoding, we can see the same rolling text a lot.
            # Add a tiny debounce so we don't emit repeatedly on the same partial window.
            if now - self._last_partial_hit_ts < 0.25:
                continue

            # Strict exact match.
            if tnorm == phrase_norm:
                self._last_emit_ts = now
                self._last_partial_hit_ts = now
                return hit

            # Slightly looser: allow the wake phrase at the end (e.g. "hey domino").
            if len(tokens) >= len(ptoks) and tokens[-len(ptoks) :] == ptoks:
                self._last_emit_ts = now
                self._last_partial_hit_ts = now
                return hit

            # Common pattern: attention word + single-token wake word.
            if len(ptoks) == 1 and len(tokens) >= 2 and tokens[-1] == ptoks[0] and tokens[-2] in attention:
                self._last_emit_ts = now
                self._last_partial_hit_ts = now
                return hit

            # In-sentence match: wake phrase appears anywhere on token boundaries.
            # Example: "hey domino tell me a joke" should trigger immediately.
            if len(tokens) >= len(ptoks) and ptoks:
                for i in range(0, len(tokens) - len(ptoks) + 1):
                    if tokens[i : i + len(ptoks)] == ptoks:
                        self._last_emit_ts = now
                        self._last_partial_hit_ts = now
                        return hit

        return None

    async def listen(self) -> AsyncIterator[WakeHit]:
        if not self._model_path.exists():
            raise FileNotFoundError(f"Vosk model folder not found: {self._model_path}")

        wake_map = _build_wake_map(self._wake_words)
        if not wake_map:
            raise ValueError("wake_words is empty or invalid; nothing to listen for")

        grammar_phrases: List[str] = sorted(wake_map.keys())
        grammar_json = json.dumps(grammar_phrases, ensure_ascii=False)

        loop = asyncio.get_running_loop()
        hit_q: asyncio.Queue[WakeHit] = asyncio.Queue()
        stop_evt = threading.Event()

        def _float_to_pcm16_bytes(x: np.ndarray) -> bytes:
            # x is float32 in [-1, 1]
            x = np.clip(x, -1.0, 1.0)
            pcm = (x * 32767.0).astype(np.int16)
            return pcm.tobytes()

        def worker() -> None:
            def _hostapi_name(device_index: int) -> str:
                try:
                    dev = dict(sd.query_devices(device_index))
                    hostapis = cast(Any, sd.query_hostapis())
                    hostapi_idx = int(dev.get("hostapi", -1))
                    if hostapi_idx < 0:
                        return ""
                    hostapi = hostapis[hostapi_idx]
                    if isinstance(hostapi, dict):
                        return str(hostapi.get("name", ""))
                    getter = getattr(hostapi, "get", None)
                    if callable(getter):
                        return str(getter("name", ""))
                    return ""
                except Exception:
                    return ""

            def _device_name(device_index: int) -> str:
                try:
                    dev = dict(sd.query_devices(device_index))
                    return str(dev.get("name", ""))
                except Exception:
                    return ""

            def _device_candidates(configured: Any) -> List[Optional[int]]:
                # Always end with None = system default.
                try:
                    devices = sd.query_devices()
                except Exception:
                    return [None]

                pref_hostapis = ("WASAPI", "MME", "DirectSound")
                bad_hostapis = ("WDM-KS",)

                def score(idx: int) -> Tuple[int, int]:
                    h = _hostapi_name(idx).upper()
                    # Prefer WASAPI/MME/DS, de-prioritize WDM-KS.
                    for i, key in enumerate(pref_hostapis):
                        if key in h:
                            return (0, i)
                    for key in bad_hostapis:
                        if key in h:
                            return (2, 0)
                    return (1, 0)

                def is_input(idx: int) -> bool:
                    try:
                        dev = dict(devices[idx])
                        return int(dev.get("max_input_channels", 0)) > 0
                    except Exception:
                        return False

                # configured can be int index or a string substring.
                if isinstance(configured, int):
                    if 0 <= configured < len(devices) and is_input(configured):
                        name = _device_name(configured)
                        same_name = [
                            i
                            for i in range(len(devices))
                            if is_input(i) and _device_name(i).casefold() == name.casefold()
                        ]
                        same_name_sorted = sorted(set(same_name), key=score)
                        out: List[Optional[int]] = []
                        if configured in same_name_sorted:
                            out.append(configured)
                            same_name_sorted.remove(configured)
                        out.extend(same_name_sorted)
                        out.append(None)
                        return out
                    return [None]

                if isinstance(configured, str):
                    needle = configured.strip().casefold()
                    if not needle:
                        return [None]
                    matches = [
                        i
                        for i in range(len(devices))
                        if is_input(i) and needle in _device_name(i).casefold()
                    ]
                    if not matches:
                        return [None]
                    matches_sorted = sorted(set(matches), key=score)
                    return [matches_sorted[0], None]

                return [None]

            def run_stream(chosen_sr: int, chosen_channels: int) -> None:
                model = Model(str(self._model_path))
                # NOTE: Using a restricted grammar list for single-word wake phrases can cause
                # Vosk to "snap" arbitrary speech to the closest grammar option, producing
                # constant false triggers. We keep grammar optional.
                if self._use_grammar:
                    rec = KaldiRecognizer(model, float(chosen_sr), grammar_json)
                else:
                    rec = KaldiRecognizer(model, float(chosen_sr))
                rec.SetWords(False)

                def callback(indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
                    if stop_evt.is_set():
                        return

                    # If we opened 2 channels, average to mono (more robust than taking just left)
                    x = indata.mean(axis=1) if indata.ndim == 2 else indata
                    pcm16 = _float_to_pcm16_bytes(x)

                    if rec.AcceptWaveform(pcm16):
                        try:
                            obj = json.loads(rec.Result() or "{}")
                            hit = self._should_emit(obj.get("text", ""), wake_map)
                            if hit is not None:
                                loop.call_soon_threadsafe(hit_q.put_nowait, hit)
                        except Exception:
                            pass
                    else:
                        if self._emit_on_partial:
                            try:
                                obj = json.loads(rec.PartialResult() or "{}")
                                hit = self._should_emit(obj.get("partial", ""), wake_map)
                                if hit is not None:
                                    loop.call_soon_threadsafe(hit_q.put_nowait, hit)
                            except Exception:
                                pass

                device_to_use = current_device
                extra_settings = None
                if isinstance(device_to_use, int):
                    host = _hostapi_name(device_to_use).upper()
                    if "WASAPI" in host and hasattr(sd, "WasapiSettings"):
                        try:
                            extra_settings = sd.WasapiSettings(exclusive=False)
                        except Exception:
                            extra_settings = None

                with sd.InputStream(
                    samplerate=chosen_sr,
                    blocksize=0,
                    dtype="float32",
                    channels=chosen_channels,
                    device=device_to_use,
                    callback=callback,
                    latency="low",
                    extra_settings=extra_settings,
                ):

                    while not stop_evt.is_set():
                        time.sleep(0.05)

            # Try configured device first, then prefer WASAPI/MME variants of same device name,
            # then fall back to system default. This avoids common WDM-KS (-9999) failures.
            last_err: Optional[Exception] = None
            for current_device in _device_candidates(self._device):
                try:
                    # If the configured sample rate fails, fall back to device default samplerate.
                    sr = self._sr
                    try:
                        dev_info = dict(sd.query_devices(current_device, kind="input"))
                        default_sr = int(float(dev_info.get("default_samplerate", sr)))
                    except Exception:
                        default_sr = sr

                    # Probe supported formats (sample rate + channels) before opening the stream.
                    rate_candidates: List[int] = []
                    for r in (sr, default_sr, 48000, 44100, 32000, 22050, 16000):
                        if isinstance(r, (int, float)):
                            rr = int(r)
                            if rr > 0 and rr not in rate_candidates:
                                rate_candidates.append(rr)
                    chan_candidates = [c for c in (self._channels, 2, 1) if isinstance(c, int) and c > 0]

                    chosen: Optional[Tuple[int, int]] = None
                    for ch in chan_candidates:
                        for r in rate_candidates:
                            try:
                                sd.check_input_settings(device=current_device, samplerate=r, channels=ch, dtype="float32")
                                chosen = (r, ch)
                                break
                            except Exception:
                                continue
                        if chosen:
                            break

                    if not chosen:
                        raise RuntimeError(
                            f"No supported input format for device={current_device} "
                            f"(tried rates={rate_candidates}, channels={chan_candidates})"
                        )

                    chosen_sr, chosen_ch = chosen
                    if isinstance(current_device, int):
                        print(
                            f"[wake_vosk] trying device={current_device} "
                            f"name='{_device_name(current_device)}' hostapi='{_hostapi_name(current_device)}' "
                            f"sr={chosen_sr} ch={chosen_ch}"
                        )
                    else:
                        print(f"[wake_vosk] trying device=DEFAULT sr={chosen_sr} ch={chosen_ch}")

                    run_stream(chosen_sr, chosen_ch)
                    return
                except sd.PortAudioError as e:
                    last_err = e
                    # Keep going to next device candidate.
                    continue
                except Exception as e:
                    last_err = e
                    continue

            raise RuntimeError(
                "Failed to start microphone capture on all candidate devices. "
                f"Last error: {last_err!r}. "
                "Try setting settings.json audio.input_device=null or run python src/list_audio_devices.py to pick a different device."
            )


        t = threading.Thread(target=worker, daemon=True)
        t.start()

        try:
            while True:
                yield await hit_q.get()
        finally:
            stop_evt.set()
            t.join(timeout=2.0)
