"""
AudioPage (DevicesPage) — dispositivos, volumen, streams y EQ rápido.

Secciones:
  1. EQ rápido              — preset en un clic (sin salir de la pestaña)
  2. Dispositivos de audio  — DeviceRow expandibles con controles BT
  3. Volumen                — sliders independientes por dispositivo
  4. Streams activos        — redirect de apps al vuelo
  5. Bluetooth              — conectar / emparejar
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

        self.set_title("Audio")
        self.set_icon_name("audio-headphones-symbolic")

        # ── Sección 0: EQ rápido ──────────────────────────────────────────
        eq_group = Adw.PreferencesGroup()
        eq_group.set_title("Ecualizador")
        self.add(eq_group)

        eq_row = Adw.ActionRow()
        eq_row.set_title("Preset rápido")
        eq_row.set_subtitle("Aplica un preset al sink 'audifonospro EQ' en PipeWire")
        self._eq_status_label = Gtk.Label(label="─")
        self._eq_status_label.add_css_class("dim-label")
        self._eq_status_label.set_valign(Gtk.Align.CENTER)
        eq_row.add_suffix(self._eq_status_label)

        _eq_presets = ["Plana", "Vocal clarity", "Bass boost", "Cinema", "Voice call"]
        _eq_gains = {
            "Plana":         [ 0,  0,  0,  0,  0,  0,  0,  0,  0,  0],
            "Vocal clarity": [-2, -1,  0,  2,  4,  5,  4,  3,  2,  1],
            "Bass boost":    [ 6,  5,  4,  2,  0,  0,  0,  0,  0,  0],
            "Cinema":        [ 3,  2,  1,  0,  0,  1,  2,  2,  1,  0],
            "Voice call":    [-3, -3,  0,  3,  5,  5,  4,  2,  0, -2],
        }
        self._eq_gains_map = _eq_gains

        eq_model = Gtk.StringList.new(_eq_presets)
        self._eq_dd = Gtk.DropDown(model=eq_model)
        self._eq_dd.set_valign(Gtk.Align.CENTER)
        self._eq_dd.connect("notify::selected", self._on_eq_preset_changed)
        eq_row.add_suffix(self._eq_dd)
        eq_row.set_activatable_widget(self._eq_dd)
        eq_group.add(eq_row)

        # ── Sección 1: Dispositivos ───────────────────────────────────────
        self._dev_group = Adw.PreferencesGroup()
        self._dev_group.set_title("Dispositivos de audio")
        self._dev_group.set_header_suffix(self._build_refresh_btn())
        self.add(self._dev_group)

        self._empty_row = Adw.ActionRow()
        self._empty_row.set_title("Escaneando dispositivos…")
        self._dev_group.add(self._empty_row)

        # ── Sección 2: Volumen por dispositivo ───────────────────────────────
        self._vol_group = Adw.PreferencesGroup()
        self._vol_group.set_title("Volumen")
        self._vol_group.set_description(
            "Control independiente del volumen de cada dispositivo de salida"
        )
        self.add(self._vol_group)
        self._vol_rows: dict[str, _VolumeRow] = {}   # sink_name → widget

        # ── Sección 3: Streams activos ────────────────────────────────────
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

        # ── Sección 3: Bluetooth Manager ──────────────────────────────────
        self._bt_group = Adw.PreferencesGroup()
        self._bt_group.set_title("Bluetooth")
        self._bt_group.set_description(
            "Conecta, desconecta o empareja dispositivos sin salir de la app"
        )
        self._bt_group.set_header_suffix(self._build_bt_toolbar())
        self.add(self._bt_group)
        self._bt_rows: dict[str, _BTRow] = {}    # mac → widget
        self._bt_status_row = Adw.ActionRow()
        self._bt_status_row.set_title("Dispositivos emparejados")
        self._bt_status_row.set_subtitle("Usa 'Escanear' para buscar nuevos")
        self._bt_group.add(self._bt_status_row)

        # Arrancar polling
        threading.Thread(target=self._poll_loop, daemon=True).start()
        # Cargar dispositivos BT al inicio
        threading.Thread(target=self._load_bt_devices, daemon=True).start()

    # ── Bluetooth toolbar ─────────────────────────────────────────────────

    def _build_bt_toolbar(self) -> Gtk.Box:
        box = Gtk.Box(spacing=4)

        self._bt_scan_btn = Gtk.Button(label="Escanear")
        self._bt_scan_btn.add_css_class("flat")
        self._bt_scan_btn.set_icon_name("bluetooth-symbolic")
        self._bt_scan_btn.connect("clicked", self._on_bt_scan)
        box.append(self._bt_scan_btn)

        self._bt_spinner = Gtk.Spinner()
        self._bt_spinner.set_visible(False)
        box.append(self._bt_spinner)

        return box

    # ── Botón refresh ─────────────────────────────────────────────────────

    def _build_refresh_btn(self) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_icon_name("view-refresh-symbolic")
        btn.set_tooltip_text("Actualizar lista")
        btn.add_css_class("flat")
        btn.connect("clicked", self._on_refresh)
        return btn

    # ── Bluetooth helpers ─────────────────────────────────────────────────

    def _load_bt_devices(self) -> None:
        """Carga la lista de dispositivos emparejados al arrancar."""
        try:
            from audifonospro.monitor.bt_manager import list_paired
            devices = list_paired()
        except Exception:
            devices = []
        GLib.idle_add(self._refresh_bt_rows, devices)

    def _on_bt_scan(self, _btn: Gtk.Button) -> None:
        self._bt_scan_btn.set_sensitive(False)
        self._bt_spinner.set_visible(True)
        self._bt_spinner.start()
        self._bt_status_row.set_subtitle("Escaneando 8 segundos…")
        threading.Thread(target=self._do_bt_scan, daemon=True).start()

    def _do_bt_scan(self) -> None:
        try:
            from audifonospro.monitor.bt_manager import scan
            devices = scan(timeout=8, on_device_found=lambda d: GLib.idle_add(
                self._add_bt_row_if_new, d
            ))
        except Exception:
            devices = []
        GLib.idle_add(self._on_bt_scan_done, devices)

    def _on_bt_scan_done(self, devices: list) -> bool:
        self._bt_spinner.stop()
        self._bt_spinner.set_visible(False)
        self._bt_scan_btn.set_sensitive(True)
        self._refresh_bt_rows(devices)
        self._bt_status_row.set_subtitle(f"{len(devices)} dispositivo(s) encontrado(s)")
        return False

    def _add_bt_row_if_new(self, device: object) -> bool:
        """Añade una fila BT en tiempo real durante el escaneo."""
        mac = device.mac
        if mac not in self._bt_rows:
            row = _BTRow(device)
            self._bt_rows[mac] = row
            self._bt_group.add(row)
        return False

    def _refresh_bt_rows(self, devices: list) -> bool:
        seen: set[str] = set()
        for dev in devices:
            seen.add(dev.mac)
            if dev.mac in self._bt_rows:
                self._bt_rows[dev.mac].update_device(dev)
            else:
                row = _BTRow(dev)
                self._bt_rows[dev.mac] = row
                self._bt_group.add(row)

        for mac in set(self._bt_rows.keys()) - seen:
            self._bt_group.remove(self._bt_rows.pop(mac))

        return False

    # ── EQ rápido ─────────────────────────────────────────────────────────

    def _on_eq_preset_changed(self, dd: Gtk.DropDown, _param: object) -> None:
        item = dd.get_selected_item()
        if item is None:
            return
        name = item.get_string()
        gains = self._eq_gains_map.get(name, [0] * 10)
        self._eq_status_label.set_text("Aplicando…")
        threading.Thread(target=self._apply_eq, args=(gains, name), daemon=True).start()

    def _apply_eq(self, gains: list, preset_name: str) -> None:
        from audifonospro.eq.pipewire_eq import get_eq
        ok, _ = get_eq().apply(gains)
        GLib.idle_add(
            self._eq_status_label.set_text,
            f"✓ {preset_name}" if ok else "Error",
        )

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
            GLib.idle_add(self._refresh_volumes, sinks)

            # Actualizar status.json para la extensión GNOME
            try:
                from audifonospro.dbus.status_writer import write_status
                write_status(devices=[
                    {
                        "name":        d.name,
                        "battery_pct": getattr(d, "battery_level", None),
                        "codec":       getattr(d, "codec", None),
                        "connected":   getattr(d, "connected", True),
                    }
                    for d in devices
                ])
            except Exception:
                pass

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

    def _refresh_volumes(self, sinks: list[dict]) -> bool:
        seen: set[str] = set()
        for sink in sinks:
            name = sink["name"]
            seen.add(name)
            label = sink.get("description") or name
            vol   = sink.get("volume", 50)
            if name in self._vol_rows:
                self._vol_rows[name].update_volume(vol)
            else:
                row = _VolumeRow(name, label, vol)
                self._vol_rows[name] = row
                self._vol_group.add(row)

        for name in set(self._vol_rows.keys()) - seen:
            self._vol_group.remove(self._vol_rows.pop(name))

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

    def stop_polling(self) -> None:
        self._running = False


# ── Widget de fila para un stream activo ─────────────────────────────────────

class _StreamRow(Adw.ActionRow):
    """Fila que representa un stream de audio activo con selector de destino."""

    def __init__(self, inp: dict, sinks: list[dict]) -> None:
        super().__init__()
        self._serial = inp["serial"]
        self._updating = False
        self._user_moved = False   # True cuando el usuario hizo una selección manual

        self.set_title(inp["app_name"])
        self.set_icon_name(inp.get("app_icon") or "multimedia-player-symbolic")

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
        self._set_current_sink_by_id(inp["sink_id"])

        self._dd.connect("notify::selected", self._on_sink_selected)
        self.add_suffix(self._dd)
        self.set_activatable_widget(self._dd)

        self._update_subtitle(inp)

    def update(self, inp: dict, sinks: list[dict]) -> None:
        """Actualiza datos del stream. NO resetea el dropdown si el usuario acaba de mover."""
        self._updating = True
        self.set_title(inp["app_name"])
        if icon := inp.get("app_icon"):
            self.set_icon_name(icon)
        self._update_subtitle(inp)

        new_names = [s["name"] for s in sinks]
        if new_names != self._sink_names:
            self._sink_names = new_names
            new_labels = [
                f"{s['description'] or s['name']}  [{s['state']}]"
                for s in sinks
            ]
            self._dd.set_model(Gtk.StringList.new(new_labels))

        # Actualizar selector SOLO si el sink real cambió externamente
        # (no mientras el usuario acaba de hacer una selección manual)
        if not self._user_moved:
            self._set_current_sink_by_id(inp["sink_id"])

        self._updating = False

    def _update_subtitle(self, inp: dict) -> None:
        parts = []
        if inp.get("media_name"):
            parts.append(inp["media_name"])
        if inp.get("corked"):
            parts.append("pausado")
        self.set_subtitle("  ·  ".join(parts) if parts else "reproduciendo")

    def _set_current_sink_by_id(self, sink_id: int) -> None:
        """Busca el índice del sink en la lista local y actualiza el dropdown."""
        from audifonospro.audio.routing import list_sinks
        current_sinks = list_sinks()
        for i, sink in enumerate(current_sinks):
            if sink["id"] == sink_id and i < len(self._sink_names):
                self._dd.set_selected(i)
                return

    def _on_sink_selected(self, dd: Gtk.DropDown, _param: object) -> None:
        if self._updating:
            return
        idx = dd.get_selected()
        if idx < 0 or idx >= len(self._sink_names):
            return
        sink_name = self._sink_names[idx]
        self._user_moved = True   # evitar que el próximo poll resetee la selección
        serial = self._serial
        threading.Thread(
            target=self._do_smart_move, args=(serial, sink_name), daemon=True
        ).start()

    @staticmethod
    def _do_smart_move(serial: int, sink_name: str) -> None:
        """
        Routing por stream individual:
          1. pactl move-sink-input  — mueve SOLO este stream al sink elegido.
             NO cambia el default sink (eso movería todos los demás también).
          2. pw-metadata target.object — pinea el stream en WirePlumber para
             que lo recuerde entre sesiones y no lo mueva si cambia el default.
        """
        from audifonospro.audio.routing import move_stream_to_sink, pin_stream_to_sink
        move_stream_to_sink(serial, sink_name)
        pin_stream_to_sink(serial, sink_name)


# ── Widget de fila para un dispositivo BT ─────────────────────────────────────

class _BTRow(Adw.ActionRow):
    """Fila para un dispositivo Bluetooth con botones conectar/desconectar."""

    def __init__(self, device: object) -> None:
        super().__init__()
        self._mac = device.mac
        self._updating = False

        self.set_title(device.name)
        self.set_subtitle(device.mac)

        box = Gtk.Box(spacing=6)
        box.set_valign(Gtk.Align.CENTER)

        self._connect_btn = Gtk.Button(label="Conectar")
        self._connect_btn.add_css_class("suggested-action")
        self._connect_btn.connect("clicked", self._on_connect)
        box.append(self._connect_btn)

        self._disconnect_btn = Gtk.Button(label="Desconectar")
        self._disconnect_btn.add_css_class("destructive-action")
        self._disconnect_btn.connect("clicked", self._on_disconnect)
        box.append(self._disconnect_btn)

        self._status_lbl = Gtk.Label()
        self._status_lbl.add_css_class("dim-label")
        self._status_lbl.add_css_class("caption")
        box.append(self._status_lbl)

        self.add_suffix(box)
        self._apply_state(device)

    def update_device(self, device: object) -> None:
        self.set_title(device.name)
        self._apply_state(device)

    def _apply_state(self, device: object) -> None:
        connected = getattr(device, "connected", False)
        paired    = getattr(device, "paired", True)
        self._connect_btn.set_sensitive(not connected)
        self._disconnect_btn.set_sensitive(connected)
        if connected:
            self.set_icon_name("bluetooth-active-symbolic")
            self._status_lbl.set_text("conectado")
        elif paired:
            self.set_icon_name("bluetooth-symbolic")
            self._status_lbl.set_text("emparejado")
        else:
            self.set_icon_name("bluetooth-disabled-symbolic")
            self._status_lbl.set_text("no emparejado")

    def _on_connect(self, _btn: Gtk.Button) -> None:
        self._connect_btn.set_sensitive(False)
        self._status_lbl.set_text("conectando…")
        threading.Thread(target=self._do_action, args=("connect",), daemon=True).start()

    def _on_disconnect(self, _btn: Gtk.Button) -> None:
        self._disconnect_btn.set_sensitive(False)
        self._status_lbl.set_text("desconectando…")
        threading.Thread(target=self._do_action, args=("disconnect",), daemon=True).start()

    def _do_action(self, action: str) -> None:
        from audifonospro.monitor.bt_manager import connect, disconnect, _get_device_props
        if action == "connect":
            connect(self._mac)
        else:
            disconnect(self._mac)
        connected, _ = _get_device_props(self._mac)
        GLib.idle_add(self._on_action_done, connected)

    def _on_action_done(self, connected: bool) -> bool:
        self._connect_btn.set_sensitive(not connected)
        self._disconnect_btn.set_sensitive(connected)
        self._status_lbl.set_text("conectado" if connected else "emparejado")
        if connected:
            self.set_icon_name("bluetooth-active-symbolic")
        else:
            self.set_icon_name("bluetooth-symbolic")
        return False


# ── Widget de fila de volumen ─────────────────────────────────────────────────

class _VolumeRow(Adw.ActionRow):
    """Slider de volumen para un sink de PulseAudio/PipeWire."""

    def __init__(self, sink_name: str, label: str, volume: int) -> None:
        super().__init__()
        self._sink_name = sink_name
        self._dragging  = False

        self.set_title(label)
        self.set_subtitle(sink_name)
        self.set_icon_name("audio-volume-high-symbolic")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_size_request(260, -1)

        self._slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self._slider.set_range(0, 100)
        self._slider.set_draw_value(False)
        self._slider.set_hexpand(True)
        self._slider.set_size_request(180, -1)
        self._slider.connect("change-value", self._on_change_value)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: setattr(self, "_dragging", True))
        drag.connect("drag-end",   self._on_drag_end)
        self._slider.add_controller(drag)

        self._pct_label = Gtk.Label()
        self._pct_label.add_css_class("numeric")
        self._pct_label.add_css_class("dim-label")
        self._pct_label.set_width_chars(4)

        box.append(self._slider)
        box.append(self._pct_label)
        self.add_suffix(box)

        self._set_slider(volume)

    def update_volume(self, volume: int) -> None:
        """Actualiza el slider desde el polling — solo si el usuario no está arrastrando."""
        if not self._dragging:
            self._set_slider(volume)

    def _set_slider(self, volume: int) -> None:
        self._slider.handler_block_by_func(self._on_change_value)
        self._slider.set_value(volume)
        self._slider.handler_unblock_by_func(self._on_change_value)
        self._pct_label.set_text(f"{volume}%")
        self._update_icon(volume)

    def _update_icon(self, volume: int) -> None:
        if volume == 0:
            self.set_icon_name("audio-volume-muted-symbolic")
        elif volume < 40:
            self.set_icon_name("audio-volume-low-symbolic")
        elif volume < 75:
            self.set_icon_name("audio-volume-medium-symbolic")
        else:
            self.set_icon_name("audio-volume-high-symbolic")

    def _on_change_value(
        self, _scale: Gtk.Scale, _scroll: object, value: float
    ) -> bool:
        pct = int(max(0, min(100, value)))
        self._pct_label.set_text(f"{pct}%")
        self._update_icon(pct)
        if not self._dragging:
            # Click puntual: aplicar inmediatamente
            threading.Thread(
                target=self._apply_volume, args=(pct,), daemon=True
            ).start()
        return False

    def _on_drag_end(self, *_: object) -> None:
        self._dragging = False
        pct = int(self._slider.get_value())
        threading.Thread(
            target=self._apply_volume, args=(pct,), daemon=True
        ).start()

    def _apply_volume(self, percent: int) -> None:
        from audifonospro.audio.routing import set_sink_volume
        set_sink_volume(self._sink_name, percent)
