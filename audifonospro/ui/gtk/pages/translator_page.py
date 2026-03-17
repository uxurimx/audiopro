"""
TranslatorPage — traductor de voz en tiempo real.

Flujo simplificado:
  1. Elige micrófono (Auto / Laptop / JBL HFP)
  2. Activa opciones: HFP automático, ANC
  3. Elige calidad (Offline / Equilibrado / Alta calidad)
  4. Elige idiomas
  5. Presiona Iniciar — habla — escucha la traducción en voz

El pipeline maneja automáticamente:
  - Cambio de perfil BT a HFP (si el toggle está activo)
  - Activación de ANC WebRTC (si el checkbox está activo)
  - Restauración de A2DP al detener
"""
from __future__ import annotations

import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings


def _list_output_sinks() -> list[tuple[str, str | None]]:
    """Devuelve [(label, sink_name|None), ...] para dispositivos de salida de audio."""
    sinks: list[tuple[str, str | None]] = [("Auto (predeterminado del sistema)", None)]
    try:
        from audifonospro.audio.routing import list_sinks
        for s in list_sinks():
            name = s["name"]
            desc = (s.get("description") or "").strip() or name
            if "bluez_output" in name:
                label = f"BT — {desc}"
            elif "alsa_output" in name and "analog" in name:
                label = f"Altavoces — {desc}"
            elif "hdmi" in name.lower():
                label = f"HDMI — {desc}"
            else:
                label = desc[:52]
            sinks.append((label, name))
    except Exception:
        pass
    return sinks


def _list_mic_sources() -> list[tuple[str, str | None]]:
    """Devuelve [(label, pa_source_name|None), ...] incluyendo monitores de sistema."""
    sources: list[tuple[str, str | None]] = [("Auto (predeterminado del sistema)", None)]
    try:
        out = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=3,
        ).stdout
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[1].strip()
            if not name:
                continue
            if "monitor" in name:
                # Fuentes monitor: capturan el audio que sale por los altavoces/BT
                if "bluez_output" in name:
                    label = "Sistema — audio de audífonos BT (video/navegador)"
                else:
                    label = "Sistema — audio del ordenador (video/navegador)"
                sources.append((label, name))
            elif "alsa_input" in name:
                sources.append(("Laptop — mic integrado", name))
            elif "bluez_input" in name:
                sources.append(("JBL HFP — mic de audífonos", name))
            elif "easyeffects_source" in name:
                sources.append(("EasyEffects (procesado)", name))
            elif "audifonospro_anc" in name:
                sources.append(("ANC Mic (audifonospro)", name))
            else:
                sources.append((name[:50], name))
    except Exception:
        pass
    return sources


_LANGS_SRC = [
    "Auto (detectar)",
    "Español", "English", "Français", "Deutsch",
    "Italiano", "Português", "日本語", "中文", "한국어",
]
_LANGS_DST = [
    "Español", "English", "Français", "Deutsch",
    "Italiano", "Português", "日本語", "中文", "한국어",
]
# Mapa de nombre de idioma → código para Whisper/LLM
_LANG_CODE = {
    "Auto (detectar)": "",
    "Español": "es", "English": "en", "Français": "fr", "Deutsch": "de",
    "Italiano": "it", "Português": "pt", "日本語": "ja", "中文": "zh", "한국어": "ko",
}

# (label, stt_provider, trans_provider, trans_model, tts_provider)
_QUALITY_OPTIONS = [
    ("Local — gratis, ~10s/frase",         "whisper_cpp", "ollama",  "llama3.2:3b",  "piper"),
    ("Equilibrado — ~$0.004/sesión, ~2s",  "whisper_cpp", "openai",  "gpt-4o-mini",  "edge_tts"),
    ("Alta calidad — ~$0.08/sesión, ~1s",  "openai",      "openai",  "gpt-4o",       "openai"),
]


class TranslatorPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._pipeline_running = False
        self._mic_timer_id = 0
        self._anc_started_here = False
        self._mic_sources = _list_mic_sources()
        self._out_sinks = _list_output_sinks()
        self._subtitle_win: object | None = None   # SubtitleWindow, lazy-imported

        self.set_title("Traductor")
        self.set_icon_name("microphone-symbolic")

        # ── Configuración ─────────────────────────────────────────────────
        config_group = Adw.PreferencesGroup()
        config_group.set_title("Configuración")
        self.add(config_group)

        # Micrófono + botón de actualizar
        mic_src_row = Adw.ActionRow()
        mic_src_row.set_title("Micrófono")
        mic_src_row.set_subtitle("Fuente de voz para la captura")
        mic_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mic_box.set_valign(Gtk.Align.CENTER)
        mic_labels = [label for label, _ in self._mic_sources]
        mic_model = Gtk.StringList.new(mic_labels)
        self._mic_dd = Gtk.DropDown(model=mic_model)
        self._mic_dd.set_valign(Gtk.Align.CENTER)
        mic_box.append(self._mic_dd)
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Actualizar lista de micrófonos")
        refresh_btn.set_valign(Gtk.Align.CENTER)
        refresh_btn.connect("clicked", self._on_refresh_mics)
        mic_box.append(refresh_btn)
        mic_src_row.add_suffix(mic_box)
        mic_src_row.set_activatable_widget(self._mic_dd)
        config_group.add(mic_src_row)

        # Salida de audio (dispositivo donde se reproduce la traducción)
        out_row = Adw.ActionRow()
        out_row.set_title("Salida de audio")
        out_row.set_subtitle("Dispositivo donde suena la voz traducida")
        out_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        out_box.set_valign(Gtk.Align.CENTER)
        out_labels = [label for label, _ in self._out_sinks]
        out_model = Gtk.StringList.new(out_labels)
        self._out_dd = Gtk.DropDown(model=out_model)
        self._out_dd.set_valign(Gtk.Align.CENTER)
        out_box.append(self._out_dd)
        out_refresh_btn = Gtk.Button()
        out_refresh_btn.set_icon_name("view-refresh-symbolic")
        out_refresh_btn.set_tooltip_text("Actualizar lista de salidas")
        out_refresh_btn.set_valign(Gtk.Align.CENTER)
        out_refresh_btn.connect("clicked", self._on_refresh_outputs)
        out_box.append(out_refresh_btn)
        out_row.add_suffix(out_box)
        out_row.set_activatable_widget(self._out_dd)
        config_group.add(out_row)

        # HFP automático (solo visible si hay MAC configurada)
        if self.settings.bluetooth.primary_mac:
            hfp_row = Adw.ActionRow()
            hfp_row.set_title("Activar mic BT al iniciar")
            hfp_row.set_subtitle(
                "Cambia los JBL a modo Manos Libres para usar su micrófono. "
                "Vuelve a A2DP al detener (reduce calidad mientras está activo)."
            )
            self._hfp_switch = Gtk.Switch()
            self._hfp_switch.set_valign(Gtk.Align.CENTER)
            self._hfp_switch.set_active(False)
            hfp_row.add_suffix(self._hfp_switch)
            hfp_row.set_activatable_widget(self._hfp_switch)
            config_group.add(hfp_row)
        else:
            self._hfp_switch = None

        # ANC (reducción de ruido)
        anc_row = Adw.ActionRow()
        anc_row.set_title("Reducir ruido de fondo (ANC)")
        anc_row.set_subtitle(
            "Activa la cancelación de ruido WebRTC en el micrófono antes de transcribir."
        )
        self._anc_switch = Gtk.Switch()
        self._anc_switch.set_valign(Gtk.Align.CENTER)
        anc_row.add_suffix(self._anc_switch)
        anc_row.set_activatable_widget(self._anc_switch)
        config_group.add(anc_row)

        # Calidad (reemplaza "Motor de traducción")
        quality_row = Adw.ActionRow()
        quality_row.set_title("Calidad")
        quality_model = Gtk.StringList.new([q[0] for q in _QUALITY_OPTIONS])
        self._quality_dd = Gtk.DropDown(model=quality_model)
        self._quality_dd.set_valign(Gtk.Align.CENTER)
        self._quality_dd.set_selected(1)   # Equilibrado por defecto
        quality_row.add_suffix(self._quality_dd)
        quality_row.set_activatable_widget(self._quality_dd)
        config_group.add(quality_row)

        # Traducir (on/off)
        translate_row = Adw.ActionRow()
        translate_row.set_title("Traducir")
        translate_row.set_subtitle("ON: traduce al idioma destino  •  OFF: solo transcribe (más rápido)")
        self._translate_switch = Gtk.Switch()
        self._translate_switch.set_valign(Gtk.Align.CENTER)
        self._translate_switch.set_active(True)
        translate_row.add_suffix(self._translate_switch)
        translate_row.set_activatable_widget(self._translate_switch)
        config_group.add(translate_row)

        # Solo texto (sin TTS)
        text_only_row = Adw.ActionRow()
        text_only_row.set_title("Solo texto — sin voz")
        text_only_row.set_subtitle("Muestra el resultado en pantalla sin sintetizar audio")
        self._text_only_switch = Gtk.Switch()
        self._text_only_switch.set_valign(Gtk.Align.CENTER)
        self._text_only_switch.set_active(False)
        text_only_row.add_suffix(self._text_only_switch)
        text_only_row.set_activatable_widget(self._text_only_switch)
        config_group.add(text_only_row)

        # Idiomas
        src_row = Adw.ActionRow()
        src_row.set_title("Idioma de origen")
        src_row.set_subtitle("Lo que tú hablas")
        src_model = Gtk.StringList.new(_LANGS_SRC)
        self._src_dd = Gtk.DropDown(model=src_model)
        self._src_dd.set_valign(Gtk.Align.CENTER)
        self._src_dd.set_selected(0)  # Auto (detectar)
        src_row.add_suffix(self._src_dd)
        src_row.set_activatable_widget(self._src_dd)
        config_group.add(src_row)

        dst_row = Adw.ActionRow()
        dst_row.set_title("Idioma de destino")
        dst_row.set_subtitle("La traducción que se escucha por los audífonos")
        dst_model = Gtk.StringList.new(_LANGS_DST)
        self._dst_dd = Gtk.DropDown(model=dst_model)
        self._dst_dd.set_valign(Gtk.Align.CENTER)
        self._dst_dd.set_selected(1)  # English
        dst_row.add_suffix(self._dst_dd)
        dst_row.set_activatable_widget(self._dst_dd)
        config_group.add(dst_row)

        # ── Control ───────────────────────────────────────────────────────
        pipeline_group = Adw.PreferencesGroup()
        pipeline_group.set_title("Control")
        self.add(pipeline_group)

        ctrl_row = Adw.ActionRow()
        ctrl_row.set_title("Traductor en tiempo real")
        ctrl_row.set_subtitle("Habla en el idioma de origen cuando esté activo")
        self._toggle_btn = Gtk.Button(label="▶  Iniciar")
        self._toggle_btn.set_valign(Gtk.Align.CENTER)
        self._toggle_btn.add_css_class("suggested-action")
        self._toggle_btn.connect("clicked", self._on_toggle_pipeline)
        ctrl_row.add_suffix(self._toggle_btn)
        pipeline_group.add(ctrl_row)

        # Ventana de subtítulos flotante
        sub_row = Adw.ActionRow()
        sub_row.set_title("Ventana de subtítulos")
        sub_row.set_subtitle("Texto grande flotante — ideal para ver un video")
        self._sub_btn = Gtk.Button(label="Abrir subtítulos")
        self._sub_btn.set_valign(Gtk.Align.CENTER)
        self._sub_btn.connect("clicked", self._on_toggle_subtitle_window)
        sub_row.add_suffix(self._sub_btn)
        sub_row.set_activatable_widget(self._sub_btn)
        pipeline_group.add(sub_row)

        # Nivel de micrófono en vivo
        self._mic_row = Adw.ActionRow()
        self._mic_row.set_title("Nivel del micrófono")
        self._mic_row.set_subtitle("─")
        self._mic_row.set_visible(False)
        self._mic_bar = Gtk.LevelBar()
        self._mic_bar.set_min_value(0)
        self._mic_bar.set_max_value(1)
        self._mic_bar.set_value(0)
        self._mic_bar.set_valign(Gtk.Align.CENTER)
        self._mic_bar.set_size_request(120, -1)
        self._mic_row.add_suffix(self._mic_bar)
        pipeline_group.add(self._mic_row)

        # ── Estado ────────────────────────────────────────────────────────
        status_group = Adw.PreferencesGroup()
        status_group.set_title("Estado del pipeline")
        self.add(status_group)

        self._stt_row     = self._make_status_row("Transcripción (Whisper)", "Inactivo")
        self._trans_row   = self._make_status_row("Traducción", "Inactivo")
        self._tts_row     = self._make_status_row("Síntesis de voz", "Inactivo")
        self._latency_row = self._make_status_row("Latencia total", "─")
        for row in [self._stt_row, self._trans_row, self._tts_row, self._latency_row]:
            status_group.add(row)

        # ── Historial de transcripciones ──────────────────────────────────
        text_group = Adw.PreferencesGroup()
        text_group.set_title("Historial de transcripciones")
        self.add(text_group)

        history_row = Adw.ActionRow()
        self._transcript_buf = Gtk.TextBuffer()
        self._transcript_buf.set_text("Aquí aparecerá el historial de traducciones…\n")
        self._transcript_view = Gtk.TextView(buffer=self._transcript_buf)
        self._transcript_view.set_editable(False)
        self._transcript_view.set_cursor_visible(False)
        self._transcript_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._transcript_view.set_monospace(False)
        self._transcript_view.set_margin_top(8)
        self._transcript_view.set_margin_bottom(8)
        self._transcript_view.set_margin_start(8)
        self._transcript_view.set_margin_end(8)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self._transcript_view)
        scroll.set_size_request(-1, 240)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(False)
        history_row.set_child(scroll)

        text_group.add(history_row)

        # Botonera: limpiar + cargar historial DB
        btn_row = Adw.ActionRow()
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_margin_top(6)
        btn_box.set_margin_bottom(6)

        clear_btn = Gtk.Button(label="Limpiar")
        clear_btn.set_icon_name("edit-clear-all-symbolic")
        clear_btn.set_tooltip_text("Limpiar historial de esta sesión")
        clear_btn.connect("clicked", self._on_clear_history)
        btn_box.append(clear_btn)

        load_btn = Gtk.Button(label="Cargar historial")
        load_btn.set_icon_name("document-open-recent-symbolic")
        load_btn.set_tooltip_text("Cargar las últimas 50 frases de la base de datos")
        load_btn.connect("clicked", self._on_load_history)
        btn_box.append(load_btn)

        btn_row.set_child(btn_box)
        text_group.add(btn_row)

        self._scroll_window = scroll

    # ── Helpers ───────────────────────────────────────────────────────────

    def _on_refresh_outputs(self, _btn: Gtk.Button | None = None) -> None:
        old_idx = self._out_dd.get_selected()
        old_name = self._out_sinks[old_idx][1] if old_idx < len(self._out_sinks) else None
        self._out_sinks = _list_output_sinks()
        self._out_dd.set_model(Gtk.StringList.new([label for label, _ in self._out_sinks]))
        if old_name:
            for i, (_, name) in enumerate(self._out_sinks):
                if name == old_name:
                    self._out_dd.set_selected(i)
                    return
        self._out_dd.set_selected(0)

    def _on_toggle_subtitle_window(self, _btn: Gtk.Button) -> None:
        if self._subtitle_win is not None and self._subtitle_win.get_visible():
            self._subtitle_win.set_visible(False)
            self._sub_btn.set_label("Abrir subtítulos")
            return

        if self._subtitle_win is None:
            from audifonospro.ui.gtk.subtitle_window import SubtitleWindow
            try:
                app = self.get_root().get_application()
            except Exception:
                app = None
            self._subtitle_win = SubtitleWindow(application=app)
            self._subtitle_win.connect("close-request", self._on_subtitle_closed)

        self._subtitle_win.present()
        self._sub_btn.set_label("Cerrar subtítulos")
        if self._pipeline_running:
            self._subtitle_win.set_pipeline_active(True)

    def _on_subtitle_closed(self, _win: object) -> bool:
        self._sub_btn.set_label("Abrir subtítulos")
        return False   # allow close

    def _on_refresh_mics(self, _btn: Gtk.Button | None = None) -> None:
        # Recordar la fuente actualmente seleccionada por nombre (no índice)
        old_idx = self._mic_dd.get_selected()
        old_name = (
            self._mic_sources[old_idx][1]
            if old_idx < len(self._mic_sources)
            else None
        )
        self._mic_sources = _list_mic_sources()
        new_model = Gtk.StringList.new([label for label, _ in self._mic_sources])
        self._mic_dd.set_model(new_model)
        # Restaurar selección por nombre; si no se encuentra, queda en Auto (0)
        if old_name:
            for i, (_, name) in enumerate(self._mic_sources):
                if name == old_name:
                    self._mic_dd.set_selected(i)
                    return
        self._mic_dd.set_selected(0)

    @staticmethod
    def _make_status_row(title: str, subtitle: str) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        return row

    def _get_config(self) -> dict:
        src_name = _LANGS_SRC[self._src_dd.get_selected()]
        dst_name = _LANGS_DST[self._dst_dd.get_selected()]
        src_lang = _LANG_CODE.get(src_name, "")   # "" = auto-detect en Whisper
        qi = self._quality_dd.get_selected()
        _, stt_p, trans_p, trans_m, tts_p = _QUALITY_OPTIONS[qi]
        # "Solo texto" override: no sintetizar voz
        if self._text_only_switch.get_active():
            tts_p = "none"
        mic_idx = self._mic_dd.get_selected()
        _, mic_source = self._mic_sources[mic_idx] if mic_idx < len(self._mic_sources) else (None, None)
        out_idx = self._out_dd.get_selected()
        _, out_sink = self._out_sinks[out_idx] if out_idx < len(self._out_sinks) else (None, None)
        translate = self._translate_switch.get_active()
        return {
            "src_lang":       src_lang,
            "dst_lang":       dst_name,   # nombre completo para el prompt de traducción
            "stt_provider":   stt_p,
            "trans_provider": trans_p,
            "trans_model":    trans_m,
            "tts_provider":   tts_p,
            "mic_source":     mic_source,
            "output_device":  out_sink,
            "translate":      translate,
        }

    # ── Nivel de mic ──────────────────────────────────────────────────────

    def _start_mic_monitor(self) -> None:
        self._mic_row.set_visible(True)
        if self._mic_timer_id:
            GLib.source_remove(self._mic_timer_id)
        self._mic_timer_id = GLib.timeout_add(100, self._update_mic_level)

    def _stop_mic_monitor(self) -> None:
        if self._mic_timer_id:
            GLib.source_remove(self._mic_timer_id)
            self._mic_timer_id = 0
        self._mic_row.set_visible(False)
        self._mic_bar.set_value(0)

    def _update_mic_level(self) -> bool:
        try:
            from audifonospro.pipeline.coordinator import get_pipeline
            pipe = get_pipeline()
            if not pipe.is_running:
                return False
            level = getattr(pipe, "_last_rms", 0.0)
            normalized = min(1.0, level / 8000.0)
            self._mic_bar.set_value(normalized)
            if normalized < 0.02:
                hint = "Silencio"
            elif normalized < 0.15:
                hint = "Bajo"
            else:
                hint = "Hablando"
            self._mic_row.set_subtitle(hint)
        except Exception:
            pass
        return True

    # ── Pipeline ──────────────────────────────────────────────────────────

    def _on_toggle_pipeline(self, _btn: Gtk.Button) -> None:
        if self._pipeline_running:
            self._stop_pipeline()
        else:
            self._start_pipeline()

    def _start_pipeline(self) -> None:
        self._pipeline_running = True
        self._anc_started_here = False
        self._toggle_btn.set_label("⏹  Detener")
        self._toggle_btn.remove_css_class("suggested-action")
        self._toggle_btn.add_css_class("destructive-action")
        self._stt_row.set_subtitle("Preparando…")
        self._trans_row.set_subtitle("En espera")
        self._tts_row.set_subtitle("En espera")
        self._latency_row.set_subtitle("─")

        threading.Thread(target=self._setup_and_launch, daemon=True).start()

    def _setup_and_launch(self) -> None:
        """Prepara HFP/ANC en background y luego lanza el pipeline."""
        import time

        # 1. Activar HFP si el toggle está encendido
        if self._hfp_switch and self._hfp_switch.get_active():
            mac = self.settings.bluetooth.primary_mac
            if mac:
                GLib.idle_add(self._stt_row.set_subtitle, "Activando mic BT…")
                try:
                    from audifonospro.audio.bluetooth import set_profile
                    set_profile(mac, "headset-head-unit")
                    time.sleep(1.5)   # esperar cambio de perfil BT
                    GLib.idle_add(self._on_refresh_mics)
                    time.sleep(0.3)   # esperar refresh
                except Exception as exc:
                    GLib.idle_add(self._stt_row.set_subtitle, f"HFP: {exc!s:.40}")

        # 2. Activar ANC si el switch está encendido
        if self._anc_switch.get_active():
            GLib.idle_add(self._stt_row.set_subtitle, "Iniciando ANC…")
            try:
                from audifonospro.anc.pipewire_anc import get_anc
                ok, _ = get_anc().apply("mic", 50)
                if ok:
                    self._anc_started_here = True
                    time.sleep(0.7)   # esperar que aparezca el nodo PipeWire
                    GLib.idle_add(self._on_refresh_mics)
                    time.sleep(0.2)
            except Exception:
                pass

        GLib.idle_add(self._launch_pipeline)

    def _launch_pipeline(self) -> bool:
        from audifonospro.pipeline.coordinator import get_pipeline

        cfg = self._get_config()

        # Auto-seleccionar mic JBL si HFP fue activado
        if self._hfp_switch and self._hfp_switch.get_active():
            for _, name in self._mic_sources:
                if name and "bluez_input" in name:
                    cfg["mic_source"] = name
                    break

        # Auto-seleccionar mic ANC si fue activado
        if self._anc_started_here:
            for _, name in self._mic_sources:
                if name and "audifonospro_anc" in name:
                    cfg["mic_source"] = name
                    break

        pipe = get_pipeline()
        pipe.on_status     = self._on_pipeline_status
        pipe.on_transcript = self._on_transcript
        pipe.start(
            src_lang       = cfg["src_lang"],
            dst_lang       = cfg["dst_lang"],
            stt_provider   = cfg["stt_provider"],
            trans_provider = cfg["trans_provider"],
            trans_model    = cfg["trans_model"],
            tts_provider   = cfg["tts_provider"],
            mic_source     = cfg["mic_source"],
            output_device  = cfg["output_device"],
            translate      = cfg["translate"],
        )
        self._start_mic_monitor()
        if self._subtitle_win is not None:
            self._subtitle_win.set_pipeline_active(True)
        return False

    def _stop_pipeline(self) -> None:
        self._pipeline_running = False
        self._toggle_btn.set_label("▶  Iniciar")
        self._toggle_btn.remove_css_class("destructive-action")
        self._toggle_btn.add_css_class("suggested-action")

        self._stop_mic_monitor()

        from audifonospro.pipeline.coordinator import get_pipeline
        get_pipeline().stop()

        # Restaurar A2DP si activamos HFP
        if self._hfp_switch and self._hfp_switch.get_active():
            mac = self.settings.bluetooth.primary_mac
            if mac:
                threading.Thread(target=self._restore_a2dp, args=(mac,), daemon=True).start()

        # Detener ANC si lo activamos nosotros
        if self._anc_started_here:
            threading.Thread(target=self._stop_anc, daemon=True).start()
            self._anc_started_here = False

        self._stt_row.set_subtitle("Inactivo")
        self._trans_row.set_subtitle("Inactivo")
        self._tts_row.set_subtitle("Inactivo")
        self._latency_row.set_subtitle("─")
        if self._subtitle_win is not None:
            self._subtitle_win.set_pipeline_active(False)

    @staticmethod
    def _restore_a2dp(mac: str) -> None:
        try:
            from audifonospro.audio.bluetooth import set_profile
            set_profile(mac, "a2dp-sink")
        except Exception:
            pass

    @staticmethod
    def _stop_anc() -> None:
        try:
            from audifonospro.anc.pipewire_anc import get_anc
            get_anc().stop()
        except Exception:
            pass

    # ── Callbacks del pipeline (desde hilos worker) ───────────────────────

    def _on_pipeline_status(self, stage: str, text: str) -> None:
        GLib.idle_add(self._apply_status, stage, text)

    def _apply_status(self, stage: str, text: str) -> bool:
        if stage == "stt":
            self._stt_row.set_subtitle(text)
        elif stage == "trans":
            self._trans_row.set_subtitle(text)
        elif stage == "tts":
            self._tts_row.set_subtitle(text)
        elif stage == "latency":
            self._latency_row.set_subtitle(text)
        return False

    def _on_transcript(self, original: str, translated: str) -> None:
        GLib.idle_add(self._append_transcript, original, translated)
        if self._subtitle_win is not None:
            self._subtitle_win.update(original, translated)

    def _append_transcript(self, original: str, translated: str) -> bool:
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        buf = self._transcript_buf
        if buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).startswith("Aquí aparecerá"):
            buf.set_text("")
        if original == translated:
            entry = f"[{ts}]  {original}\n\n"
        else:
            entry = f"[{ts}]  {original}\n→  {translated}\n\n"
        buf.insert(buf.get_end_iter(), entry)
        self._transcript_view.scroll_to_iter(buf.get_end_iter(), 0.0, False, 0.0, 1.0)
        return False

    def _on_clear_history(self, _btn: Gtk.Button) -> None:
        self._transcript_buf.set_text("Aquí aparecerá el historial de traducciones…\n")

    def _on_load_history(self, _btn: Gtk.Button) -> None:
        """Carga las últimas 50 frases de la DB en el historial."""
        threading.Thread(target=self._load_history_bg, daemon=True).start()

    def _load_history_bg(self) -> None:
        try:
            from audifonospro.db.phrases import get_recent_phrases
            rows = get_recent_phrases(50)
        except Exception as exc:
            GLib.idle_add(self._append_raw, f"[Error al cargar historial: {exc}]\n")
            return
        if not rows:
            GLib.idle_add(self._append_raw, "[El historial está vacío]\n")
            return
        # Ordenar cronológico (vienen DESC de la DB)
        rows = list(reversed(rows))
        lines = ["─── Historial de la base de datos ───\n"]
        for r in rows:
            ts = r["timestamp"][:19].replace("T", " ")
            orig = r["original"]
            trans = r["translated"]
            if orig == trans:
                lines.append(f"[{ts}]  {orig}\n\n")
            else:
                lines.append(f"[{ts}]  {orig}\n→  {trans}\n\n")
        GLib.idle_add(self._append_raw, "".join(lines))

    def _append_raw(self, text: str) -> bool:
        buf = self._transcript_buf
        if buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).startswith("Aquí aparecerá"):
            buf.set_text("")
        buf.insert(buf.get_end_iter(), text)
        self._transcript_view.scroll_to_iter(buf.get_end_iter(), 0.0, False, 0.0, 1.0)
        return False
