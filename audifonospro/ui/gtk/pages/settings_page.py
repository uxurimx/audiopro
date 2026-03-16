"""
SettingsPage — ajustes generales + opciones avanzadas colapsables.

Secciones siempre visibles:
  - Tema de color
  - Bluetooth: MAC, codec preferido
  - API Keys: OpenAI
  - Acerca de

Secciones avanzadas (toggle para mostrar):
  - ANC manual: controles completos de cancelación de ruido
  - Gestos JBL: mapeo de toques táctiles a acciones
  - Monitor / diagnóstico
"""
from __future__ import annotations

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings


class SettingsPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

        self.set_title("Ajustes")
        self.set_icon_name("preferences-system-symbolic")

        self._build_appearance_group()
        self._build_bt_group()
        self._build_api_group()
        self._build_advanced_toggle()
        self._build_adv_anc_group()
        self._build_adv_controls_group()
        self._build_adv_monitor_group()
        self._build_about_group()

        # Ocultar grupos avanzados por defecto
        for g in self._advanced_groups:
            g.set_visible(False)

    # ── Secciones principales ─────────────────────────────────────────────

    def _build_appearance_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Apariencia")
        self.add(g)

        theme_row = Adw.ActionRow()
        theme_row.set_title("Tema de color")
        themes = ["Sistema (automático)", "Claro", "Oscuro"]
        theme_model = Gtk.StringList.new(themes)
        theme_dd = Gtk.DropDown(model=theme_model)
        theme_dd.set_valign(Gtk.Align.CENTER)
        theme_dd.connect("notify::selected", self._on_theme_changed)
        theme_row.add_suffix(theme_dd)
        theme_row.set_activatable_widget(theme_dd)
        g.add(theme_row)

    def _build_bt_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Bluetooth")
        self.add(g)

        mac_row = Adw.ActionRow()
        mac_row.set_title("Audífono principal")
        mac_row.set_subtitle(self.settings.bluetooth.primary_mac or "No configurado")
        mac_row.set_icon_name("bluetooth-symbolic")
        g.add(mac_row)

        codec_row = Adw.ActionRow()
        codec_row.set_title("Codec preferido")
        codec_row.set_subtitle("Codec de audio Bluetooth de alta calidad")
        codecs = ["AAC", "SBC", "aptX", "LDAC", "LC3"]
        codec_model = Gtk.StringList.new(codecs)
        codec_dd = Gtk.DropDown(model=codec_model)
        codec_dd.set_valign(Gtk.Align.CENTER)
        preferred = getattr(self.settings.bluetooth, "preferred_codec", "AAC")
        if preferred in codecs:
            codec_dd.set_selected(codecs.index(preferred))
        codec_row.add_suffix(codec_dd)
        codec_row.set_activatable_widget(codec_dd)
        g.add(codec_row)

    def _build_api_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("API Keys")
        self.add(g)

        key_row = Adw.ActionRow()
        key_row.set_title("OpenAI API Key")
        key_row.set_subtitle(
            "Necesaria para el modo Equilibrado y Alta calidad.\n"
            "Configura con: export OPENAI_API_KEY=sk-…  o en config.yaml"
        )
        key_row.set_icon_name("dialog-password-symbolic")
        has_key = bool(getattr(self.settings, "openai_api_key", None))
        key_status = Gtk.Label(label="✓ Configurada" if has_key else "No configurada")
        key_status.set_valign(Gtk.Align.CENTER)
        if has_key:
            key_status.add_css_class("success")
        else:
            key_status.add_css_class("dim-label")
        key_row.add_suffix(key_status)
        g.add(key_row)

    def _build_advanced_toggle(self) -> None:
        g = Adw.PreferencesGroup()
        self.add(g)

        toggle_row = Adw.ActionRow()
        toggle_row.set_title("Opciones avanzadas")
        toggle_row.set_subtitle("ANC manual, mapeo de gestos, diagnóstico")
        toggle_row.set_icon_name("applications-engineering-symbolic")
        self._adv_switch = Gtk.Switch()
        self._adv_switch.set_valign(Gtk.Align.CENTER)
        self._adv_switch.connect("state-set", self._on_advanced_toggle)
        toggle_row.add_suffix(self._adv_switch)
        toggle_row.set_activatable_widget(self._adv_switch)
        g.add(toggle_row)

    def _on_advanced_toggle(self, _switch: Gtk.Switch, state: bool) -> bool:
        for g in self._advanced_groups:
            g.set_visible(state)
        return False

    # ── Secciones avanzadas ───────────────────────────────────────────────

    def _build_adv_anc_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Cancelación de ruido (ANC)")
        g.set_description(
            "Crea dispositivos virtuales en PipeWire para filtrar ruido.\n"
            "El Traductor puede activar ANC automáticamente con su toggle."
        )
        self.add(g)
        self._advanced_groups = [g]   # primera entrada de la lista

        # Modo
        mode_row = Adw.ActionRow()
        mode_row.set_title("Modo")
        _modes = ["Micrófono (WebRTC)", "Salida (Filtro bandpass)"]
        mode_model = Gtk.StringList.new(_modes)
        self._anc_mode_dd = Gtk.DropDown(model=mode_model)
        self._anc_mode_dd.set_valign(Gtk.Align.CENTER)
        self._anc_mode_dd.connect("notify::selected", self._on_anc_mode_changed)
        mode_row.add_suffix(self._anc_mode_dd)
        mode_row.set_activatable_widget(self._anc_mode_dd)
        g.add(mode_row)

        # Intensidad (solo para modo Salida)
        self._anc_intensity_row = Adw.ActionRow()
        self._anc_intensity_row.set_title("Intensidad del filtro")
        self._anc_intensity_row.set_visible(False)
        intensity_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        intensity_box.set_valign(Gtk.Align.CENTER)
        self._anc_intensity_lbl = Gtk.Label(label="50%")
        self._anc_intensity_lbl.set_width_chars(4)
        self._anc_intensity_lbl.add_css_class("numeric")
        intensity_box.append(self._anc_intensity_lbl)
        self._anc_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self._anc_scale.set_range(0, 100)
        self._anc_scale.set_value(50)
        self._anc_scale.set_size_request(160, -1)
        self._anc_scale.set_draw_value(False)
        self._anc_scale.connect("value-changed", lambda s: self._anc_intensity_lbl.set_text(f"{int(s.get_value())}%"))
        intensity_box.append(self._anc_scale)
        self._anc_intensity_row.add_suffix(intensity_box)
        g.add(self._anc_intensity_row)

        # Estado + botones
        self._anc_status_row = Adw.ActionRow()
        self._anc_status_row.set_title("Estado")
        self._anc_status_row.set_subtitle("ANC desactivado")
        anc_btn_box = Gtk.Box(spacing=8)
        anc_btn_box.set_valign(Gtk.Align.CENTER)
        self._anc_stop_btn = Gtk.Button(label="Desactivar")
        self._anc_stop_btn.add_css_class("destructive-action")
        self._anc_stop_btn.set_sensitive(False)
        self._anc_stop_btn.connect("clicked", self._on_anc_stop)
        anc_btn_box.append(self._anc_stop_btn)
        self._anc_apply_btn = Gtk.Button(label="Activar ANC")
        self._anc_apply_btn.add_css_class("suggested-action")
        self._anc_apply_btn.connect("clicked", self._on_anc_apply)
        anc_btn_box.append(self._anc_apply_btn)
        self._anc_spinner = Gtk.Spinner()
        anc_btn_box.append(self._anc_spinner)
        self._anc_status_row.add_suffix(anc_btn_box)
        g.add(self._anc_status_row)

    def _build_adv_controls_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Gestos de los audífonos JBL")
        g.set_description("Asigna una acción a cada toque táctil")
        self.add(g)
        self._advanced_groups.append(g)

        try:
            from audifonospro.controls.evdev_listener import DEFAULT_MAPPING, get_listener
            _action_labels = [
                "── Sin acción ──", "Play / Pause", "Siguiente pista", "Pista anterior",
                "Subir volumen", "Bajar volumen", "Ciclar nivel ANC", "Iniciar traductor",
            ]
            _label_to_key = {
                "── Sin acción ──": "── Sin acción ──",
                "Play / Pause": "play_pause",
                "Siguiente pista": "next_track",
                "Pista anterior": "prev_track",
                "Subir volumen": "vol_up",
                "Bajar volumen": "vol_down",
                "Ciclar nivel ANC": "anc_cycle",
                "Iniciar traductor": "translator_start",
            }
            _key_to_label = {v: k for k, v in _label_to_key.items()}
            _gestures = [
                ("Toque simple — izquierdo",  "single_tap_left"),
                ("Toque simple — derecho",    "single_tap_right"),
                ("Doble toque — izquierdo",   "double_tap_left"),
                ("Doble toque — derecho",     "double_tap_right"),
                ("Toque largo — izquierdo",   "long_press_left"),
                ("Toque largo — derecho",     "long_press_right"),
            ]
            actions_model = Gtk.StringList.new(_action_labels)
            for label, gesture_key in _gestures:
                row = Adw.ActionRow()
                row.set_title(label)
                dd = Gtk.DropDown(model=actions_model)
                dd.set_valign(Gtk.Align.CENTER)
                default_action = DEFAULT_MAPPING.get(gesture_key, "── Sin acción ──")
                default_label  = _key_to_label.get(default_action, "── Sin acción ──")
                if default_label in _action_labels:
                    dd.set_selected(_action_labels.index(default_label))
                dd.connect("notify::selected", self._on_gesture_changed,
                           gesture_key, _label_to_key, get_listener)
                row.add_suffix(dd)
                row.set_activatable_widget(dd)
                g.add(row)

            # Listener on/off
            listen_row = Adw.ActionRow()
            listen_row.set_title("Listener de eventos táctiles")
            self._ctrl_status_lbl = Gtk.Label(label="Inactivo")
            self._ctrl_status_lbl.add_css_class("dim-label")
            self._ctrl_status_lbl.set_valign(Gtk.Align.CENTER)
            listen_row.add_suffix(self._ctrl_status_lbl)
            ctrl_box = Gtk.Box(spacing=8)
            ctrl_box.set_valign(Gtk.Align.CENTER)
            self._ctrl_start_btn = Gtk.Button(label="Iniciar")
            self._ctrl_start_btn.add_css_class("suggested-action")
            self._ctrl_start_btn.connect("clicked", self._on_ctrl_start)
            ctrl_box.append(self._ctrl_start_btn)
            self._ctrl_stop_btn = Gtk.Button(label="Detener")
            self._ctrl_stop_btn.add_css_class("destructive-action")
            self._ctrl_stop_btn.set_sensitive(False)
            self._ctrl_stop_btn.connect("clicked", self._on_ctrl_stop)
            ctrl_box.append(self._ctrl_stop_btn)
            listen_row.add_suffix(ctrl_box)
            g.add(listen_row)
            get_listener().set_on_gesture(self._on_gesture_received)
        except ImportError:
            info = Adw.ActionRow()
            info.set_title("Módulo evdev no disponible")
            info.set_subtitle("Instala python-evdev para habilitar gestos")
            g.add(info)

    def _build_adv_monitor_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Monitor y diagnóstico")
        g.set_description("Información técnica en tiempo real (para depuración)")
        self.add(g)
        self._advanced_groups.append(g)

        poll_row = Adw.ActionRow()
        poll_row.set_title("Intervalo de actualización")
        poll_row.set_subtitle("Frecuencia del polling de dispositivos en la pestaña Audio")
        poll_model = Gtk.StringList.new(["250 ms", "500 ms", "1 s", "2 s"])
        poll_dd = Gtk.DropDown(model=poll_model)
        poll_dd.set_valign(Gtk.Align.CENTER)
        poll_dd.set_selected(3)  # 2s por defecto
        poll_row.add_suffix(poll_dd)
        poll_row.set_activatable_widget(poll_dd)
        g.add(poll_row)

        info_row = Adw.ActionRow()
        info_row.set_title("PipeWire / GStreamer")
        import subprocess as _sp
        try:
            pw_ver = _sp.run(
                ["pipewire", "--version"], capture_output=True, text=True, timeout=2
            ).stdout.split("\n")[0].strip()
        except Exception:
            pw_ver = "no disponible"
        info_row.set_subtitle(pw_ver)
        info_row.set_icon_name("dialog-information-symbolic")
        g.add(info_row)

    def _build_about_group(self) -> None:
        g = Adw.PreferencesGroup()
        self.add(g)

        about_row = Adw.ActionRow()
        about_row.set_title("audifonospro")
        about_row.set_subtitle("v0.2.0 · roBit · MIT")
        about_row.set_icon_name("audio-headphones-symbolic")
        about_row.set_activatable(True)
        about_row.connect("activated", self._on_about)
        g.add(about_row)

    # ── Callbacks de apariencia ───────────────────────────────────────────

    def _on_theme_changed(self, dd: Gtk.DropDown, _param: object) -> None:
        idx = dd.get_selected()
        schemes = [
            Adw.ColorScheme.DEFAULT,
            Adw.ColorScheme.FORCE_LIGHT,
            Adw.ColorScheme.FORCE_DARK,
        ]
        Adw.StyleManager.get_default().set_color_scheme(schemes[idx])

    def _on_about(self, _row: Adw.ActionRow) -> None:
        dialog = Adw.AboutDialog()
        dialog.set_application_name("audifonospro")
        dialog.set_version("0.2.0")
        dialog.set_developer_name("roBit")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_comments(
            "Sistema de audio personal multi-dispositivo.\n"
            "Traductor en tiempo real · Cinema Mode · ANC software."
        )
        dialog.present(self)

    # ── Callbacks ANC ─────────────────────────────────────────────────────

    def _on_anc_mode_changed(self, dd: Gtk.DropDown, _param: object) -> None:
        self._anc_intensity_row.set_visible(dd.get_selected() == 1)

    def _on_anc_apply(self, _btn: Gtk.Button) -> None:
        self._anc_apply_btn.set_sensitive(False)
        self._anc_stop_btn.set_sensitive(False)
        self._anc_spinner.start()
        self._anc_status_row.set_subtitle("Iniciando…")
        mode = "mic" if self._anc_mode_dd.get_selected() == 0 else "out"
        intensity = int(self._anc_scale.get_value())
        threading.Thread(
            target=self._anc_apply_thread, args=(mode, intensity), daemon=True
        ).start()

    def _anc_apply_thread(self, mode: str, intensity: int) -> None:
        from audifonospro.anc.pipewire_anc import get_anc
        ok, msg = get_anc().apply(mode, intensity)
        GLib.idle_add(self._anc_apply_done, ok, msg, mode)

    def _anc_apply_done(self, ok: bool, msg: str, mode: str) -> bool:
        self._anc_spinner.stop()
        self._anc_apply_btn.set_sensitive(True)
        self._anc_stop_btn.set_sensitive(ok)
        if ok:
            if mode == "mic":
                self._anc_status_row.set_subtitle(
                    "Activo — selecciona «audifonospro ANC Mic» en el Traductor"
                )
            else:
                self._anc_status_row.set_subtitle("Activo — sink «audifonospro Filtro de Ruido»")
            self._anc_status_row.remove_css_class("error")
        else:
            self._anc_status_row.set_subtitle(f"Error: {msg[:120]}")
            self._anc_status_row.add_css_class("error")
        return False

    def _on_anc_stop(self, _btn: Gtk.Button) -> None:
        from audifonospro.anc.pipewire_anc import get_anc
        get_anc().stop()
        self._anc_status_row.set_subtitle("ANC desactivado")
        self._anc_status_row.remove_css_class("error")
        self._anc_stop_btn.set_sensitive(False)

    # ── Callbacks Controles ───────────────────────────────────────────────

    def _on_gesture_changed(
        self, dd: Gtk.DropDown, _param: object,
        gesture_key: str, label_to_key: dict, get_listener_fn: object,
    ) -> None:
        item = dd.get_selected_item()
        if item:
            action_key = label_to_key.get(item.get_string(), "── Sin acción ──")
            get_listener_fn().set_mapping(gesture_key, action_key)

    def _on_ctrl_start(self, _btn: Gtk.Button) -> None:
        try:
            from audifonospro.controls.evdev_listener import EvdevListener, get_listener
            path = EvdevListener.find_jbl_device()
            if path:
                ok = get_listener().start(path)
                if ok:
                    self._ctrl_status_lbl.set_text(f"Escuchando {path}")
                    self._ctrl_start_btn.set_sensitive(False)
                    self._ctrl_stop_btn.set_sensitive(True)
                    return
            self._ctrl_status_lbl.set_text("JBL no encontrado — ¿conectado en HFP?")
        except Exception as exc:
            self._ctrl_status_lbl.set_text(str(exc)[:60])

    def _on_ctrl_stop(self, _btn: Gtk.Button) -> None:
        try:
            from audifonospro.controls.evdev_listener import get_listener
            get_listener().stop()
        except Exception:
            pass
        self._ctrl_status_lbl.set_text("Inactivo")
        self._ctrl_start_btn.set_sensitive(True)
        self._ctrl_stop_btn.set_sensitive(False)

    def _on_gesture_received(self, gesture: str, action: str) -> None:
        pass   # callback del listener — no necesitamos mostrar nada aquí
