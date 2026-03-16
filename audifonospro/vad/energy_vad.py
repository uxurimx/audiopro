"""
VAD por energía RMS — detecta segmentos de voz en un stream de audio.

Algoritmo:
  1. Calcula RMS de cada chunk → compara con umbral en dB
  2. Acumula chunks de voz hasta detectar N ms de silencio consecutivo
  3. Emite el segmento completo como bytes WAV (int16 mono)

No requiere extensiones C ni modelos ML.
"""
from __future__ import annotations

import io
import wave

import numpy as np


class EnergyVAD:
    """
    Detector de actividad de voz basado en energía RMS.

    Uso:
        vad = EnergyVAD()
        for chunk in audio_stream:           # chunk: np.ndarray int16
            segment = vad.feed(chunk)
            if segment:                       # segment: bytes WAV completo
                transcribe(segment)
        final = vad.flush()                   # emitir lo que quede al parar
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        silence_threshold_db: float = -40.0,
        silence_duration_ms: int = 600,
        min_speech_ms: int = 300,
        max_speech_ms: int = 30_000,
    ) -> None:
        self._sr = sample_rate
        # Convertir dB a amplitud lineal (int16 max = 32768)
        self._thresh = (10 ** (silence_threshold_db / 20.0)) * 32768.0
        self._silence_frames = int(silence_duration_ms * sample_rate / 1000)
        self._min_frames = int(min_speech_ms * sample_rate / 1000)
        self._max_frames = int(max_speech_ms * sample_rate / 1000)
        self._reset()

    # ── API pública ───────────────────────────────────────────────────────

    def feed(self, chunk: np.ndarray) -> bytes | None:
        """
        Alimenta un chunk de audio (int16).
        Retorna bytes WAV del segmento completo, o None si aún no termina.
        """
        rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
        is_voice = rms > self._thresh

        if is_voice:
            self._in_speech = True
            self._silence_count = 0
            self._buffer.append(chunk.copy())
            self._total_frames += len(chunk)
        elif self._in_speech:
            # Silencio después de voz — acumular para no cortar respiraciones
            self._buffer.append(chunk.copy())
            self._silence_count += len(chunk)
            self._total_frames += len(chunk)

            if self._silence_count >= self._silence_frames:
                return self._emit()
        # else: silencio antes de hablar → ignorar

        # Duración máxima por segmento
        if self._total_frames >= self._max_frames:
            return self._emit()

        return None

    def flush(self) -> bytes | None:
        """Fuerza la emisión de cualquier segmento pendiente (al detener el pipeline)."""
        if self._in_speech and self._buffer:
            return self._emit()
        return None

    def reset(self) -> None:
        """Reinicia el estado interno."""
        self._reset()

    # ── Internals ─────────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._buffer: list[np.ndarray] = []
        self._silence_count: int = 0
        self._total_frames: int = 0
        self._in_speech: bool = False

    def _emit(self) -> bytes | None:
        if not self._buffer:
            self._reset()
            return None

        audio = np.concatenate(self._buffer)
        # Descontar el silencio final para calcular si hay suficiente voz
        voice_frames = self._total_frames - self._silence_count
        self._reset()

        if voice_frames < self._min_frames:
            return None  # demasiado corto — probablemente ruido

        return _to_wav_bytes(audio, self._sr)


# ── Helper ────────────────────────────────────────────────────────────────────

def _to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """Convierte un array int16 a bytes WAV en memoria."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)          # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(audio.astype(np.int16).tobytes())
    return buf.getvalue()
