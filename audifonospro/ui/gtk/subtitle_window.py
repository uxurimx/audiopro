"""
SubtitleWindow — ventana flotante de subtítulos en tiempo real.

Muestra la traducción en texto grande sobre fondo oscuro.
Diseñada para colocarse en la parte inferior de la pantalla
mientras se ve un video.

Uso:
    from audifonospro.ui.gtk.subtitle_window import SubtitleWindow
    win = SubtitleWindow()
    win.present()
    win.update("Hello there", "Hola")   # llamar desde cualquier hilo
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk, GLib


_CSS = b"""
window.subtitle-win {
    background-color: #0f0f14;
}
window.subtitle-win headerbar {
    background-color: #1a1a24;
    border-bottom: 1px solid #2e2e40;
    box-shadow: none;
    min-height: 30px;
    padding: 0 6px;
}
window.subtitle-win headerbar * {
    color: #c8c8d0;
    font-size: 11px;
}
window.subtitle-win headerbar button {
    min-height: 24px;
    min-width: 24px;
    padding: 2px;
}
.sub-translated {
    color: #f0f0f0;
    font-size: 22pt;
    font-weight: 500;
    letter-spacing: 0.3px;
}
.sub-original {
    color: #707888;
    font-size: 12pt;
    font-style: italic;
}
.sub-status {
    color: #404858;
    font-size: 10pt;
}
"""

_PROVIDER: Gtk.CssProvider | None = None


def _get_provider() -> Gtk.CssProvider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = Gtk.CssProvider()
        _PROVIDER.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            _PROVIDER,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    return _PROVIDER


class SubtitleWindow(Gtk.Window):
    """
    Ventana flotante de subtítulos con fondo oscuro.

    Thread-safe: update() puede llamarse desde cualquier hilo.
    El texto se limpia automáticamente 8 s después de la última traducción.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        _get_provider()   # registrar CSS

        self.set_title("audioPro · Subtítulos")
        self.set_default_size(780, 130)
        self.set_resizable(True)
        self.add_css_class("subtitle-win")

        # Layout central
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_margin_top(14)
        box.set_margin_bottom(18)
        box.set_margin_start(24)
        box.set_margin_end(24)

        # Texto traducido (grande)
        self._translated_lbl = Gtk.Label(label="Esperando traducción…")
        self._translated_lbl.set_wrap(True)
        self._translated_lbl.set_xalign(0.5)
        self._translated_lbl.set_justify(Gtk.Justification.CENTER)
        self._translated_lbl.set_selectable(True)
        self._translated_lbl.add_css_class("sub-translated")
        box.append(self._translated_lbl)

        # Texto original (pequeño, tenue)
        self._original_lbl = Gtk.Label(label="")
        self._original_lbl.set_wrap(True)
        self._original_lbl.set_xalign(0.5)
        self._original_lbl.set_justify(Gtk.Justification.CENTER)
        self._original_lbl.set_selectable(True)
        self._original_lbl.add_css_class("sub-original")
        box.append(self._original_lbl)

        # Indicador de estado (cuando no hay traducción activa)
        self._status_lbl = Gtk.Label(label="▸ Inicia el traductor para ver los subtítulos")
        self._status_lbl.add_css_class("sub-status")
        self._status_lbl.set_visible(True)
        box.append(self._status_lbl)

        self.set_child(box)
        self._clear_timer: int = 0
        self._active = False   # True mientras el pipeline esté corriendo

    # ── API pública ────────────────────────────────────────────────────────

    def set_pipeline_active(self, active: bool) -> None:
        """Llamar cuando el pipeline inicia/para."""
        GLib.idle_add(self._apply_active, active)

    def update(self, original: str, translated: str) -> None:
        """Thread-safe: llamar desde cualquier hilo al llegar una traducción."""
        GLib.idle_add(self._apply_update, original, translated)

    # ── Internals ─────────────────────────────────────────────────────────

    def _apply_active(self, active: bool) -> bool:
        self._active = active
        if not active:
            self._translated_lbl.set_text("")
            self._original_lbl.set_text("")
            self._status_lbl.set_label("▸ Pipeline detenido")
            self._status_lbl.set_visible(True)
            if self._clear_timer:
                GLib.source_remove(self._clear_timer)
                self._clear_timer = 0
        else:
            self._status_lbl.set_label("▸ Escuchando…")
            self._status_lbl.set_visible(True)
        return False

    def _apply_update(self, original: str, translated: str) -> bool:
        self._translated_lbl.set_text(translated)
        # Ocultar el original si es igual a la traducción (modo transcripción)
        if original != translated:
            self._original_lbl.set_text(original)
            self._original_lbl.set_visible(True)
        else:
            self._original_lbl.set_visible(False)
        self._status_lbl.set_visible(False)
        # Reiniciar timer de limpieza
        if self._clear_timer:
            GLib.source_remove(self._clear_timer)
        self._clear_timer = GLib.timeout_add_seconds(8, self._on_clear_timeout)
        return False

    def _on_clear_timeout(self) -> bool:
        self._translated_lbl.set_text("")
        self._original_lbl.set_text("")
        if self._active:
            self._status_lbl.set_label("▸ Escuchando…")
            self._status_lbl.set_visible(True)
        self._clear_timer = 0
        return False
