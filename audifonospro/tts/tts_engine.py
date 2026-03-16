"""
TTS: edge-tts (Microsoft gratis, online), piper (local), OpenAI tts-1.

Salida: archivo MP3/WAV en directorio temporal → reproducción via GStreamer.
GStreamer maneja MP3/OGG/WAV sin dependencias extra (decodebin + autoaudiosink).

Uso:
    path = synthesize("Hola mundo", voice="es-MX-JorgeNeural", provider="edge_tts")
    play_audio(path, device="bluez_output.B4:84:D5:98:E8:31.a2dp-sink")
    os.unlink(path)
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile


# Mapa nombre idioma (UI) → voz edge-tts por defecto
DEFAULT_VOICES: dict[str, str] = {
    "Español":  "es-MX-JorgeNeural",
    "English":  "en-US-AriaNeural",
    "Français": "fr-FR-DeniseNeural",
    "Deutsch":  "de-DE-KatjaNeural",
    "Italiano": "it-IT-ElsaNeural",
    "Português":"pt-BR-FranciscaNeural",
    "日本語":   "ja-JP-NanamiNeural",
    "中文":     "zh-CN-XiaoxiaoNeural",
    "한국어":   "ko-KR-SunHiNeural",
    # códigos cortos
    "es": "es-MX-JorgeNeural",  "en": "en-US-AriaNeural",
    "fr": "fr-FR-DeniseNeural", "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",   "pt": "pt-BR-FranciscaNeural",
    "ja": "ja-JP-NanamiNeural", "zh": "zh-CN-XiaoxiaoNeural",
    "ko": "ko-KR-SunHiNeural",
}


def synthesize(
    text: str,
    voice: str | None = None,
    language: str = "Español",
    provider: str = "edge_tts",
    settings: object | None = None,
) -> str:
    """
    Sintetiza texto a audio. Retorna ruta al archivo temporal.

    voice    : nombre de voz (edge-tts / piper / openai). None → default para language.
    provider : "edge_tts" | "piper" | "openai"
    Retorna  : ruta al archivo de audio (MP3 o WAV)
    """
    if settings is None:
        from audifonospro.config import get_settings
        settings = get_settings()

    resolved_voice = voice or DEFAULT_VOICES.get(language, "en-US-AriaNeural")

    if provider == "edge_tts":
        return _speak_edge_tts(text, resolved_voice)
    elif provider == "piper":
        return _speak_piper(text, settings)
    elif provider == "openai":
        return _speak_openai(text, resolved_voice, settings)
    else:
        raise ValueError(f"TTS provider desconocido: {provider!r}")


def play_audio(path: str, device: str | None = None) -> None:
    """
    Reproduce un archivo de audio sincrónicamente via GStreamer.

    device : nombre del sink PipeWire/PulseAudio (ej. bluez_output.B4:84:D5...)
             None → autoaudiosink (dispositivo por defecto del sistema)
    """
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)

    if device:
        sink_str = f'pulsesink device="{device}"'
    else:
        sink_str = "autoaudiosink"

    pipeline_str = (
        f'filesrc location="{path}" ! decodebin ! '
        f'audioconvert ! audioresample ! {sink_str}'
    )

    try:
        pipeline = Gst.parse_launch(pipeline_str)
        pipeline.set_state(Gst.State.PLAYING)
        bus = pipeline.get_bus()
        bus.timed_pop_filtered(
            30 * Gst.SECOND,
            Gst.MessageType.EOS | Gst.MessageType.ERROR,
        )
        pipeline.set_state(Gst.State.NULL)
    except Exception:
        # Fallback: paplay si GStreamer falla
        subprocess.run(["paplay", path], check=False, timeout=30)


# ── edge-tts ──────────────────────────────────────────────────────────────────

async def _edge_tts_async(text: str, voice: str, path: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(path)


def _speak_edge_tts(text: str, voice: str) -> str:
    path = tempfile.mktemp(suffix=".mp3")
    asyncio.run(_edge_tts_async(text, voice, path))
    return path


# ── piper ─────────────────────────────────────────────────────────────────────

def _speak_piper(text: str, settings: object) -> str:
    from pathlib import Path

    binary = Path(settings.tts.piper_binary).expanduser()
    model  = Path(settings.tts.piper_model).expanduser()

    if not binary.exists():
        raise FileNotFoundError(
            f"piper no encontrado: {binary}. Ejecuta: make install-piper"
        )
    if not model.exists():
        raise FileNotFoundError(f"Modelo piper no encontrado: {model}")

    path = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        [str(binary), "--model", str(model), "--output_file", path],
        input=text.encode(),
        capture_output=True,
        timeout=30,
        check=True,
    )
    return path


# ── OpenAI TTS ────────────────────────────────────────────────────────────────

def _speak_openai(text: str, voice: str, settings: object) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada en .env")

    import openai
    client = openai.OpenAI(api_key=settings.openai_api_key)

    # OpenAI TTS voices: alloy, echo, fable, onyx, nova, shimmer
    # Mapear voces edge-tts a OpenAI si se pasó una voz edge-tts
    oai_voice = settings.tts.openai_voice or "nova"

    path = tempfile.mktemp(suffix=".mp3")
    resp = client.audio.speech.create(
        model=settings.tts.openai_model or "tts-1",
        voice=oai_voice,
        input=text,
    )
    resp.stream_to_file(path)
    return path
