"""
DevicesPage — lista de dispositivos + routing de streams activos.

Secciones:
  1. Dispositivos de audio  — DeviceRow expandibles con controles BT/EQ
  2. Streams activos        — qué app está sonando y en qué dispositivo,
                              con dropdown para cambiar a cualquier sink
  3. Cinema / MKV           — abrir un archivo MKV y asignar pistas
"""
from __future__ import annotations

import threading
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings


class DevicesPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._rows: dict[str, object] = {}
        self._stream_rows: dict[int, _StreamRow] = {}   # serial → widget
        self._running = True

        self.set_title("Dispositivos")
        self.set_icon_name("audio-headphones-symbolic")

        # ── Sección 1: Dispositivos ───────────────────────────────────────
        self._dev_group = Adw.PreferencesGroup()
        self._dev_group.set_title("Dispositivos de audio")
        self._dev_group.set_header_suffix(self._build_refresh_btn())
        self.add(self._dev_group)

        self._empty_row = Adw.ActionRow()
        self._empty_row.set_title("Escaneando dispositivos…")
        self._dev_group.add(self._empty_row)

        # ── Sección 2: Streams activos ────────────────────────────────────
        self._stream_group = Adw.PreferencesGroup()
        self._stream_group.set_title("Streams activos")
        self._stream_group.set_description(
            "Apps que están reproduciendo audio ahora mismo. "
            "Cambia el destino con el selector."
        )
        self.add(self._stream_group)

        self._no_streams_row = Adw.ActionRow()
        self._no_streams_row.set_title("Sin streams activos")
        self._no_streams_row.set_subtitle("No hay aplicaciones reproduciendo audio")
        self._stream_group.add(self._no_streams_row)

        # ── Sección 3: Cinema / MKV ───────────────────────────────────────
        self._cinema_group = Adw.PreferencesGroup()
        self._cinema_group.set_title("Cinema — abrir archivo")
        self._cinema_group.set_description(
            "Carga un MKV/MP4 y asigna cada pista de audio a un dispositivo distinto"
        )
        self.add(self._cinema_group)
        self._build_cinema_row()

        # Arrancar polling
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ── Botón refresh ─────────────────────────────────────────────────────

    def _build_refresh_btn(self) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_icon_name("view-refresh-symbolic")
        btn.set_tooltip_text("Actualizar lista")
        btn.add_css_class("flat")
        btn.connect("clicked", self._on_refresh)
        return btn

    def _build_cinema_row(self) -> None:
        row = Adw.ActionRow()
        row.set_title("Archivo de video")
        self._mkv_path_label = Gtk.Label(label="Ningún archivo seleccionado")
        self._mkv_path_label.add_css_class("dim-label")
        self._mkv_path_label.set_ellipsize(3)   # PANGO_ELLIPSIZE_END
        self._mkv_path_label.set_max_width_chars(30)
        self._mkv_path_label.set_valign(Gtk.Align.CENTER)
        row.add_suffix(self._mkv_path_label)

        open_btn = Gtk.Button(label="Abrir…")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.add_css_class("flat")
        open_btn.connect("clicked", self._on_open_mkv)
        row.add_suffix(open_btn)
        self._cinema_group.add(row)

    # ── Polling ───────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            # Dispositivos cada 2s
            try:
                from audifonospro.monitor.device_enumerator import enumerate_all_devices
                devices = enumerate_all_devices()
            except Exception:
                devices = []
            # Streams cada poll (también 2s)
            try:
                from audifonospro.audio.routing import list_sink_inputs, list_sinks
                inputs = list_sink_inputs()
                sinks  = list_sinks()
            except Exception:
                inputs, sinks = [], []

            GLib.idle_add(self._refresh_rows, devices)
            GLib.idle_add(self._refresh_streams, inputs, sinks)
            time.sleep(2.0)

    def _refresh_rows(self, devices: list) -> bool:
        from audifonospro.ui.gtk.widgets.device_row import DeviceRow

        self._empty_row.set_visible(len(devices) == 0)

        seen_ids: set[str] = set()
        for device in devices:
            seen_ids.add(device.id)
            if device.id in self._rows:
                self._rows[device.id].update_device(device)
            else:
                row = DeviceRow(device)
                self._rows[device.id] = row
                self._dev_group.add(row)

        for dev_id in set(self._rows.keys()) - seen_ids:
            self._dev_group.remove(self._rows.pop(dev_id))

        return False

    def _refresh_streams(self, inputs: list[dict], sinks: list[dict]) -> bool:
        self._no_streams_row.set_visible(len(inputs) == 0)

        seen_serials: set[int] = set()
        for inp in inputs:
            serial = inp["serial"]
            seen_serials.add(serial)
            if serial in self._stream_rows:
                self._stream_rows[serial].update(inp, sinks)
            else:
                sr = _StreamRow(inp, sinks)
                self._stream_rows[serial] = sr
                self._stream_group.add(sr)

        for serial in set(self._stream_rows.keys()) - seen_serials:
            self._stream_group.remove(self._stream_rows.pop(serial))

        return False

    # ── Callbacks ─────────────────────────────────────────────────────────

    def _on_refresh(self, _btn: Gtk.Button) -> None:
        try:
            from audifonospro.monitor.device_enumerator import enumerate_all_devices
            from audifonospro.audio.routing import list_sink_inputs, list_sinks
            self._refresh_rows(enumerate_all_devices())
            self._refresh_streams(list_sink_inputs(), list_sinks())
        except Exception:
            pass

    def _on_open_mkv(self, _btn: Gtk.Button) -> None:
        from gi.repository import Gio
        dialog = Gtk.FileDialog()
        dialog.set_title("Abrir archivo de video")

        f = Gtk.FileFilter()
        f.set_name("Video (MKV, MP4, AVI)")
        for pat in ["*.mkv", "*.mp4", "*.avi", "*.mov", "*.webm"]:
            f.add_pattern(pat)

        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dialog.set_filters(store)
        dialog.open(self.get_root(), None, self._on_mkv_chosen)

    def _on_mkv_chosen(self, dialog: Gtk.FileDialog, result: object) -> None:
        try:
            gfile = dialog.open_finish(result)
            path = gfile.get_path()
            self._mkv_path_label.set_text(path)
            # TODO Cinema Mode (Fase 2b): GStreamer multi-track routing
        except Exception:
            pass

    def stop_polling(self) -> None:
        self._running = False


# ── Widget de fila para un stream activo ─────────────────────────────────────

class _StreamRow(Adw.ActionRow):
    """Fila que representa un stream de audio activo con selector de destino."""

    def __init__(self, inp: dict, sinks: list[dict]) -> None:
        super().__init__()
        self._serial = inp["serial"]
        self._updating = False

        self.set_title(inp["app_name"])
        self.set_icon_name("multimedia-player-symbolic")

        # Dropdown de sinks destino
        self._sink_names = [s["name"] for s in sinks]
        sink_labels = [
            f"{s['description'] or s['name']}  [{s['state']}]"
            for s in sinks
        ]
        model = Gtk.StringList.new(sink_labels)
        self._dd = Gtk.DropDown(model=model)
        self._dd.set_valign(Gtk.Align.CENTER)
        self._dd.set_tooltip_text("Redirigir a este dispositivo")

        # Seleccionar el sink actual
        self._set_current_sink(inp["sink_id"])

        self._dd.connect("notify::selected", self._on_sink_selected)
        self.add_suffix(self._dd)
        self.set_activatable_widget(self._dd)

        self._update_subtitle(inp)

    def update(self, inp: dict, sinks: list[dict]) -> None:
        """Actualiza datos del stream (no recrea el widget)."""
        self._updating = True
        self.set_title(inp["app_name"])
        self._update_subtitle(inp)

        # Si cambiaron los sinks disponibles, actualizar modelo
        new_names = [s["name"] for s in sinks]
        if new_names != self._sink_names:
            self._sink_names = new_names
            new_labels = [
                f"{s['description'] or s['name']}  [{s['state']}]"
                for s in sinks
            ]
            self._dd.set_model(Gtk.StringList.new(new_labels))

        self._set_current_sink(inp["sink_id"])
        self._updating = False

    def _update_subtitle(self, inp: dict) -> None:
        parts = []
        if inp.get("media_name"):
            parts.append(inp["media_name"])
        if inp.get("corked"):
            parts.append("pausado")
        self.set_subtitle("  ·  ".join(parts) if parts else "reproduciendo")

    def _set_current_sink(self, sink_id: int) -> None:
        """Selecciona en el dropdown el sink que está usando actualmente."""
        # pactl list sinks short nos da IDs; buscamos por posición en la lista
        from audifonospro.audio.routing import list_sinks
        for i, sink in enumerate(list_sinks()):
            if sink["id"] == sink_id:
                self._updating = True
                self._dd.set_selected(i)
                self._updating = False
                return

    def _on_sink_selected(self, dd: Gtk.DropDown, _param: object) -> None:
        if self._updating:
            return
        idx = dd.get_selected()
        if idx < 0 or idx >= len(self._sink_names):
            return
        sink_name = self._sink_names[idx]
        serial = self._serial
        threading.Thread(
            target=self._do_move, args=(serial, sink_name), daemon=True
        ).start()

    @staticmethod
    def _do_move(serial: int, sink_name: str) -> None:
        from audifonospro.audio.routing import move_stream_to_sink
        move_stream_to_sink(serial, sink_name)
