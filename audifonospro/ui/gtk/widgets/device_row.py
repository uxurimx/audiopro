"""
DeviceRow — Adw.ExpanderRow que representa un dispositivo de audio.

Estructura visual (estilo GNOME Settings):

  🎧 JBL VIBE BUDS                [████████░░] 60% ▾
     A2DP · AAC · 48kHz · ● Conectado
     ├─ Perfil BT          [A2DP ▼]
     ├─ Señal RSSI          -67 dBm  [████░░]
     ├─ PipeWire            Node #139 · running · 0 xruns
     ├─ Micrófono (HFP)     ●──────────────────○
     └─ Asignar persona     [─────── ▼]  [🔊 Output] [🎤 Input]
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib, GObject

from audifonospro.monitor.device_info import AudioDevice, DeviceType


class DeviceRow(Adw.ExpanderRow):
    """
    Fila expandible para un dispositivo de audio.

    Se actualiza in-place con update_device() sin recrearse,
    lo que evita parpadeo en el monitor en tiempo real.
    """

    __gtype_name__ = "AudiofonosDeviceRow"

    def __init__(self, device: AudioDevice, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._device = device
        self._profile_dropdown: Gtk.DropDown | None = None
        self._battery_bar: Gtk.LevelBar | None = None
        self._battery_label: Gtk.Label | None = None
        self._rssi_label: Gtk.Label | None = None
        self._pw_row: Adw.ActionRow | None = None
        self._build_row()

    # ── Construcción inicial ──────────────────────────────────────────────

    def _build_row(self) -> None:
        d = self._device

        # Título y subtítulo
        self.set_title(f"{d.type_icon}  {d.name}")
        self.set_subtitle(d.connection_label)

        # ── Sufijos del header ──
        if d.type == DeviceType.BLUETOOTH:
            # Barra de batería
            self._battery_bar = Gtk.LevelBar()
            self._battery_bar.set_valign(Gtk.Align.CENTER)
            self._battery_bar.set_size_request(64, 6)
            self._battery_bar.set_mode(Gtk.LevelBarMode.CONTINUOUS)
            self._battery_bar.add_offset_value("low",    0.10)
            self._battery_bar.add_offset_value("high",   0.30)
            self._battery_bar.add_offset_value("full",   1.00)
            self._update_battery_bar(d.battery_percent)
            self.add_suffix(self._battery_bar)

            # Label porcentaje
            self._battery_label = Gtk.Label(label="─")
            self._battery_label.set_valign(Gtk.Align.CENTER)
            self._battery_label.add_css_class("dim-label")
            self._battery_label.set_size_request(36, -1)
            if d.battery_percent is not None:
                self._battery_label.set_text(f"{d.battery_percent}%")
            self.add_suffix(self._battery_label)

        # ── Filas expandidas ──
        if d.type == DeviceType.BLUETOOTH:
            self._build_bt_rows()
        self._build_pw_row()
        self._build_assign_row()

    def _build_bt_rows(self) -> None:
        d = self._device

        # ── Perfil BT ──
        profile_row = Adw.ActionRow()
        profile_row.set_title("Perfil Bluetooth")

        profiles = d.available_profiles or ["a2dp-sink", "headset-head-unit"]
        profile_strings = Gtk.StringList.new(profiles)
        self._profile_dropdown = Gtk.DropDown(model=profile_strings)
        self._profile_dropdown.set_valign(Gtk.Align.CENTER)
        if d.bt_profile in profiles:
            self._profile_dropdown.set_selected(profiles.index(d.bt_profile))
        self._profile_dropdown.connect("notify::selected", self._on_profile_changed)
        profile_row.add_suffix(self._profile_dropdown)
        profile_row.set_activatable_widget(self._profile_dropdown)
        self.add_row(profile_row)

        # ── Señal RSSI ──
        rssi_row = Adw.ActionRow()
        rssi_row.set_title("Señal (RSSI)")
        self._rssi_label = Gtk.Label(label="─")
        self._rssi_label.set_valign(Gtk.Align.CENTER)
        self._rssi_label.add_css_class("dim-label")
        if d.rssi_dbm is not None:
            self._rssi_label.set_text(f"{d.rssi_dbm} dBm")
        rssi_row.add_suffix(self._rssi_label)
        self.add_row(rssi_row)


    def _build_pw_row(self) -> None:
        self._pw_row = Adw.ActionRow()
        self._pw_row.set_title("PipeWire")
        self._update_pw_row(self._device)
        self.add_row(self._pw_row)

    def _build_assign_row(self) -> None:
        assign_row = Adw.ActionRow()
        assign_row.set_title("Asignar canal")
        assign_row.set_subtitle("Cinema mode — persona y pista de audio")

        box = Gtk.Box(spacing=8)
        box.set_valign(Gtk.Align.CENTER)

        # Selector de persona
        persons = Gtk.StringList.new(["─", "Papa", "Mamá", "Hija", "Otro"])
        self._person_dropdown = Gtk.DropDown(model=persons)
        self._person_dropdown.set_tooltip_text("Asignar a persona")
        if self._device.assigned_person:
            items = ["─", "Papa", "Mamá", "Hija", "Otro"]
            person_cap = self._device.assigned_person.capitalize()
            if person_cap in items:
                self._person_dropdown.set_selected(items.index(person_cap))
        box.append(self._person_dropdown)

        # Botones Output / Input
        out_btn = Gtk.Button(label="🔊 Output")
        out_btn.add_css_class("pill")
        out_btn.add_css_class("suggested-action")
        out_btn.set_tooltip_text("Usar como salida de audio")
        out_btn.connect("clicked", self._on_set_output)
        box.append(out_btn)

        if self._device.is_input:
            in_btn = Gtk.Button(label="🎤 Input")
            in_btn.add_css_class("pill")
            in_btn.set_tooltip_text("Usar como entrada de audio")
            in_btn.connect("clicked", self._on_set_input)
            box.append(in_btn)

        assign_row.add_suffix(box)
        self.add_row(assign_row)

    # ── Actualización en vivo ─────────────────────────────────────────────

    def update_device(self, device: AudioDevice) -> None:
        """Actualiza la fila con nuevos datos. No recrea el widget."""
        self._device = device
        self.set_subtitle(device.connection_label)
        self._update_battery_bar(device.battery_percent)
        if self._battery_label and device.battery_percent is not None:
            self._battery_label.set_text(f"{device.battery_percent}%")
        if self._rssi_label:
            self._rssi_label.set_text(
                f"{device.rssi_dbm} dBm" if device.rssi_dbm is not None else "─"
            )
        if self._pw_row:
            self._update_pw_row(device)

    def _update_battery_bar(self, percent: int | None) -> None:
        if self._battery_bar is None:
            return
        if percent is not None:
            self._battery_bar.set_value(percent / 100.0)
        else:
            self._battery_bar.set_value(0)

    def _update_pw_row(self, device: AudioDevice) -> None:
        if self._pw_row is None:
            return
        if device.pw_sink_node_id is not None:
            xruns_str = f"  ·  {device.pw_xruns} xruns" if device.pw_xruns > 0 else ""
            rate_str  = f"  ·  {device.pw_sample_rate // 1000}kHz" if device.pw_sample_rate else ""
            self._pw_row.set_subtitle(
                f"Node #{device.pw_sink_node_id}  ·  {device.pw_state}{rate_str}{xruns_str}"
            )
        else:
            self._pw_row.set_subtitle("No detectado en PipeWire")

    # ── Callbacks de controles ────────────────────────────────────────────

    def _on_profile_changed(self, dropdown: Gtk.DropDown, _param: object) -> None:
        if self._device.mac_address is None:
            return
        selected = dropdown.get_selected_item()
        if selected is None:
            return
        profile = selected.get_string()
        import threading
        threading.Thread(
            target=self._apply_profile, args=(self._device.mac_address, profile),
            daemon=True,
        ).start()


    def _on_set_output(self, _btn: Gtk.Button) -> None:
        import threading
        threading.Thread(
            target=self._apply_set_output, daemon=True
        ).start()

    def _on_set_input(self, _btn: Gtk.Button) -> None:
        import threading
        threading.Thread(
            target=self._apply_set_input, daemon=True
        ).start()

    def _apply_set_output(self) -> None:
        """
        1. Cambia el sink por defecto a este dispositivo.
        2. Mueve todos los streams activos a este sink.
        """
        from audifonospro.audio.routing import (
            set_default_sink, move_all_streams_to_sink, get_sink_name_for_mac,
        )
        sink_name = (
            self._device.pw_sink_name
            or (get_sink_name_for_mac(self._device.mac_address)
                if self._device.mac_address else None)
        )
        if not sink_name:
            return
        set_default_sink(sink_name)
        move_all_streams_to_sink(sink_name)

    def _apply_set_input(self) -> None:
        from audifonospro.audio.routing import set_default_sink
        if self._device.pw_source_name:
            # PipeWire: set-default-source
            import subprocess
            subprocess.run(
                ["pactl", "set-default-source", self._device.pw_source_name],
                capture_output=True, timeout=5,
            )

    @staticmethod
    def _apply_profile(mac: str, profile: str) -> None:
        from audifonospro.audio.bluetooth import set_profile
        set_profile(mac, profile)
