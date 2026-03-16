"""
Tab 1 — Dispositivos.

Muestra todos los dispositivos de audio disponibles con sus controles:
  - Selección de input/output para el Traductor
  - Asignación de persona (Cinema Mode)
  - Indicador de estado compacto

Los datos vienen del mismo worker que el Monitor tab,
pero aquí el foco es la selección y asignación, no las métricas.
"""
from __future__ import annotations

import threading

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Static, DataTable
from textual.containers import Vertical, VerticalScroll

from audifonospro.config import Settings
from audifonospro.monitor.device_info import AudioDevice, DeviceType


class DevicesTab(Widget):
    DEFAULT_CSS = """
    DevicesTab {
        height: 1fr;
        padding: 0 1;
    }
    #devices-table-wrap {
        height: 1fr;
    }
    #devices-hint {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    COLUMNS = [
        ("Dispositivo",  30),
        ("Tipo",          8),
        ("Estado",       20),
        ("Batería",      12),
        ("Señal",        12),
        ("Perfil",       18),
        ("Persona",      10),
    ]

    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._stop_event = threading.Event()
        self._devices: list[AudioDevice] = []
        self._selected_input: str | None  = None
        self._selected_output: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            "[dim]↑↓ navegar · Enter seleccionar · I = input · O = output · A = asignar persona[/dim]",
            id="devices-hint",
        )
        with Vertical(id="devices-table-wrap"):
            table = DataTable(id="devices-table", cursor_type="row")
            for col_name, col_width in self.COLUMNS:
                table.add_column(col_name, width=col_width)
            yield table

    def on_mount(self) -> None:
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="devices-poll"
        )
        self._poll_thread.start()

    def on_unmount(self) -> None:
        self._stop_event.set()

    def _poll_loop(self) -> None:
        from audifonospro.monitor.device_enumerator import enumerate_all_devices
        import time

        interval = self.settings.ui.refresh_rate_ms / 1000.0
        while not self._stop_event.wait(timeout=interval):
            try:
                devices = enumerate_all_devices()
                self.app.call_from_thread(self._refresh_table, devices)
            except Exception:
                pass

    def _refresh_table(self, devices: list[AudioDevice]) -> None:
        """Actualiza la DataTable con la lista de devices. Corre en el main thread."""
        self._devices = devices
        table = self.query_one("#devices-table", DataTable)
        table.clear()

        for device in devices:
            # Batería
            if device.battery_percent is not None:
                bat = f"{device.battery_percent}%"
            else:
                bat = "─" if device.type == DeviceType.BLUETOOTH else "N/A"

            # Señal
            if device.rssi_dbm is not None:
                sig = f"{device.rssi_dbm} dBm"
            else:
                sig = "─" if device.type == DeviceType.BLUETOOTH else "N/A"

            # Estado
            status = "● " + device.connection_label if device.connected else "○ Desconectado"

            # Perfil
            profile = device.bt_profile or device.pw_state or "─"

            # Marcadores de input/output seleccionado
            name_prefix = ""
            if device.id == self._selected_input:
                name_prefix += "🎤 "
            if device.id == self._selected_output:
                name_prefix += "🔊 "

            table.add_row(
                name_prefix + device.type_icon + " " + device.name,
                device.type.value[:6],
                status,
                bat,
                sig,
                profile[:18],
                device.assigned_person or "─",
                key=device.id,
            )
