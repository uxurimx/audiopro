"""
Resampleo de audio.

PipeWire captura a 48kHz siempre (JACK backend lo fuerza).
Whisper y el VAD necesitan 16kHz.
OpenAI TTS devuelve 24kHz PCM.

Usa scipy.signal.resample_poly para resampleo de alta calidad
con ratios enteros exactos — sin artefactos de aliasing.

Ratios comunes:
  48000 → 16000 : down=3, up=1  (factor 3x)
  16000 → 48000 : down=1, up=3
  24000 → 48000 : down=1, up=2
  48000 → 24000 : down=2, up=1
"""
from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import resample_poly


def resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """
    Resamplea audio entre dos frecuencias de muestreo.

    Args:
        audio:     array float32 de forma (N,) o (N, channels)
        from_rate: frecuencia de origen en Hz
        to_rate:   frecuencia de destino en Hz

    Returns:
        Array float32 resampleado. Mantiene la forma (canales preservados).
    """
    if from_rate == to_rate:
        return audio

    g    = gcd(from_rate, to_rate)
    up   = to_rate  // g
    down = from_rate // g

    result = resample_poly(audio, up, down, axis=0)
    return result.astype(np.float32)


def to_16k(audio: np.ndarray, from_rate: int = 48000) -> np.ndarray:
    """48kHz → 16kHz para Whisper y webrtcvad."""
    return resample(audio, from_rate, 16_000)


def to_48k(audio: np.ndarray, from_rate: int = 24000) -> np.ndarray:
    """24kHz (OpenAI TTS PCM) → 48kHz para reproducción en PipeWire."""
    return resample(audio, from_rate, 48_000)


def to_mono(audio: np.ndarray) -> np.ndarray:
    """Convierte audio multicanal a mono (promedio de canales)."""
    if audio.ndim == 1:
        return audio
    return audio.mean(axis=1).astype(np.float32)


def pcm_to_float(pcm_int16: bytes) -> np.ndarray:
    """Convierte bytes PCM int16 a float32 normalizado [-1, 1]."""
    arr = np.frombuffer(pcm_int16, dtype=np.int16)
    return (arr / 32768.0).astype(np.float32)


def float_to_pcm(audio: np.ndarray) -> bytes:
    """Convierte float32 [-1, 1] a bytes PCM int16."""
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()
