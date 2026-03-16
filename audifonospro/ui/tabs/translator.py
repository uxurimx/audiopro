"""
Tab 5 — Traductor en tiempo real

Muestra el estado del pipeline de traducción:
  - Indicador VAD (barra de energía del micrófono)
  - Estado por etapa: IDLE / ESCUCHANDO / STT / LLM / TTS
  - Tiempo de cada etapa (ms)
  - Texto transcrito (input)
  - Texto traducido (output)
  - Historial de la sesión

Pipeline: mic → ANC → VAD → STT → LLM → TTS → speaker

Controles:
  - [Iniciar] / [Pausar]
  - Idioma fuente y destino (selectores)
  - Modo: Traducir | Explicar | Traducir + notas
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label, Static
from textual.widget import Widget

from audifonospro.config import Settings


class TranslatorTab(Widget):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label("[bold]Traductor en tiempo real[/bold]")
        yield Static(
            "Implementación en Fase 4 — VAD → STT → LLM → TTS pipeline",
            classes="placeholder",
        )
