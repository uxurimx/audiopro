"""
Tab 2 — Monitor en tiempo real.

Arquitectura de actualización:
  - Worker thread (daemon): llama enumerate_all_devices() cada 500ms
  - Resultado se pasa al hilo principal via post_message()
  - Hilo principal actualiza o crea DeviceCard widgets en el DOM

Este patrón evita bloquear el event loop de Textual con las
llamadas de subprocess (bluetoothctl, pw-dump, upower).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Static
from textual.scroll_view import ScrollView
from textual.containers import VerticalScroll

from audifonospro.config import Settings
from audifonospro.monitor.device_info import AudioDevice


# ── Mensaje para comunicar thread → main loop ─────────────────────────────────

class DevicesRefreshed(Message):
    """Publicado por el worker thread cuando llega una nueva lectura de devices."""
    def __init__(self, devices: list[AudioDevice]) -> None:
        super().__init__()
        self.devices = devices


# ── Tab principal ─────────────────────────────────────────────────────────────

class MonitorTab(Widget):
    DEFAULT_CSS = """
    MonitorTab {
        height: 1fr;
        padding: 0 1;
    }
    #monitor-header {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    #device-scroll {
        height: 1fr;
    }
    #no-devices {
        padding: 2 4;
        color: $text-muted;
        text-style: italic;
    }
    """

    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        # Registro de tarjetas por device_id para updates in-place
        self._cards: dict[str, object] = {}   # id → DeviceCard

    def compose(self) -> ComposeResult:
        yield Static("Iniciando monitor...", id="monitor-header")
        with VerticalScroll(id="device-scroll"):
            yield Static(
                "Buscando dispositivos de audio...",
                id="no-devices",
            )

    def on_mount(self) -> None:
        """Arranca el worker thread de polling al montar el tab."""
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="monitor-poll"
        )
        self._poll_thread.start()

    def on_unmount(self) -> None:
        """Detiene el worker al salir del tab."""
        self._stop_event.set()

    # ── Worker (hilo separado) ────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """
        Loop de polling que corre en un thread daemon.

        Importa enumerate_all_devices dentro del thread para que las
        llamadas de subprocess no bloqueen el event loop de Textual.
        """
        from audifonospro.monitor.device_enumerator import enumerate_all_devices

        interval = self.settings.ui.refresh_rate_ms / 1000.0

        while not self._stop_event.wait(timeout=interval):
            try:
                devices = enumerate_all_devices()
                self.post_message(DevicesRefreshed(devices))
            except Exception:
                pass  # Silencioso: el monitor es best-effort

    # ── Manejador del mensaje (hilo principal) ────────────────────────────

    def on_devices_refreshed(self, event: DevicesRefreshed) -> None:
        """
        Recibe nuevos datos y actualiza el DOM.

        Crea tarjetas nuevas para devices que aparecen por primera vez,
        actualiza in-place las existentes (evita parpadeo).
        """
        from audifonospro.ui.widgets.device_card import DeviceCard

        devices = event.devices
        container = self.query_one("#device-scroll", VerticalScroll)

        # Quitar el placeholder "Buscando..." si hay devices
        try:
            placeholder = self.query_one("#no-devices", Static)
            if devices:
                placeholder.remove()
        except Exception:
            pass

        # Actualizar o crear tarjetas
        current_ids = {d.id for d in devices}

        for device in devices:
            if device.id in self._cards:
                # Actualización in-place — sin recrear el widget
                self._cards[device.id].update_device(device)
            else:
                # Primera vez que vemos este device: crear tarjeta
                card = DeviceCard(device, id=f"card-{_safe_id(device.id)}")
                self._cards[device.id] = card
                container.mount(card)

        # Marcar como desconectado los devices que desaparecieron
        for dev_id in list(self._cards.keys()):
            if dev_id not in current_ids:
                # Puede ser que se desconectó: mantener tarjeta pero marcar
                # (en una futura iteración se eliminará si sigue sin aparecer)
                pass

        # Header con timestamp
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        n  = len(devices)
        self.query_one("#monitor-header", Static).update(
            f"[dim]📡 {n} dispositivo{'s' if n != 1 else ''} · "
            f"actualizado {ts} · "
            f"intervalo {self.settings.ui.refresh_rate_ms}ms[/dim]"
        )


def _safe_id(device_id: str) -> str:
    """Convierte un device ID a un ID válido para Textual (sin ':' ni espacios)."""
    return device_id.replace(":", "_").replace(" ", "_").lower()
