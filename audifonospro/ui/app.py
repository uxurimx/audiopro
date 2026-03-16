"""
Aplicación principal Textual — audifonospro TUI.

7 pestañas, cada una es un módulo independiente que se monta aquí.
El App actúa como bus de eventos central: los workers de background
publican estado vía app.call_from_thread() y los tabs se suscriben.
"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from audifonospro.config import Settings


class AudiofonosApp(App[None]):
    """Sistema de audio personal multi-dispositivo."""

    TITLE = "audifonospro"
    SUB_TITLE = "v0.1"

    CSS = """
    Screen {
        background: $surface;
    }

    TabbedContent {
        height: 1fr;
    }

    TabPane {
        padding: 0;
    }

    .placeholder {
        padding: 2 4;
        color: $text-muted;
        text-style: italic;
    }

    /* Colores de estado de dispositivo */
    .status-connected    { color: $success; }
    .status-disconnected { color: $error; }
    .status-busy         { color: $warning; }

    /* Barra de batería */
    .battery-high   { color: $success; }
    .battery-medium { color: $warning; }
    .battery-low    { color: $error; }
    """

    BINDINGS = [
        Binding("q", "quit", "Salir", priority=True),
        Binding("d", "toggle_dark", "Tema"),
        Binding("1", "show_tab('devices')",    "Dispositivos", show=False),
        Binding("2", "show_tab('monitor')",    "Monitor",      show=False),
        Binding("3", "show_tab('controls')",   "Controles",    show=False),
        Binding("4", "show_tab('eq')",         "EQ",           show=False),
        Binding("5", "show_tab('translator')", "Traductor",    show=False),
        Binding("6", "show_tab('stacks')",     "Stacks",       show=False),
        Binding("7", "show_tab('settings')",   "Ajustes",      show=False),
    ]

    def __init__(
        self,
        settings: Settings | None = None,
        start_mode: str = "ui",
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.settings = settings or Settings()
        self.start_mode = start_mode

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with TabbedContent(initial="tab-devices", id="main-tabs"):
            with TabPane("📱 Dispositivos", id="tab-devices"):
                from audifonospro.ui.tabs.devices import DevicesTab
                yield DevicesTab(settings=self.settings)

            with TabPane("📡 Monitor", id="tab-monitor"):
                from audifonospro.ui.tabs.monitor import MonitorTab
                yield MonitorTab(settings=self.settings)

            with TabPane("🎮 Controles", id="tab-controls"):
                from audifonospro.ui.tabs.controls import ControlsTab
                yield ControlsTab(settings=self.settings)

            with TabPane("🎛 EQ", id="tab-eq"):
                from audifonospro.ui.tabs.eq import EQTab
                yield EQTab(settings=self.settings)

            with TabPane("🎤 Traductor", id="tab-translator"):
                from audifonospro.ui.tabs.translator import TranslatorTab
                yield TranslatorTab(settings=self.settings)

            with TabPane("📦 Stacks", id="tab-stacks"):
                from audifonospro.ui.tabs.stacks import StacksTab
                yield StacksTab(settings=self.settings)

            with TabPane("⚙ Ajustes", id="tab-settings"):
                from audifonospro.ui.tabs.settings import SettingsTab
                yield SettingsTab(settings=self.settings)

        yield Footer()

    def on_mount(self) -> None:
        """Arranca workers de background al montar la app."""
        if self.start_mode == "cinema":
            self.action_show_tab("devices")

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = f"tab-{tab_id}"

    def action_toggle_dark(self) -> None:
        self.dark = not self.dark
