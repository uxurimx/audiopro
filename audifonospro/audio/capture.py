"""
Captura de audio desde cualquier dispositivo (BT, jack, built-in).

Diseño:
  - sounddevice InputStream con callback no-bloqueante
  - Los chunks de audio van a una queue.Queue con backpressure
  - El caller consume de la queue en su propio hilo/async

El callback de sounddevice corre en un hilo C de PortAudio —
nunca se bloquea, solo pone en la queue o descarta si está llena.
"""
from __future__ import annotations

import queue
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

from audifonospro.config import Settings


class AudioCapture:
    """
    Captura de audio en streaming.

    Ejemplo de uso:
        capture = AudioCapture(settings)
        capture.start(device="JBL")        # nombre parcial del dispositivo
        while True:
            chunk = capture.read()          # numpy float32 (frames, channels)
            process(chunk)
        capture.stop()
    """

    def __init__(
        self,
        settings: Settings,
        on_chunk: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self.settings   = settings
        self.on_chunk   = on_chunk          # callback opcional (alternativa a read())
        self._queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=settings.pipeline.queue_maxsize_raw
        )
        self._stream: sd.InputStream | None = None
        self._device_name: str | None = None

    # ── Control ───────────────────────────────────────────────────────────

    def start(self, device: str | int | None = None) -> None:
        """
        Inicia la captura.

        Args:
            device: nombre parcial del dispositivo, índice sounddevice,
                    o None para usar el dispositivo por defecto del sistema.
        """
        if self._stream is not None:
            self.stop()

        resolved = self._resolve_device(device or self.settings.audio.input_device)
        self._device_name = str(resolved) if resolved is not None else "default"

        blocksize = int(
            self.settings.audio.sample_rate
            * self.settings.audio.buffer_ms
            / 1000
        )

        self._stream = sd.InputStream(
            device=resolved,
            samplerate=self.settings.audio.sample_rate,
            channels=self.settings.audio.channels,
            dtype="float32",
            blocksize=blocksize,
            callback=self._callback,
            latency="low",
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ── Lectura ───────────────────────────────────────────────────────────

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Lee el siguiente chunk de la queue (bloqueante hasta timeout)."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def read_nowait(self) -> np.ndarray | None:
        """Lee el siguiente chunk sin bloquear. Retorna None si no hay datos."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    @property
    def is_running(self) -> bool:
        return self._stream is not None and self._stream.active

    @property
    def device_name(self) -> str:
        return self._device_name or "─"

    # ── Callback ──────────────────────────────────────────────────────────

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        chunk = indata.copy()
        if self.on_chunk:
            self.on_chunk(chunk)
        else:
            try:
                self._queue.put_nowait(chunk)
            except queue.Full:
                pass  # Descarta el chunk más viejo implícitamente (backpressure)

    # ── Utilidades ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_device(device: str | int | None) -> str | int | None:
        """
        Resuelve un nombre parcial de dispositivo al índice sounddevice.

        "JBL" → busca el primer dispositivo de entrada cuyo nombre
                contenga "JBL" (case-insensitive).
        "auto" | None → None (usa el dispositivo del sistema).
        int → pasa directo.
        """
        if device is None or device == "auto":
            return None
        if isinstance(device, int):
            return device

        query = device.lower()
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if query in dev["name"].lower() and dev["max_input_channels"] > 0:
                return i
        # No encontrado — usar default
        return None

    @staticmethod
    def list_input_devices() -> list[dict]:
        """Lista todos los dispositivos de entrada disponibles."""
        return [
            {"index": i, "name": dev["name"], "channels": dev["max_input_channels"],
             "sample_rate": int(dev["default_samplerate"])}
            for i, dev in enumerate(sd.query_devices())
            if dev["max_input_channels"] > 0
        ]
