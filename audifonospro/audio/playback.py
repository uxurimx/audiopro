"""
Reproducción de audio hacia cualquier dispositivo de salida.

Simétrico a capture.py — el caller pone chunks en la queue
y el callback de sounddevice los consume en orden.

Si la queue está vacía (underrun), reproduce silencio para evitar
glitches. Si la queue está llena (overrun), descarta el chunk más
viejo para no acumular latencia.
"""
from __future__ import annotations

import queue

import numpy as np
import sounddevice as sd

from audifonospro.config import Settings


class AudioPlayback:
    """
    Reproducción de audio en streaming.

    Ejemplo de uso:
        playback = AudioPlayback(settings)
        playback.start(device="JBL")
        while data_available:
            chunk = get_audio_chunk()       # numpy float32
            playback.write(chunk)
        playback.stop()
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=settings.pipeline.queue_maxsize_audio_out
        )
        self._stream: sd.OutputStream | None = None
        self._device_name: str | None = None
        self._blocksize = int(
            settings.audio.sample_rate * settings.audio.buffer_ms / 1000
        )

    # ── Control ───────────────────────────────────────────────────────────

    def start(self, device: str | int | None = None) -> None:
        if self._stream is not None:
            self.stop()

        resolved = self._resolve_device(device or self.settings.audio.output_device)
        self._device_name = str(resolved) if resolved is not None else "default"

        self._stream = sd.OutputStream(
            device=resolved,
            samplerate=self.settings.audio.sample_rate,
            channels=self.settings.audio.channels,
            dtype="float32",
            blocksize=self._blocksize,
            callback=self._callback,
            latency="low",
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def clear(self) -> None:
        """Vacía la queue (cancela audio pendiente)."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    # ── Escritura ─────────────────────────────────────────────────────────

    def write(self, audio: np.ndarray, timeout: float = 0.1) -> bool:
        """
        Envía un chunk de audio a la cola de reproducción.

        Retorna True si se encoló, False si la cola estaba llena.
        """
        try:
            self._queue.put(audio, timeout=timeout)
            return True
        except queue.Full:
            # Descartar el chunk más viejo para mantener baja latencia
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(audio)
            except queue.Empty:
                pass
            return False

    @property
    def is_running(self) -> bool:
        return self._stream is not None and self._stream.active

    @property
    def queue_fill(self) -> float:
        """Nivel de llenado de la cola [0.0 – 1.0]."""
        max_size = self._queue.maxsize
        return self._queue.qsize() / max_size if max_size > 0 else 0.0

    @property
    def device_name(self) -> str:
        return self._device_name or "─"

    # ── Callback ──────────────────────────────────────────────────────────

    def _callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        try:
            chunk = self._queue.get_nowait()
            # Adaptar si el tamaño del chunk no coincide exactamente
            if len(chunk) >= frames:
                outdata[:] = chunk[:frames].reshape(outdata.shape)
            else:
                outdata[: len(chunk)] = chunk.reshape(-1, outdata.shape[1])
                outdata[len(chunk) :] = 0
        except queue.Empty:
            outdata[:] = 0  # Silencio en underrun (no glitch)

    # ── Utilidades ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_device(device: str | int | None) -> str | int | None:
        if device is None or device == "auto":
            return None
        if isinstance(device, int):
            return device
        query = device.lower()
        for i, dev in enumerate(sd.query_devices()):
            if query in dev["name"].lower() and dev["max_output_channels"] > 0:
                return i
        return None

    @staticmethod
    def list_output_devices() -> list[dict]:
        return [
            {"index": i, "name": dev["name"], "channels": dev["max_output_channels"],
             "sample_rate": int(dev["default_samplerate"])}
            for i, dev in enumerate(sd.query_devices())
            if dev["max_output_channels"] > 0
        ]
