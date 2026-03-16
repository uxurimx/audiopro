"""
Gestión de perfiles Bluetooth.

El switch HFP ↔ A2DP es la operación más importante de este módulo:
  - A2DP: calidad alta (AAC/aptX), sin micrófono
  - HFP:  calidad reducida (mSBC/CVSD 16kHz), CON micrófono

El context manager garantiza que A2DP se restaura incluso si hay
un error o el usuario cierra la app abruptamente (atexit + signal).
"""
from __future__ import annotations

import atexit
import signal
import subprocess
import time
from contextlib import contextmanager


def mac_to_card(mac: str) -> str:
    """B4:84:D5:98:E8:31 → bluez_card.B4_84_D5_98_E8_31"""
    return "bluez_card." + mac.replace(":", "_")


def set_profile(mac: str, profile: str, retries: int = 3) -> bool:
    """
    Cambia el perfil de una tarjeta Bluetooth.

    Reintenta hasta 3 veces con 300ms de espera entre intentos,
    porque PipeWire puede tardar un momento en procesar el cambio.

    Perfiles comunes:
      a2dp-sink           → A2DP alta calidad
      headset-head-unit   → HFP mSBC 16kHz (mic disponible)
      headset-head-unit-cvsd → HFP CVSD 8kHz (más compatible)
    """
    card = mac_to_card(mac)
    for attempt in range(retries):
        result = subprocess.run(
            ["pactl", "set-card-profile", card, profile],
            capture_output=True,
        )
        if result.returncode == 0:
            return True
        if attempt < retries - 1:
            time.sleep(0.3)
    return False


def get_active_profile(mac: str) -> str | None:
    """Lee el perfil activo actual de una tarjeta BT vía pactl."""
    card = mac_to_card(mac)
    output = subprocess.run(
        ["pactl", "list", "cards"],
        capture_output=True, text=True, timeout=4,
    ).stdout

    if card not in output:
        return None

    start = output.find(card)
    block = output[start:start + 1500]
    for line in block.splitlines():
        if "Active Profile:" in line:
            return line.split(":", 1)[1].strip()
    return None


class ProfileManager:
    """
    Gestiona el ciclo de vida del perfil BT de un dispositivo.

    Uso típico:
        mgr = ProfileManager("B4:84:D5:98:E8:31")
        mgr.switch_to_hfp()       # activa micrófono
        # ... usar el micrófono ...
        mgr.restore()             # vuelve a A2DP

    O como context manager:
        with ProfileManager("B4:84:D5:98:E8:31").hfp():
            # micrófono disponible aquí
    """

    def __init__(self, mac: str, a2dp_profile: str = "a2dp-sink") -> None:
        self.mac = mac
        self.a2dp_profile = a2dp_profile
        self._original_profile: str | None = None
        self._hfp_active = False
        # Garantizar restauración al salir del proceso
        atexit.register(self.restore)
        signal.signal(signal.SIGTERM, lambda *_: self.restore())

    def switch_to_hfp(self, prefer_msbc: bool = True) -> bool:
        """
        Activa el micrófono cambiando a perfil HFP.

        Intenta mSBC primero (16kHz, mejor calidad).
        Si falla, cae a CVSD (8kHz, más compatible).
        """
        self._original_profile = get_active_profile(self.mac) or self.a2dp_profile

        profiles_to_try = (
            ["headset-head-unit", "headset-head-unit-cvsd"]
            if prefer_msbc
            else ["headset-head-unit-cvsd", "headset-head-unit"]
        )
        for profile in profiles_to_try:
            if set_profile(self.mac, profile):
                self._hfp_active = True
                time.sleep(0.5)  # Esperar a que PipeWire registre el source
                return True
        return False

    def restore(self) -> bool:
        """Restaura el perfil original (normalmente A2DP)."""
        if not self._hfp_active:
            return True
        profile = self._original_profile or self.a2dp_profile
        ok = set_profile(self.mac, profile)
        if ok:
            self._hfp_active = False
        return ok

    @contextmanager
    def hfp(self):
        """Context manager: HFP activo dentro del bloque, A2DP restaurado al salir."""
        self.switch_to_hfp()
        try:
            yield self
        finally:
            self.restore()
