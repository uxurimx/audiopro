"""
Tab 6 — Stacks

Un Stack es una configuración nombrada de toda la cadena de procesamiento.
Un clic activa el modo completo.

Stacks predefinidos:
  LOCAL       → whisper.cpp + llama3:8b + piper         → $0.00/sesión
  SWEET_SPOT  → whisper.cpp + GPT-4o-mini + edge-tts    → ~$0.004/sesión  ★
  CLOUD_PRO   → OpenAI Whisper + GPT-4o + OpenAI TTS    → ~$0.50/sesión
  CINEMA      → GStreamer multi-track (sin traducción)   → $0.00

Cada stack define:
  - stt.provider + stt.model
  - translation.provider + translation.model
  - tts.provider + tts.voice
  - anc.default_level
  - eq.default_preset

El usuario puede crear stacks CUSTOM y guardarlos por nombre.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label, Static
from textual.widget import Widget

from audifonospro.config import Settings


class StacksTab(Widget):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    def compose(self) -> ComposeResult:
        yield Label("[bold]Stacks de configuración[/bold]")
        yield Static(
            "Implementación en Fase 4 — LOCAL / SWEET_SPOT / CLOUD_PRO / CINEMA",
            classes="placeholder",
        )
