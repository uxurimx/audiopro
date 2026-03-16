"""
Stacks: configuraciones nombradas de STT + Traducción + TTS.

Cada stack define qué motores usar y con qué parámetros.
El StackManager guarda el stack activo y lo aplica al pipeline.

Stacks disponibles:
  LOCAL      — 100% offline, máxima privacidad, mayor latencia
  SWEET_SPOT — whisper.cpp + GPT-4o-mini + edge-tts (balance calidad/costo)
  CLOUD_PRO  — OpenAI Whisper + GPT-4o + OpenAI TTS (máxima calidad)
  CINEMA     — modo multi-pista MKV vía GStreamer (sin STT/traducción)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stack:
    id: str
    title: str
    stt_provider: str          # "whisper_cpp" | "openai"
    stt_model: str             # ggml-small / ggml-tiny / whisper-1
    trans_provider: str        # "ollama" | "openai"
    trans_model: str           # llama3:8b / llama3.2:3b / gpt-4o-mini / gpt-4o
    tts_provider: str          # "edge_tts" | "piper" | "openai"
    tts_voice: str             # voz edge-tts / piper model / openai voice


# ── Presets ───────────────────────────────────────────────────────────────────

PRESETS: dict[str, Stack] = {
    "LOCAL": Stack(
        id="LOCAL",
        title="Local",
        stt_provider="whisper_cpp",
        stt_model="ggml-tiny",
        trans_provider="ollama",
        trans_model="llama3.2:3b",
        tts_provider="piper",
        tts_voice="es_MX-claude-medium.onnx",
    ),
    "SWEET_SPOT": Stack(
        id="SWEET_SPOT",
        title="Sweet Spot",
        stt_provider="whisper_cpp",
        stt_model="ggml-small",
        trans_provider="openai",
        trans_model="gpt-4o-mini",
        tts_provider="edge_tts",
        tts_voice="es-MX-JorgeNeural",
    ),
    "CLOUD_PRO": Stack(
        id="CLOUD_PRO",
        title="Cloud Pro",
        stt_provider="openai",
        stt_model="whisper-1",
        trans_provider="openai",
        trans_model="gpt-4o",
        tts_provider="openai",
        tts_voice="nova",
    ),
    "CINEMA": Stack(
        id="CINEMA",
        title="Cinema",
        stt_provider="whisper_cpp",  # no usado en cinema mode
        stt_model="ggml-small",
        trans_provider="openai",     # no usado en cinema mode
        trans_model="gpt-4o-mini",
        tts_provider="edge_tts",
        tts_voice="es-MX-JorgeNeural",
    ),
}


# ── Manager ───────────────────────────────────────────────────────────────────

class StackManager:
    """Gestiona el stack activo y lo aplica al pipeline de traducción."""

    def __init__(self) -> None:
        self._active_id: str = "SWEET_SPOT"

    @property
    def active(self) -> Stack:
        return PRESETS[self._active_id]

    @property
    def active_id(self) -> str:
        return self._active_id

    def activate(self, stack_id: str, pipeline: object | None = None) -> Stack:
        """
        Activa un stack.
        Si pipeline está corriendo, lo reconfigura sin detenerlo.
        """
        if stack_id not in PRESETS:
            raise ValueError(f"Stack desconocido: {stack_id!r}")

        self._active_id = stack_id
        stack = PRESETS[stack_id]

        if pipeline is not None and hasattr(pipeline, "reconfigure"):
            pipeline.reconfigure(
                stt_provider=stack.stt_provider,
                trans_provider=stack.trans_provider,
                trans_model=stack.trans_model,
                tts_provider=stack.tts_provider,
                tts_voice=stack.tts_voice,
            )

        return stack

    def get(self, stack_id: str) -> Stack:
        return PRESETS[stack_id]


# ── Singleton ─────────────────────────────────────────────────────────────────

_manager: StackManager | None = None


def get_stack_manager() -> StackManager:
    global _manager
    if _manager is None:
        _manager = StackManager()
    return _manager
