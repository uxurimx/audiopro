"""
Control de mpv via IPC socket (JSON protocol).

mpv se lanza con --no-audio porque el audio lo maneja GStreamer con routing
multi-dispositivo. mpv sólo se encarga del video (--no-audio).

Para seek/pause, se envían comandos al socket IPC de mpv en tiempo real.
La deriva entre mpv y GStreamer es mínima (~ms) porque ambos arrancan en t=0
y reproducen a la misma velocidad. El seek sincroniza ambos manualmente.

API:
    player = MpvPlayer()
    player.play("/ruta/video.mkv")
    player.pause_toggle()
    player.seek_to(120.5)   # segundos
    player.stop()
    player.is_running       # bool
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time


_SOCK = "/tmp/audifonospro-mpv.sock"


class MpvPlayer:

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ── Ciclo de vida ──────────────────────────────────────────────────────

    @staticmethod
    def available() -> str | None:
        """Devuelve 'mpv', 'ffplay' o None si no hay reproductor disponible."""
        import shutil
        if shutil.which("mpv"):
            return "mpv"
        if shutil.which("ffplay"):
            return "ffplay"
        return None

    def play(self, path: str) -> bool:
        """
        Lanza el reproductor de video (sin audio — audio va por GStreamer).
        Prefiere mpv (tiene IPC para seek/pause sync).
        Fallback: ffplay (sin control externo, seek/pause no sincronizados).
        Devuelve True si se pudo lanzar.
        """
        self.stop()
        player = self.available()
        if not player:
            return False

        try:
            os.unlink(_SOCK)
        except FileNotFoundError:
            pass

        try:
            if player == "mpv":
                self._proc = subprocess.Popen(
                    [
                        "mpv",
                        "--no-audio",
                        f"--input-ipc-server={_SOCK}",
                        "--keep-open=yes",
                        "--title=Cinema — audifonospro",
                        "--geometry=800x450",
                        "--",
                        path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Esperar socket listo (hasta 3s)
                for _ in range(30):
                    if os.path.exists(_SOCK):
                        break
                    time.sleep(0.1)
            else:
                # ffplay: no tiene IPC; seek/pause no se sincronizan
                self._proc = subprocess.Popen(
                    ["ffplay", "-an", "-autoexit", "-window_title",
                     "Cinema — audifonospro", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return True
        except Exception:
            return False

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._command_internal(["quit"])
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
        try:
            os.unlink(_SOCK)
        except FileNotFoundError:
            pass

    def pause_toggle(self) -> None:
        self._command(["cycle", "pause"])

    def set_pause(self, paused: bool) -> None:
        self._command(["set_property", "pause", paused])

    def seek_to(self, seconds: float) -> None:
        """Salta a una posición absoluta en segundos."""
        self._command(["seek", round(seconds, 2), "absolute"])

    # ── Estado ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    # ── Internos ──────────────────────────────────────────────────────────

    def _command(self, cmd: list) -> None:
        threading.Thread(
            target=self._command_internal, args=(cmd,), daemon=True
        ).start()

    def _command_internal(self, cmd: list) -> None:
        if not os.path.exists(_SOCK):
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(_SOCK)
                s.sendall(json.dumps({"command": cmd}).encode() + b"\n")
        except Exception:
            pass

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
