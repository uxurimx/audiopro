"""
EQPage — ecualizador de 10 bandas por persona/dispositivo.

Usa Gtk.Scale vertical para cada banda de frecuencia.
Presets para: Plana, Vocal clarity (para hipoacusia), Bass boost, Cinema.

Backend: PipeWire filter-chain con nodos bq_peaking (ver eq/pipewire_eq.py).
Al aplicar, crea un sink virtual "audifonospro EQ" en el grafo de PipeWire.
"""
from __future__ import annotations

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings

# Bandas ISO 1/3 de octava (10 bandas estándar), en Hz
_BANDS_HZ = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

_PRESETS: dict[str, list[float]] = {
    "Plana":            [ 0,  0,  0,  0,  0,  0,  0,  0,  0,  0],
    "Vocal clarity":    [-2, -1,  0,  2,  4,  5,  4,  3,  2,  1],
    "Bass boost":       [ 6,  5,  4,  2,  0,  0,  0,  0,  0,  0],
    "Cinema":           [ 3,  2,  1,  0,  0,  1,  2,  2,  1,  0],
    "Voice call":       [-3, -3,  0,  3,  5,  5,  4,  2,  0, -2],
}

_PERSONS = ["─", "Papa", "Mamá", "Hija", "Otro"]


class EQPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._scales: list[Gtk.Scale] = []

        self.set_title("EQ")
        self.set_icon_name("multimedia-volume-control-symbolic")

        # ── Selector persona + preset ─────────────────────────────────────
        header_group = Adw.PreferencesGroup()
        self.add(header_group)

        person_row = Adw.ActionRow()
        person_row.set_title("Persona")
        persons_model = Gtk.StringList.new(_PERSONS)
        self._person_dd = Gtk.DropDown(model=persons_model)
        self._person_dd.set_valign(Gtk.Align.CENTER)
        person_row.add_suffix(self._person_dd)
        person_row.set_activatable_widget(self._person_dd)
        header_group.add(person_row)

        preset_row = Adw.ActionRow()
        preset_row.set_title("Preset")
        preset_model = Gtk.StringList.new(list(_PRESETS.keys()))
        self._preset_dd = Gtk.DropDown(model=preset_model)
        self._preset_dd.set_valign(Gtk.Align.CENTER)
        self._preset_dd.connect("notify::selected", self._on_preset_changed)
        preset_row.add_suffix(self._preset_dd)
        preset_row.set_activatable_widget(self._preset_dd)
        header_group.add(preset_row)

        # ── Faders verticales ─────────────────────────────────────────────
        eq_group = Adw.PreferencesGroup()
        eq_group.set_title("Ecualizador de 10 bandas")
        self.add(eq_group)

        # Box horizontal con un Scale vertical por banda
        faders_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        faders_box.set_halign(Gtk.Align.CENTER)
        faders_box.set_margin_top(8)
        faders_box.set_margin_bottom(8)

        for hz in _BANDS_HZ:
            band_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            band_box.set_halign(Gtk.Align.CENTER)

            # Etiqueta dB encima
            db_label = Gtk.Label(label="0 dB")
            db_label.add_css_class("caption")
            db_label.set_width_chars(5)
            band_box.append(db_label)

            # Fader vertical [-12, +12] dB
            scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL)
            scale.set_range(-12, 12)
            scale.set_value(0)
            scale.set_inverted(True)   # arriba = +, abajo = -
            scale.set_size_request(32, 140)
            scale.set_draw_value(False)
            scale.add_mark(0, Gtk.PositionType.RIGHT, None)
            scale.connect("value-changed", self._on_scale_changed, db_label)
            self._scales.append(scale)
            band_box.append(scale)

            # Etiqueta de frecuencia debajo
            freq_str = f"{hz // 1000}k" if hz >= 1000 else str(hz)
            freq_label = Gtk.Label(label=freq_str)
            freq_label.add_css_class("caption")
            band_box.append(freq_label)

            faders_box.append(band_box)

        # Envolver en un Adw.ActionRow para que quede en el grupo
        wrap_row = Adw.ActionRow()
        wrap_row.set_activatable(False)
        # Añadir el box directamente como child del row
        wrap_row.set_child(faders_box)
        eq_group.add(wrap_row)

        # ── Botones de acción + estado ────────────────────────────────────
        actions_group = Adw.PreferencesGroup()
        self.add(actions_group)

        btn_row = Adw.ActionRow()
        btn_row.set_title("Aplicar")
        self._apply_subtitle = "Crea el sink 'audifonospro EQ' en PipeWire"
        btn_row.set_subtitle(self._apply_subtitle)
        self._apply_row = btn_row

        btn_box = Gtk.Box(spacing=8)
        btn_box.set_valign(Gtk.Align.CENTER)

        stop_btn = Gtk.Button(label="Desactivar EQ")
        stop_btn.add_css_class("destructive-action")
        stop_btn.connect("clicked", self._on_stop_eq)
        btn_box.append(stop_btn)

        reset_btn = Gtk.Button(label="Resetear")
        reset_btn.connect("clicked", self._on_reset)
        btn_box.append(reset_btn)

        self._apply_btn = Gtk.Button(label="Aplicar EQ")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.connect("clicked", self._on_apply)
        btn_box.append(self._apply_btn)

        btn_row.add_suffix(btn_box)
        actions_group.add(btn_row)

    # ── Callbacks ────────────────────────────────────────────────────────

    def _on_scale_changed(self, scale: Gtk.Scale, label: Gtk.Label) -> None:
        val = scale.get_value()
        sign = "+" if val > 0 else ""
        label.set_text(f"{sign}{val:.0f}dB")

    def _on_preset_changed(self, dd: Gtk.DropDown, _param: object) -> None:
        item = dd.get_selected_item()
        if item is None:
            return
        name = item.get_string()
        gains = _PRESETS.get(name, [0] * 10)
        for scale, gain in zip(self._scales, gains):
            scale.set_value(gain)

    def _on_reset(self, _btn: Gtk.Button) -> None:
        for scale in self._scales:
            scale.set_value(0)

    def _on_stop_eq(self, _btn: Gtk.Button) -> None:
        from audifonospro.eq.pipewire_eq import get_eq
        get_eq().stop()
        self._apply_row.set_subtitle("EQ desactivado — sink eliminado")

    def _on_apply(self, _btn: Gtk.Button) -> None:
        gains = [s.get_value() for s in self._scales]
        self._apply_btn.set_sensitive(False)
        self._apply_row.set_subtitle("Aplicando…")
        threading.Thread(target=self._apply_eq_thread, args=(gains,), daemon=True).start()

    def _apply_eq_thread(self, gains: list[float]) -> None:
        from audifonospro.eq.pipewire_eq import get_eq
        ok, msg = get_eq().apply(gains)
        GLib.idle_add(self._on_apply_done, ok, msg)

    def _on_apply_done(self, ok: bool, msg: str) -> bool:
        self._apply_btn.set_sensitive(True)
        if ok:
            self._apply_row.set_subtitle(
                "✓ EQ activo — enruta tu app al sink 'audifonospro EQ' en GNOME Sound"
            )
        else:
            self._apply_row.set_subtitle(f"Error: {msg}")
        return False
