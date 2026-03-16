"""
MonitorPage — vista en tiempo real de todos los dispositivos de audio.

Actualiza cada 500 ms usando un daemon thread + GLib.idle_add().
Usa DeviceRow (Adw.ExpanderRow) que se actualizan in-place sin parpadeo.
"""
from __future__ import annotations

import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings


class MonitorPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._rows: dict[str, object] = {}   # device.id → DeviceRow
        self._running = False

        self.set_title("Monitor")
        self.set_icon_name("utilities-system-monitor-symbolic")

        # Grupo principal de dispositivos
        self._group = Adw.PreferencesGroup()
        self._group.set_title("Dispositivos detectados")
        self._group.set_description("Se actualiza automáticamente cada 500 ms")
        self.add(self._group)

        # Estado inicial
        self._status_row = Adw.ActionRow()
        self._status_row.set_title("Escaneando…")
        self._status_row.set_subtitle("Buscando dispositivos de audio")
        self._group.add(self._status_row)

        # Arrancar hilo de polling
        self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    # ── Polling ──────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            try:
                from audifonospro.monitor.device_enumerator import enumerate_all_devices
                devices = enumerate_all_devices()
            except Exception as exc:
                devices = []
                GLib.idle_add(self._set_error, str(exc))
            else:
                GLib.idle_add(self._refresh_rows, devices)
            time.sleep(0.5)

    def _set_error(self, msg: str) -> bool:
        self._status_row.set_title("Error al escanear")
        self._status_row.set_subtitle(msg)
        self._status_row.set_visible(True)
        return False

    def _refresh_rows(self, devices: list) -> bool:
        from audifonospro.ui.gtk.widgets.device_row import DeviceRow

        # Ocultar fila de estado una vez que hay datos
        self._status_row.set_visible(len(devices) == 0)
        if not devices:
            self._status_row.set_title("Sin dispositivos")
            self._status_row.set_subtitle("No se encontraron dispositivos de audio")

        seen_ids: set[str] = set()
        for device in devices:
            seen_ids.add(device.id)
            if device.id in self._rows:
                self._rows[device.id].update_device(device)
            else:
                row = DeviceRow(device)
                self._rows[device.id] = row
                self._group.add(row)

        # Eliminar filas de dispositivos desconectados
        gone = set(self._rows.keys()) - seen_ids
        for dev_id in gone:
            self._group.remove(self._rows.pop(dev_id))

        return False  # GLib.idle_add no repite

    # ── Ciclo de vida ────────────────────────────────────────────────────

    def stop_polling(self) -> None:
        self._running = False
