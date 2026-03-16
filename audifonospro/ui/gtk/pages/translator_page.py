"""
TranslatorPage — traductor de voz en tiempo real.

Flujo:
  Micrófono (HFP) → Whisper STT → OpenAI/Ollama translate → edge-tts → audífono

Estado actual: UI completa, pipeline conectado en Fase 4.
"""
from __future__ import annotations

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings

_LANGS = [
    "Español", "English", "Français", "Deutsch",
    "Italiano", "Português", "日本語", "中文", "한국어",
]

_PROVIDERS = ["OpenAI GPT-4o-mini", "Ollama llama3:8b", "Ollama gemma2:2b"]


class TranslatorPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._pipeline_running = False

        self.set_title("Traductor")
        self.set_icon_name("microphone-sensitivity-high-symbolic")

        # ── Configuración ─────────────────────────────────────────────────
        config_group = Adw.PreferencesGroup()
        config_group.set_title("Configuración")
        self.add(config_group)

        # Idioma origen
        src_row = Adw.ActionRow()
        src_row.set_title("Idioma origen")
        src_model = Gtk.StringList.new(_LANGS)
        self._src_dd = Gtk.DropDown(model=src_model)
        self._src_dd.set_valign(Gtk.Align.CENTER)
        self._src_dd.set_selected(1)  # English por defecto
        src_row.add_suffix(self._src_dd)
        src_row.set_activatable_widget(self._src_dd)
        config_group.add(src_row)

        # Idioma destino
        dst_row = Adw.ActionRow()
        dst_row.set_title("Idioma destino")
        dst_model = Gtk.StringList.new(_LANGS)
        self._dst_dd = Gtk.DropDown(model=dst_model)
        self._dst_dd.set_valign(Gtk.Align.CENTER)
        self._dst_dd.set_selected(0)  # Español por defecto
        dst_row.add_suffix(self._dst_dd)
        dst_row.set_activatable_widget(self._dst_dd)
        config_group.add(dst_row)

        # Proveedor de traducción
        prov_row = Adw.ActionRow()
        prov_row.set_title("Motor de traducción")
        prov_model = Gtk.StringList.new(_PROVIDERS)
        self._prov_dd = Gtk.DropDown(model=prov_model)
        self._prov_dd.set_valign(Gtk.Align.CENTER)
        prov_row.add_suffix(self._prov_dd)
        prov_row.set_activatable_widget(self._prov_dd)
        config_group.add(prov_row)

        # ── Control del pipeline ──────────────────────────────────────────
        pipeline_group = Adw.PreferencesGroup()
        pipeline_group.set_title("Pipeline")
        self.add(pipeline_group)

        ctrl_row = Adw.ActionRow()
        ctrl_row.set_title("Traductor en tiempo real")
        ctrl_row.set_subtitle("Inicia la captura y traducción continua")

        self._toggle_btn = Gtk.Button(label="▶  Iniciar")
        self._toggle_btn.set_valign(Gtk.Align.CENTER)
        self._toggle_btn.add_css_class("suggested-action")
        self._toggle_btn.connect("clicked", self._on_toggle_pipeline)
        ctrl_row.add_suffix(self._toggle_btn)
        pipeline_group.add(ctrl_row)

        # ── Estado en tiempo real ─────────────────────────────────────────
        status_group = Adw.PreferencesGroup()
        status_group.set_title("Estado")
        self.add(status_group)

        self._stt_row = self._status_row("STT (Whisper)", "Inactivo")
        self._trans_row = self._status_row("Traducción", "Inactivo")
        self._tts_row = self._status_row("TTS (edge-tts)", "Inactivo")
        self._latency_row = self._status_row("Latencia total", "─")
        for row in [self._stt_row, self._trans_row, self._tts_row, self._latency_row]:
            status_group.add(row)

        # ── Transcripción ─────────────────────────────────────────────────
        text_group = Adw.PreferencesGroup()
        text_group.set_title("Última transcripción")
        self.add(text_group)

        text_row = Adw.ActionRow()
        self._transcript_label = Gtk.Label(label="─")
        self._transcript_label.set_wrap(True)
        self._transcript_label.set_xalign(0)
        self._transcript_label.add_css_class("dim-label")
        self._transcript_label.set_margin_top(8)
        self._transcript_label.set_margin_bottom(8)
        text_row.set_child(self._transcript_label)
        text_group.add(text_row)

        # ── Nota de costo ─────────────────────────────────────────────────
        cost_group = Adw.PreferencesGroup()
        self.add(cost_group)
        cost_row = Adw.ActionRow()
        cost_row.set_title("Costo estimado")
        cost_row.set_subtitle(
            "GPT-4o-mini: ~$0.004/sesión · Whisper.cpp: gratis · edge-tts: gratis"
        )
        cost_row.set_icon_name("dialog-information-symbolic")
        cost_group.add(cost_row)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _status_row(title: str, subtitle: str) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        return row

    # ── Pipeline ──────────────────────────────────────────────────────────

    def _on_toggle_pipeline(self, _btn: Gtk.Button) -> None:
        if self._pipeline_running:
            self._stop_pipeline()
        else:
            self._start_pipeline()

    def _start_pipeline(self) -> None:
        self._pipeline_running = True
        self._toggle_btn.set_label("⏹  Detener")
        self._toggle_btn.remove_css_class("suggested-action")
        self._toggle_btn.add_css_class("destructive-action")
        self._stt_row.set_subtitle("Esperando voz…")
        self._trans_row.set_subtitle("En espera")
        self._tts_row.set_subtitle("En espera")
        # TODO Fase 4: iniciar pipeline real
        # t = threading.Thread(target=self._run_pipeline, daemon=True)
        # t.start()

    def _stop_pipeline(self) -> None:
        self._pipeline_running = False
        self._toggle_btn.set_label("▶  Iniciar")
        self._toggle_btn.remove_css_class("destructive-action")
        self._toggle_btn.add_css_class("suggested-action")
        self._stt_row.set_subtitle("Inactivo")
        self._trans_row.set_subtitle("Inactivo")
        self._tts_row.set_subtitle("Inactivo")
        self._latency_row.set_subtitle("─")
