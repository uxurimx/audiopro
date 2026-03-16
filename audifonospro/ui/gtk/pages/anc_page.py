"""
ANCPage — Cancelación de Ruido por Software.

Dos modos vía PipeWire (sin paquetes extra):
  - Micrófono WebRTC : crea fuente virtual "audifonospro ANC Mic".
                       El usuario la selecciona como mic en sus llamadas.
  - Filtro de salida : crea sink virtual "audifonospro Filtro de Ruido".
                       El usuario enruta su app allí para reducir ruido de fondo.

Backend: audifonospro/anc/pipewire_anc.py
"""
from __future__ import annotations

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings


_MODES = ["Micrófono (WebRTC)", "Salida (Filtro bandpass)"]
_MODE_KEYS = ["mic", "out"]

_MODE_HINTS = {
    "mic": (
        "Crea el micrófono virtual «audifonospro ANC Mic» en PipeWire.\n"
        "Selecciónalo como micrófono en tu app de llamadas (Discord, Meet, Zoom, etc.).\n"
        "WebRTC suprime ruido de fondo y ecos automáticamente."
    ),
    "out": (
        "Crea el sink virtual «audifonospro Filtro de Ruido» en PipeWire.\n"
        "Enruta tu app de música/video a ese sink desde GNOME Sound Settings.\n"
        "El filtro elimina zumbidos graves y silbidos agudos según la intensidad."
    ),
}


class ANCPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

        self.set_title("ANC")
        self.set_icon_name("microphone-sensitivity-high-symbolic")

        self._build_mode_group()
        self._build_actions_group()
        self._build_info_group()

        # Actualizar UI según modo inicial
        self._on_mode_changed(self._mode_dd, None)

    # ── Construcción UI ───────────────────────────────────────────────────

    def _build_mode_group(self) -> None:
        group = Adw.PreferencesGroup()
        group.set_title("Configuración")
        self.add(group)

        # Selector de modo
        mode_row = Adw.ActionRow()
        mode_row.set_title("Modo")
        mode_row.set_subtitle("Tipo de cancelación de ruido")
        model = Gtk.StringList.new(_MODES)
        self._mode_dd = Gtk.DropDown(model=model)
        self._mode_dd.set_valign(Gtk.Align.CENTER)
        self._mode_dd.connect("notify::selected", self._on_mode_changed)
        mode_row.add_suffix(self._mode_dd)
        mode_row.set_activatable_widget(self._mode_dd)
        group.add(mode_row)

        # Slider de intensidad (sólo para modo "out")
        self._intensity_row = Adw.ActionRow()
        self._intensity_row.set_title("Intensidad del filtro")
        self._intensity_row.set_subtitle("Agresividad del filtro pasa-bandas")

        intensity_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        intensity_box.set_valign(Gtk.Align.CENTER)

        self._intensity_label = Gtk.Label(label="50%")
        self._intensity_label.set_width_chars(4)
        self._intensity_label.add_css_class("numeric")
        intensity_box.append(self._intensity_label)

        self._intensity_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self._intensity_scale.set_range(0, 100)
        self._intensity_scale.set_value(50)
        self._intensity_scale.set_size_request(180, -1)
        self._intensity_scale.set_draw_value(False)
        self._intensity_scale.add_mark(0,   Gtk.PositionType.BOTTOM, "Suave")
        self._intensity_scale.add_mark(50,  Gtk.PositionType.BOTTOM, None)
        self._intensity_scale.add_mark(100, Gtk.PositionType.BOTTOM, "Intenso")
        self._intensity_scale.connect("value-changed", self._on_intensity_changed)
        intensity_box.append(self._intensity_scale)

        self._intensity_row.add_suffix(intensity_box)
        group.add(self._intensity_row)

    def _build_actions_group(self) -> None:
        group = Adw.PreferencesGroup()
        group.set_title("Control")
        self.add(group)

        self._status_row = Adw.ActionRow()
        self._status_row.set_title("Estado")
        self._status_row.set_subtitle("ANC desactivado")

        btn_box = Gtk.Box(spacing=8)
        btn_box.set_valign(Gtk.Align.CENTER)

        self._stop_btn = Gtk.Button(label="Desactivar")
        self._stop_btn.add_css_class("destructive-action")
        self._stop_btn.set_sensitive(False)
        self._stop_btn.connect("clicked", self._on_stop)
        btn_box.append(self._stop_btn)

        self._apply_btn = Gtk.Button(label="Activar ANC")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.connect("clicked", self._on_apply)
        btn_box.append(self._apply_btn)

        self._spinner = Gtk.Spinner()
        btn_box.append(self._spinner)

        self._status_row.add_suffix(btn_box)
        group.add(self._status_row)

    def _build_info_group(self) -> None:
        group = Adw.PreferencesGroup()
        group.set_title("Instrucciones")
        self.add(group)

        self._hint_row = Adw.ActionRow()
        self._hint_row.set_activatable(False)
        self._hint_lbl = Gtk.Label()
        self._hint_lbl.set_wrap(True)
        self._hint_lbl.set_xalign(0)
        self._hint_lbl.set_margin_top(8)
        self._hint_lbl.set_margin_bottom(8)
        self._hint_lbl.add_css_class("dim-label")
        self._hint_row.set_child(self._hint_lbl)
        group.add(self._hint_row)

    # ── Callbacks ─────────────────────────────────────────────────────────

    def _on_mode_changed(self, dd: Gtk.DropDown, _param: object) -> None:
        idx  = dd.get_selected()
        mode = _MODE_KEYS[idx] if idx < len(_MODE_KEYS) else "mic"
        self._intensity_row.set_visible(mode == "out")
        self._hint_lbl.set_text(_MODE_HINTS.get(mode, ""))

    def _on_intensity_changed(self, scale: Gtk.Scale) -> None:
        self._intensity_label.set_text(f"{int(scale.get_value())}%")

    def _on_apply(self, _btn: Gtk.Button) -> None:
        self._apply_btn.set_sensitive(False)
        self._stop_btn.set_sensitive(False)
        self._spinner.start()
        self._status_row.set_subtitle("Iniciando…")

        idx       = self._mode_dd.get_selected()
        mode      = _MODE_KEYS[idx] if idx < len(_MODE_KEYS) else "mic"
        intensity = int(self._intensity_scale.get_value())

        threading.Thread(
            target=self._apply_thread, args=(mode, intensity), daemon=True
        ).start()

    def _apply_thread(self, mode: str, intensity: int) -> None:
        from audifonospro.anc.pipewire_anc import get_anc
        ok, msg = get_anc().apply(mode, intensity)
        GLib.idle_add(self._on_apply_done, ok, msg, mode)

    def _on_apply_done(self, ok: bool, msg: str, mode: str) -> bool:
        self._spinner.stop()
        self._apply_btn.set_sensitive(True)
        self._stop_btn.set_sensitive(ok)

        if ok:
            device = "audifonospro ANC Mic" if mode == "mic" else "audifonospro Filtro de Ruido"
            self._status_row.set_subtitle(f"Activo — {device}")
            self._status_row.remove_css_class("error")
        else:
            self._status_row.set_subtitle(f"Error: {msg}")
            self._status_row.add_css_class("error")
        return False

    def _on_stop(self, _btn: Gtk.Button) -> None:
        from audifonospro.anc.pipewire_anc import get_anc
        get_anc().stop()
        self._status_row.set_subtitle("ANC desactivado")
        self._status_row.remove_css_class("error")
        self._stop_btn.set_sensitive(False)
