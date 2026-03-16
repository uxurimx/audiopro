"""
SettingsPage — ajustes generales del sistema.

Usa Adw.PreferencesPage con grupos:
  - Audio: dispositivos de entrada/salida, sample rate, buffer
  - Bluetooth: timeout, codec preferido
  - ANC: nivel por defecto, umbral de sensibilidad
  - STT/TTS: API keys, providers
  - UI: color scheme, polling interval
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from audifonospro.config import Settings


class SettingsPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

        self.set_title("Ajustes")
        self.set_icon_name("preferences-system-symbolic")

        self._build_audio_group()
        self._build_bt_group()
        self._build_anc_group()
        self._build_stt_group()
        self._build_ui_group()
        self._build_about_group()

    # ── Grupos ────────────────────────────────────────────────────────────

    def _build_audio_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Audio")
        self.add(g)

        # Dispositivo de entrada
        in_row = Adw.ActionRow()
        in_row.set_title("Dispositivo de entrada")
        in_row.set_subtitle(self.settings.audio.input_device or "auto (sistema)")
        in_row.set_icon_name("audio-input-microphone-symbolic")
        g.add(in_row)

        # Dispositivo de salida
        out_row = Adw.ActionRow()
        out_row.set_title("Dispositivo de salida")
        out_row.set_subtitle(self.settings.audio.output_device or "auto (sistema)")
        out_row.set_icon_name("audio-speakers-symbolic")
        g.add(out_row)

        # Sample rate
        rate_row = Adw.ActionRow()
        rate_row.set_title("Frecuencia de muestreo")
        rate_row.set_subtitle(f"{self.settings.audio.sample_rate} Hz")
        g.add(rate_row)

        # Buffer
        buf_row = Adw.ActionRow()
        buf_row.set_title("Tamaño de buffer")
        buf_row.set_subtitle(f"{self.settings.audio.buffer_ms} ms")
        g.add(buf_row)

    def _build_bt_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Bluetooth")
        self.add(g)

        mac_row = Adw.ActionRow()
        mac_row.set_title("MAC del audífono principal")
        mac_row.set_subtitle(
            self.settings.bluetooth.primary_mac or "No configurado"
        )
        mac_row.set_icon_name("bluetooth-symbolic")
        g.add(mac_row)

        codec_row = Adw.ActionRow()
        codec_row.set_title("Codec preferido")
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

    def _build_anc_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Cancelación de ruido (ANC)")
        self.add(g)

        level_row = Adw.ActionRow()
        level_row.set_title("Nivel ANC por defecto")
        levels = ["Off", "Espectral", "RNNoise", "LMS adaptativo", "GATT hardware", "Híbrido"]
        lvl_model = Gtk.StringList.new(levels)
        lvl_dd = Gtk.DropDown(model=lvl_model)
        lvl_dd.set_valign(Gtk.Align.CENTER)
        default_lvl = getattr(self.settings.anc, "default_level", 1)
        lvl_dd.set_selected(min(default_lvl, len(levels) - 1))
        level_row.add_suffix(lvl_dd)
        level_row.set_activatable_widget(lvl_dd)
        g.add(level_row)

        info_row = Adw.ActionRow()
        info_row.set_title("ANC hardware")
        info_row.set_subtitle("Control vía BLE GATT — disponible en Fase 3")
        info_row.set_icon_name("dialog-information-symbolic")
        g.add(info_row)

    def _build_stt_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("STT / Traducción / TTS")
        self.add(g)

        stt_row = Adw.ActionRow()
        stt_row.set_title("Proveedor STT")
        stt_providers = ["whisper_cpp (local)", "openai_api"]
        stt_model = Gtk.StringList.new(stt_providers)
        stt_dd = Gtk.DropDown(model=stt_model)
        stt_dd.set_valign(Gtk.Align.CENTER)
        provider = getattr(self.settings.stt, "provider", "whisper_cpp")
        if provider in stt_providers:
            stt_dd.set_selected(stt_providers.index(provider))
        stt_row.add_suffix(stt_dd)
        stt_row.set_activatable_widget(stt_dd)
        g.add(stt_row)

        key_row = Adw.ActionRow()
        key_row.set_title("OpenAI API Key")
        key_row.set_subtitle("Configurar vía variable OPENAI_API_KEY o config.yaml")
        key_row.set_icon_name("dialog-password-symbolic")
        g.add(key_row)

    def _build_ui_group(self) -> None:
        g = Adw.PreferencesGroup()
        g.set_title("Interfaz")
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

        poll_row = Adw.ActionRow()
        poll_row.set_title("Intervalo de polling")
        poll_row.set_subtitle("Cada cuánto se actualizan las métricas del monitor")
        poll_model = Gtk.StringList.new(["250 ms", "500 ms", "1 s", "2 s"])
        poll_dd = Gtk.DropDown(model=poll_model)
        poll_dd.set_valign(Gtk.Align.CENTER)
        poll_dd.set_selected(1)  # 500 ms por defecto
        poll_row.add_suffix(poll_dd)
        poll_row.set_activatable_widget(poll_dd)
        g.add(poll_row)

    def _build_about_group(self) -> None:
        g = Adw.PreferencesGroup()
        self.add(g)

        about_row = Adw.ActionRow()
        about_row.set_title("audifonospro")
        about_row.set_subtitle("Sistema de audio personal multi-dispositivo · roBit")
        about_row.set_icon_name("audio-headphones-symbolic")
        about_row.set_activatable(True)
        about_row.connect("activated", self._on_about)
        g.add(about_row)

    # ── Callbacks ────────────────────────────────────────────────────────

    def _on_theme_changed(self, dd: Gtk.DropDown, _param: object) -> None:
        from gi.repository import Adw as Adw2
        idx = dd.get_selected()
        schemes = [
            Adw2.ColorScheme.DEFAULT,
            Adw2.ColorScheme.FORCE_LIGHT,
            Adw2.ColorScheme.FORCE_DARK,
        ]
        Adw2.StyleManager.get_default().set_color_scheme(schemes[idx])

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
