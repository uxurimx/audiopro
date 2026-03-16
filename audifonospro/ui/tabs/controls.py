"""
Tab 3 — Controles / Button Mapping

Editor visual de gestos para cada dispositivo BT.

Gestos soportados (via evdev):
  - 1 tap, 2 taps, 3 taps
  - Hold corto (500ms), Hold largo (1500ms)
  - Combinaciones L+R simultáneo

Acciones mapeables:
  - Iniciar / pausar traducción
  - Cambiar idioma fuente/destino
  - Cambiar pista de audio (Cinema Mode)
  - Subir / bajar volumen
  - Ciclar preset EQ
  - Toggle ANC (nivel +1 / -1)
  - Activar modo "Explicar" vs "Traducir"
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label, Static
from textual.widget import Widget

from audifonospro.config import Settings


class ControlsTab(Widget):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label("[bold]Mapeo de botones y gestos[/bold]")
        yield Static(
            "Implementación en Fase 5 — evdev gesture machine",
            classes="placeholder",
        )
