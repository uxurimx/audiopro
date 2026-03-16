"""
Tab 7 — Ajustes avanzados

Configuración completa del sistema, editable en vivo.
Los cambios se escriben a config.yaml y se aplican sin reiniciar
(donde sea posible).

Secciones:
  STT       → proveedor, modelo, idioma, ruta al binario
  LLM       → proveedor, modelo, prompt del sistema
  TTS       → proveedor, voz, modelo
  Audio     → buffer, sample rate, dispositivos de referencia ANC
  Bluetooth → perfiles, codec preferido, auto-reconexión
  Pipeline  → tamaños de cola, timeouts
  API Keys  → escritas al keyring del sistema (no a disco plano)
  Sistema   → log level, tema UI, intervalo de refresh del monitor
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label, Static
from textual.widget import Widget

from audifonospro.config import Settings


class SettingsTab(Widget):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label("[bold]Ajustes avanzados[/bold]")
        yield Static(
            "Implementación en Fase 4 — config editor en vivo",
            classes="placeholder",
        )
