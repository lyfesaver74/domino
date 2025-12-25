from __future__ import annotations

import asyncio
import io
import wave
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd


def sniff_audio_format(data: bytes) -> str:
    """Best-effort audio container sniff.

    Returns: 'wav' | 'mp3' | 'ogg' | 'flac' | 'unknown'
    """

    if not data or len(data) < 4:
        return "unknown"

    head4 = data[:4]
    if head4 == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":
        return "wav"
    if head4 == b"OggS":
        return "ogg"
    if head4 == b"fLaC":
        return "flac"
    # MP3: either ID3 tag or MPEG frame sync.
    if head4[:3] == b"ID3":
        return "mp3"
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"

    return "unknown"


def _read_wav_bytes(wav_bytes: bytes) -> Tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = int(wf.getnchannels())
        sample_rate = int(wf.getframerate())
        sample_width = int(wf.getsampwidth())
        frames = int(wf.getnframes())
        pcm = wf.readframes(frames)

    if channels <= 0 or sample_rate <= 0:
        raise ValueError("Invalid WAV header")

    # Support PCM16 and PCM32.
    if sample_width == 2:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(pcm, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        audio = audio.reshape(-1, channels)

    return audio, sample_rate


def play_wav_bytes_blocking(
    wav_bytes: bytes,
    *,
    output_device: Optional[int] = None,
) -> None:
    audio, sample_rate = _read_wav_bytes(wav_bytes)
    sd.play(audio, samplerate=sample_rate, device=output_device)
    sd.wait()


async def play_wav_bytes(
    wav_bytes: bytes,
    *,
    output_device: Optional[int] = None,
) -> None:
    # Run in a thread so we don't block the asyncio loop.
    await asyncio.to_thread(play_wav_bytes_blocking, wav_bytes, output_device=output_device)


def _decode_with_miniaudio(audio_bytes: bytes) -> Tuple[np.ndarray, int]:
    try:
        import miniaudio  # type: ignore
    except Exception as exc:
        raise RuntimeError("miniaudio is not installed; cannot decode compressed audio") from exc

    decoded = miniaudio.decode(audio_bytes)
    sample_rate = int(decoded.sample_rate)
    channels = int(decoded.nchannels)
    sample_width = int(decoded.sample_width)

    if sample_width == 2:
        audio = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(decoded.samples, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported decoded sample width: {sample_width}")

    if channels > 1:
        audio = audio.reshape(-1, channels)

    return audio, sample_rate


def play_audio_bytes_blocking(
    audio_bytes: bytes,
    *,
    output_device: Optional[int] = None,
) -> None:
    fmt = sniff_audio_format(audio_bytes)
    if fmt == "wav":
        return play_wav_bytes_blocking(audio_bytes, output_device=output_device)

    if fmt in {"mp3", "ogg", "flac"}:
        audio, sample_rate = _decode_with_miniaudio(audio_bytes)
        sd.play(audio, samplerate=sample_rate, device=output_device)
        sd.wait()
        return

    raise ValueError(f"Unsupported audio format: {fmt}")


async def play_audio_bytes(
    audio_bytes: bytes,
    *,
    output_device: Optional[int] = None,
) -> None:
    await asyncio.to_thread(play_audio_bytes_blocking, audio_bytes, output_device=output_device)
