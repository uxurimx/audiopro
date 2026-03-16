"""
Tab 4 — Ecualizador

10 bandas de frecuencia por dispositivo con sliders interactivos.
Compresor dinámico y filtros de claridad vocal.

Presets integrados:
  - flat         → sin modificación
  - vocal        → boost 1-4kHz (claridad de diálogo)
  - bass_boost   → boost sub-80Hz y 200Hz
  - v_shape      → boost graves + agudos, mid recortado
  - accessibility → vocal + compresor dinámico (para problemas auditivos)

Implementación: scipy.signal filtros IIR aplicados en tiempo real
al stream de audio de cada dispositivo (cadena de BiQuad peaking EQ).
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label, Static
from textual.widget import Widget

from audifonospro.config import Settings


class EQTab(Widget):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label("[bold]Ecualizador por dispositivo[/bold]")
        yield Static(
            "Implementación en Fase 3 — scipy IIR filters + presets",
            classes="placeholder",
        )
