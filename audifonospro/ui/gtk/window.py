"""
Ventana principal — Adw.ApplicationWindow.

Layout estilo GNOME HIG:
  - Adw.ToolbarView contiene HeaderBar + ViewStack + ViewSwitcherBar
  - Adw.ViewSwitcherTitle en el header (tabs visibles cuando ventana es ancha)
  - Adw.ViewSwitcherBar en el fondo (aparece cuando ventana es estrecha)
  - Adw.ToastOverlay para notificaciones flotantes
  - Responde al color scheme del sistema (dark/light mode automático)
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

        self.set_title("audifonospro")
        self.set_default_size(960, 680)

        # ── Toast overlay (notificaciones) ────────────────────────────────
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        # ── Toolbar view ──────────────────────────────────────────────────
        toolbar_view = Adw.ToolbarView()
        self._toast_overlay.set_child(toolbar_view)

        # ── View stack (contenido de cada página) ─────────────────────────
        self._stack = Adw.ViewStack()
        toolbar_view.set_content(self._stack)

        # ── Header bar ────────────────────────────────────────────────────
        header = Adw.HeaderBar()
        header.set_show_back_button(False)

        # Título adaptativo: muestra tabs en el header cuando hay espacio
        title = Adw.ViewSwitcherTitle()
        title.set_title("audifonospro")
        title.set_stack(self._stack)
        header.set_title_widget(title)

        # Botón de menú (hamburguesa) a la derecha
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_tooltip_text("Menú")
        header.pack_end(menu_btn)

        toolbar_view.add_top_bar(header)

        # ── Bottom switcher bar (para ventanas estrechas) ─────────────────
        switcher_bar = Adw.ViewSwitcherBar()
        switcher_bar.set_stack(self._stack)
        toolbar_view.add_bottom_bar(switcher_bar)

        # Revelar bottom bar solo cuando el header no tiene espacio para los tabs
        title.connect(
            "notify::title-visible",
            lambda t, _: switcher_bar.set_reveal(t.get_title_visible()),
        )

        # ── Cargar páginas ─────────────────────────────────────────────────
        self._load_pages()

    def _load_pages(self) -> None:
        pages = [
            ("audio",   "Audio",      "audio-headphones-symbolic",     "audifonospro.ui.gtk.pages.devices_page",    "DevicesPage"),
            ("trans",   "Traductor",  "microphone-symbolic",           "audifonospro.ui.gtk.pages.translator_page", "TranslatorPage"),
            ("cinema",  "Cinema",     "video-display-symbolic",        "audifonospro.ui.gtk.pages.cinema_page",     "CinemaPage"),
            ("cfg",     "Ajustes",    "preferences-system-symbolic",   "audifonospro.ui.gtk.pages.settings_page",   "SettingsPage"),
        ]

        for page_id, title, icon, module_path, class_name in pages:
            try:
                import importlib
                mod    = importlib.import_module(module_path)
                cls    = getattr(mod, class_name)
                widget = cls(settings=self.settings)
            except Exception as exc:
                widget = self._error_page(title, str(exc))

            page = self._stack.add_titled(widget, page_id, title)
            page.set_icon_name(icon)

    @staticmethod
    def _error_page(title: str, error: str) -> Gtk.Widget:
        status = Adw.StatusPage()
        status.set_title(f"Error cargando {title}")
        status.set_description(error)
        status.set_icon_name("dialog-error-symbolic")
        return status

    # ── API pública ───────────────────────────────────────────────────────

    def show_toast(self, message: str, timeout: int = 3) -> None:
        """Muestra una notificación flotante en la parte inferior."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self._toast_overlay.add_toast(toast)

    def navigate_to(self, page_id: str) -> None:
        self._stack.set_visible_child_name(page_id)
