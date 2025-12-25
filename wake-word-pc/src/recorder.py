from __future__ import annotations

import io
import time
import wave
from typing import Any, Optional

import numpy as np
import sounddevice as sd


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x), dtype=np.float64)))


def record_command(*, audio_cfg: dict, rec_cfg: dict) -> Optional[bytes]:
    """Capture a single voice command after a wake-word.

    Requirements satisfied:
    - Uses sounddevice.InputStream in blocking mode.
    - Reads ~30ms chunks.
    - RMS-based VAD with three stop conditions.
    - Returns in-memory WAV bytes (PCM16) or None.

    Notes:
    - This function is dict-first, but tolerates objects for convenience.
    """

    try:
        device = _get(audio_cfg, "input_device", None)
        sample_rate_hz = int(_get(audio_cfg, "sample_rate_hz", 16000))
        channels = int(_get(audio_cfg, "channels", 1))

        max_seconds = float(_get(rec_cfg, "max_seconds", 12))
        vad_cfg = _get(rec_cfg, "vad", {})
        energy_threshold = float(_get(vad_cfg, "energy_threshold", 0.01))
        silence_ms_to_stop = float(_get(vad_cfg, "silence_ms_to_stop", 900))
        no_speech_ms_to_stop = float(_get(vad_cfg, "no_speech_ms_to_stop", 1500))

        # Fixed, small chunk size for responsive VAD.
        chunk_ms = 30.0
        frames_per_chunk = max(1, int(round(sample_rate_hz * (chunk_ms / 1000.0))))

        # Basic start confirmation to avoid a single noisy spike.
        speech_confirm_ms = 60.0
        speech_candidate_ms = 0.0

        speech_started = False
        last_voiced_ts: Optional[float] = None
        speech_chunks: list[np.ndarray] = []

        start_ts = time.monotonic()

        with sd.InputStream(
            samplerate=sample_rate_hz,
            channels=channels,
            dtype="float32",
            device=device,
            blocksize=frames_per_chunk,
        ) as stream:
            while True:
                now = time.monotonic()
                elapsed_s = now - start_ts
                if elapsed_s >= max_seconds:
                    break

                data, overflowed = stream.read(frames_per_chunk)
                if overflowed:
                    # Not fatal; still best-effort capture.
                    pass

                if data is None or getattr(data, "size", 0) == 0:
                    continue

                # data is (frames, channels)
                if channels > 1:
                    mono = data.mean(axis=1)
                else:
                    mono = data[:, 0]

                level = _rms(mono)

                if not speech_started:
                    if level >= energy_threshold:
                        speech_candidate_ms += chunk_ms
                        if speech_candidate_ms >= speech_confirm_ms:
                            speech_started = True
                            last_voiced_ts = now
                            speech_chunks.append(np.array(mono, dtype=np.float32, copy=True))
                    else:
                        speech_candidate_ms = 0.0

                    if no_speech_ms_to_stop > 0 and (elapsed_s * 1000.0) >= no_speech_ms_to_stop:
                        return None
                else:
                    speech_chunks.append(np.array(mono, dtype=np.float32, copy=True))

                    # Track voiced time with a small hysteresis.
                    if level >= (energy_threshold * 0.7):
                        last_voiced_ts = now

                    if last_voiced_ts is not None:
                        silence_ms = (now - last_voiced_ts) * 1000.0
                        if silence_ms >= silence_ms_to_stop:
                            break

        if not speech_chunks:
            return None

        audio = np.concatenate(speech_chunks)
        audio = np.clip(audio, -1.0, 1.0)
        pcm16 = (audio * 32767.0).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate_hz)
            wf.writeframes(pcm16.tobytes())

        return buf.getvalue()

    except sd.PortAudioError as exc:
        print(f"[recorder] sounddevice error: {exc}")
        return None
    except Exception as exc:
        print(f"[recorder] error: {exc}")
        return None
