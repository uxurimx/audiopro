"""
Adw.Application — punto de entrada de la interfaz gráfica.

Usa el application-id estándar de reverse-DNS para integrarse
correctamente con GNOME Shell (notificaciones, taskbar, etc.).
"""
from __future__ import annotations

import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from audifonospro.config import Settings


class AudiofonosApp(Adw.Application):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            application_id="dev.robit.audifonospro",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.settings = settings
        self.connect("activate", self._on_activate)

    def _on_activate(self, _app: "AudiofonosApp") -> None:
        self._apply_color_scheme()
        from audifonospro.ui.gtk.window import MainWindow
        win = MainWindow(application=self, settings=self.settings)
        win.present()

    def _apply_color_scheme(self) -> None:
        """
        Aplica el tema de color según settings.ui.theme.

        El portal D-Bus falla en algunos entornos (sandbox, SSH, etc.),
        por eso leemos del config en lugar de depender del portal.
        """
        theme = (self.settings.ui.theme or "dark").lower()
        scheme_map = {
            "dark":  Adw.ColorScheme.FORCE_DARK,
            "light": Adw.ColorScheme.FORCE_LIGHT,
        }
        scheme = scheme_map.get(theme, Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def run_app(self) -> int:
        return self.run(sys.argv)
