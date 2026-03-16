"""
Listener de eventos táctiles del audífono JBL vía evdev.

Los audífonos JBL Vive Buds aparecen en /dev/input/eventX como un dispositivo
HID que emite teclas multimedia estándar del kernel Linux.

Eventos típicos que emiten:
  KEY_PLAYPAUSE  (164)  → toque simple
  KEY_NEXTSONG   (163)  → doble toque derecho
  KEY_PREVIOUSSONG(165) → doble toque izquierdo
  KEY_VOLUMEUP   (115)  → toque largo derecho
  KEY_VOLUMEDOWN (114)  → toque largo izquierdo

Mapeo de nombres de gesto (usados en la UI) → códigos de tecla:
  single_tap_*     → KEY_PLAYPAUSE
  double_tap_right → KEY_NEXTSONG
  double_tap_left  → KEY_PREVIOUSSONG
  long_press_right → KEY_VOLUMEUP
  long_press_left  → KEY_VOLUMEDOWN
  triple_tap_*     → (no estándar, muchos JBL no los emiten)

Acciones disponibles (se ejecutan con subprocess):
  play_pause, next_track, prev_track,
  vol_up, vol_down, anc_cycle, translator_start
"""
from __future__ import annotations

import subprocess
import threading
from typing import Callable

# Mapa: código de tecla Linux → nombre de gesto canónico
_KEY_TO_GESTURE: dict[int, str] = {
    164: "single_tap_right",   # KEY_PLAYPAUSE
    163: "double_tap_right",   # KEY_NEXTSONG
    165: "double_tap_left",    # KEY_PREVIOUSSONG
    115: "long_press_right",   # KEY_VOLUMEUP
    114: "long_press_left",    # KEY_VOLUMEDOWN
    113: "single_tap_left",    # KEY_MUTE (algunos JBL lo usan así)
}

# Acción por defecto para cada gesto (nombre de acción)
DEFAULT_MAPPING: dict[str, str] = {
    "single_tap_left":   "play_pause",
    "single_tap_right":  "play_pause",
    "double_tap_left":   "prev_track",
    "double_tap_right":  "next_track",
    "long_press_left":   "vol_down",
    "long_press_right":  "vol_up",
    "triple_tap_left":   "── Sin acción ──",
    "triple_tap_right":  "── Sin acción ──",
}


def _run_action(action: str) -> None:
    """Ejecuta la acción correspondiente al gesto detectado."""
    try:
        if action == "play_pause":
            subprocess.run(["playerctl", "play-pause"], check=False, timeout=3)
        elif action == "next_track":
            subprocess.run(["playerctl", "next"], check=False, timeout=3)
        elif action == "prev_track":
            subprocess.run(["playerctl", "previous"], check=False, timeout=3)
        elif action == "vol_up":
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"],
                check=False, timeout=3,
            )
        elif action == "vol_down":
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"],
                check=False, timeout=3,
            )
        elif action == "anc_cycle":
            # TODO Fase 3: señal al gestor ANC
            pass
        elif action == "translator_start":
            # TODO Fase 4: señal al pipeline de traducción
            pass
        # "── Sin acción ──" → no hace nada
    except FileNotFoundError:
        pass  # playerctl/pactl no instalados


class EvdevListener:
    """
    Escucha eventos del audífono JBL en un daemon thread.

    Uso:
        listener = EvdevListener()
        listener.set_mapping("double_tap_right", "next_track")
        path = listener.find_jbl_device()     # busca automáticamente
        if path:
            listener.start(path)
        ...
        listener.stop()
    """

    def __init__(self) -> None:
        self._mapping: dict[str, str] = dict(DEFAULT_MAPPING)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._on_gesture: Callable[[str, str], None] | None = None  # (gesto, acción)

    def set_mapping(self, gesture: str, action: str) -> None:
        self._mapping[gesture] = action

    def set_on_gesture(self, callback: Callable[[str, str], None]) -> None:
        """Callback opcional: recibe (nombre_gesto, nombre_acción) cuando ocurre."""
        self._on_gesture = callback

    @staticmethod
    def find_jbl_device() -> str | None:
        """Devuelve el path /dev/input/eventX del primer JBL encontrado, o None."""
        try:
            import evdev
            for path in evdev.list_devices():
                try:
                    dev = evdev.InputDevice(path)
                    if "JBL" in dev.name.upper() or "BLE" in dev.name.upper():
                        return path
                except Exception:
                    continue
        except ImportError:
            pass
        return None

    def start(self, device_path: str) -> bool:
        """Inicia el listener en un daemon thread. Retorna False si evdev no está."""
        try:
            import evdev  # noqa: F401
        except ImportError:
            return False
        if self._thread and self._thread.is_alive():
            self.stop()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen, args=(device_path,), daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Hilo de escucha ───────────────────────────────────────────────────

    def _listen(self, path: str) -> None:
        try:
            import evdev
            device = evdev.InputDevice(path)
            for event in device.read_loop():
                if self._stop_event.is_set():
                    break
                # Solo teclas en estado DOWN (1 = press, evdev.KeyEvent.key_down)
                if event.type == evdev.ecodes.EV_KEY and event.value == 1:
                    gesture = _KEY_TO_GESTURE.get(event.code)
                    if gesture:
                        action = self._mapping.get(gesture, "── Sin acción ──")
                        if self._on_gesture:
                            self._on_gesture(gesture, action)
                        if action and action != "── Sin acción ──":
                            threading.Thread(
                                target=_run_action, args=(action,), daemon=True
                            ).start()
        except Exception:
            pass  # dispositivo desconectado, permiso denegado, etc.


# Singleton
_listener: EvdevListener | None = None


def get_listener() -> EvdevListener:
    global _listener
    if _listener is None:
        _listener = EvdevListener()
    return _listener
