"""
Tab 1 — Dispositivos

Muestra todos los dispositivos de audio disponibles:
  - Bluetooth (A2DP / HFP)
  - Jack 3.5mm
  - Bocinas integradas de la laptop
  - HDMI

Permite asignar cada dispositivo a una persona y seleccionar
el canal de audio que recibirá (para Cinema Mode).
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label, Static
from textual.widget import Widget

from audifonospro.config import Settings


class DevicesTab(Widget):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label("[bold]Dispositivos de audio[/bold]")
        yield Static(
            "Implementación en Fase 1 — Device Manager + Monitor D-Bus",
            classes="placeholder",
        )
