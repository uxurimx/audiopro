"""
Tab 2 — Monitor en tiempo real

Por cada dispositivo conectado muestra:
  - Batería (%) con barra animada
  - Señal RSSI (dBm) con barras visuales
  - Perfil activo (A2DP / HFP / jack / built-in)
  - Codec y sample rate
  - PipeWire: node ID, buffer level, xruns, latencia
  - Micrófonos detectados y capacidades ANC (via GATT)

Fuentes de datos:
  - org.bluez.Battery1        → batería
  - org.bluez.Device1.RSSI   → señal
  - pw-dump JSON              → nodos PipeWire
  - bleak GATT scanner        → capacidades del device
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label, Static
from textual.widget import Widget

from audifonospro.config import Settings


class MonitorTab(Widget):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label("[bold]Monitor de dispositivos[/bold]")
        yield Static(
            "Implementación en Fase 1 — D-Bus + PipeWire monitor",
            classes="placeholder",
        )
