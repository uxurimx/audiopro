"""
ControlsPage — mapeo de botones/gestos de los audífonos JBL.

Backend: audifonospro.controls.evdev_listener — escucha /dev/input/eventX
y ejecuta acciones (playerctl, pactl) en un daemon thread.
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings
from audifonospro.controls.evdev_listener import DEFAULT_MAPPING, get_listener

# Etiquetas legibles para las acciones (interno → UI)
_ACTION_LABELS = [
    "── Sin acción ──",
    "Play / Pause",
    "Siguiente pista",
    "Pista anterior",
    "Subir volumen",
    "Bajar volumen",
    "Ciclar nivel ANC",
    "Iniciar traductor",
]

# Mapa UI label → clave interna
_LABEL_TO_KEY = {
    "── Sin acción ──":  "── Sin acción ──",
    "Play / Pause":      "play_pause",
    "Siguiente pista":   "next_track",
    "Pista anterior":    "prev_track",
    "Subir volumen":     "vol_up",
    "Bajar volumen":     "vol_down",
    "Ciclar nivel ANC":  "anc_cycle",
    "Iniciar traductor": "translator_start",
}
_KEY_TO_LABEL = {v: k for k, v in _LABEL_TO_KEY.items()}

_GESTURES = [
    ("Toque simple — izquierdo",  "single_tap_left"),
    ("Toque simple — derecho",    "single_tap_right"),
    ("Doble toque — izquierdo",   "double_tap_left"),
    ("Doble toque — derecho",     "double_tap_right"),
    ("Toque largo — izquierdo",   "long_press_left"),
    ("Toque largo — derecho",     "long_press_right"),
    ("Triple toque — izquierdo",  "triple_tap_left"),
    ("Triple toque — derecho",    "triple_tap_right"),
]


class ControlsPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._dropdowns: dict[str, Gtk.DropDown] = {}
        self._listener_path: str | None = None

        self.set_title("Controles")
        self.set_icon_name("input-gaming-symbolic")

        # ── Grupo: gestos del audífono ────────────────────────────────────
        gestures_group = Adw.PreferencesGroup()
        gestures_group.set_title("Gestos JBL Vibe Buds")
        gestures_group.set_description(
            "Asigna una acción a cada gesto táctil del audífono"
        )
        self.add(gestures_group)

        actions_model = Gtk.StringList.new(_ACTION_LABELS)

        for label, gesture_key in _GESTURES:
            row = Adw.ActionRow()
            row.set_title(label)

            dd = Gtk.DropDown(model=actions_model)
            dd.set_valign(Gtk.Align.CENTER)

            # Pre-seleccionar acción por defecto
            default_action = DEFAULT_MAPPING.get(gesture_key, "── Sin acción ──")
            default_label  = _KEY_TO_LABEL.get(default_action, "── Sin acción ──")
            if default_label in _ACTION_LABELS:
                dd.set_selected(_ACTION_LABELS.index(default_label))

            dd.connect("notify::selected", self._on_action_changed, gesture_key)
            self._dropdowns[gesture_key] = dd

            row.add_suffix(dd)
            row.set_activatable_widget(dd)
            gestures_group.add(row)

        # ── Grupo: dispositivo evdev ──────────────────────────────────────
        evdev_group = Adw.PreferencesGroup()
        evdev_group.set_title("Dispositivo de entrada")
        evdev_group.set_description(
            "El listener escucha los eventos táctiles del audífono en tiempo real"
        )
        self.add(evdev_group)

        # Detección
        self._evdev_row = Adw.ActionRow()
        self._evdev_row.set_title("Dispositivo evdev")
        self._evdev_row.set_subtitle("No detectado")

        detect_btn = Gtk.Button(label="Detectar")
        detect_btn.set_valign(Gtk.Align.CENTER)
        detect_btn.add_css_class("flat")
        detect_btn.connect("clicked", self._on_detect_evdev)
        self._evdev_row.add_suffix(detect_btn)
        evdev_group.add(self._evdev_row)

        # Start / Stop listener
        listen_row = Adw.ActionRow()
        listen_row.set_title("Listener")
        self._listener_status = "Inactivo"
        listen_row.set_subtitle(self._listener_status)
        self._listen_row = listen_row

        ctrl_box = Gtk.Box(spacing=8)
        ctrl_box.set_valign(Gtk.Align.CENTER)

        self._stop_btn = Gtk.Button(label="Detener")
        self._stop_btn.add_css_class("destructive-action")
        self._stop_btn.set_sensitive(False)
        self._stop_btn.connect("clicked", self._on_stop_listener)
        ctrl_box.append(self._stop_btn)

        self._start_btn = Gtk.Button(label="Iniciar listener")
        self._start_btn.add_css_class("suggested-action")
        self._start_btn.set_sensitive(False)
        self._start_btn.connect("clicked", self._on_start_listener)
        ctrl_box.append(self._start_btn)

        listen_row.add_suffix(ctrl_box)
        evdev_group.add(listen_row)

        # ── Último gesto detectado ────────────────────────────────────────
        log_group = Adw.PreferencesGroup()
        log_group.set_title("Último evento")
        self.add(log_group)

        self._last_event_row = Adw.ActionRow()
        self._last_event_row.set_title("Gesto")
        self._last_event_row.set_subtitle("─")
        log_group.add(self._last_event_row)

        # Registrar callback del listener
        get_listener().set_on_gesture(self._on_gesture_received)

    # ── Callbacks evdev ───────────────────────────────────────────────────

    def _on_detect_evdev(self, _btn: Gtk.Button) -> None:
        from audifonospro.controls.evdev_listener import EvdevListener
        path = EvdevListener.find_jbl_device()
        if path:
            try:
                import evdev
                dev = evdev.InputDevice(path)
                self._evdev_row.set_subtitle(f"{path}  ·  {dev.name}")
            except Exception:
                self._evdev_row.set_subtitle(path)
            self._listener_path = path
            self._start_btn.set_sensitive(True)
        else:
            self._evdev_row.set_subtitle(
                "No se encontró JBL — ¿audífono conectado en HFP?"
            )

    def _on_start_listener(self, _btn: Gtk.Button) -> None:
        if not self._listener_path:
            return
        ok = get_listener().start(self._listener_path)
        if ok:
            self._listen_row.set_subtitle(f"Escuchando {self._listener_path}")
            self._start_btn.set_sensitive(False)
            self._stop_btn.set_sensitive(True)
        else:
            self._listen_row.set_subtitle("Error — evdev no instalado")

    def _on_stop_listener(self, _btn: Gtk.Button) -> None:
        get_listener().stop()
        self._listen_row.set_subtitle("Inactivo")
        self._start_btn.set_sensitive(True)
        self._stop_btn.set_sensitive(False)

    def _on_gesture_received(self, gesture: str, action: str) -> None:
        """Callback desde el thread del listener → actualizar UI en main thread."""
        GLib.idle_add(self._update_last_event, gesture, action)

    def _update_last_event(self, gesture: str, action: str) -> bool:
        label = _KEY_TO_LABEL.get(action, action)
        self._last_event_row.set_title(gesture.replace("_", " ").title())
        self._last_event_row.set_subtitle(f"→ {label}")
        return False

    def _on_action_changed(
        self, dd: Gtk.DropDown, _param: object, gesture_key: str
    ) -> None:
        item = dd.get_selected_item()
        if not item:
            return
        ui_label = item.get_string()
        action_key = _LABEL_TO_KEY.get(ui_label, "── Sin acción ──")
        get_listener().set_mapping(gesture_key, action_key)
