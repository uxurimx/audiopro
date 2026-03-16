"""
STT: whisper.cpp (local, subprocess) y OpenAI Whisper API (fallback cloud).

whisper.cpp:
  - Binary: ~/whisper.cpp/main  o  ~/whisper.cpp/whisper-cli
  - Modelo: ~/whisper.cpp/models/ggml-small.bin  (configurable en config.yaml)
  - Instalar: make install-whisper  (desde el Makefile del proyecto)

OpenAI Whisper:
  - Requiere OPENAI_API_KEY en .env
  - Modelo: whisper-1

Uso:
    text = transcribe(wav_bytes, language="es", settings=get_settings())
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


# Mapa de nombres de idioma (UI) → código ISO 639-1 para whisper
LANG_CODES: dict[str, str] = {
    "Español": "es", "English": "en", "Français": "fr", "Deutsch": "de",
    "Italiano": "it", "Português": "pt", "日本語": "ja", "中文": "zh", "한국어": "ko",
}


def transcribe(
    wav_bytes: bytes,
    language: str = "es",
    settings: object | None = None,
    provider: str | None = None,
) -> str:
    """
    Transcribe audio WAV a texto.

    wav_bytes : bytes WAV (int16, mono, 16 kHz)
    language  : código ISO 639-1 o nombre completo ("Español" / "es")
    provider  : "whisper_cpp" | "openai" | None (auto desde settings)
    Retorna   : texto transcrito, o "" si falla
    """
    lang = LANG_CODES.get(language, language)  # normalizar nombre → código

    if settings is None:
        from audifonospro.config import get_settings
        settings = get_settings()

    prov = provider or settings.stt.provider

    if prov == "whisper_cpp":
        try:
            return _transcribe_whisper_cpp(wav_bytes, lang, settings)
        except Exception as exc:
            # Si whisper.cpp no está instalado, intentar OpenAI
            if settings.openai_api_key:
                return _transcribe_openai(wav_bytes, lang, settings)
            raise RuntimeError(f"whisper.cpp falló y no hay clave OpenAI: {exc}") from exc
    elif prov == "openai":
        return _transcribe_openai(wav_bytes, lang, settings)
    else:
        raise ValueError(f"STT provider desconocido: {prov!r}")


# ── whisper.cpp ───────────────────────────────────────────────────────────────

def _find_whisper_binary(settings: object) -> Path | None:
    """Encuentra el binario de whisper.cpp en rutas comunes."""
    candidates = [
        Path(settings.stt.whisper_cpp_binary).expanduser(),
        # build/bin/ es donde cmake instala los binarios
        Path("~/whisper.cpp/build/bin/whisper-cli").expanduser(),
        Path("~/whisper.cpp/build/bin/main").expanduser(),
        # rutas legacy
        Path("~/whisper.cpp/main").expanduser(),
        Path("~/whisper.cpp/whisper-cli").expanduser(),
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return c
    return None


def _transcribe_whisper_cpp(wav_bytes: bytes, language: str, settings: object) -> str:
    binary = _find_whisper_binary(settings)
    if binary is None:
        raise FileNotFoundError(
            "whisper.cpp no encontrado. Ejecuta: make install-whisper"
        )

    model = Path(settings.stt.whisper_cpp_model).expanduser()
    if not model.exists():
        raise FileNotFoundError(f"Modelo whisper.cpp no encontrado: {model}")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        wav_path = f.name

    try:
        result = subprocess.run(
            [
                str(binary),
                "-m", str(model),
                "-f", wav_path,
                "-l", language,
                "--no-timestamps",
                "--print-progress",
                "-np",              # no progress bar en stderr
                "-t", "4",          # 4 threads CPU
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        lines = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and not line.startswith("[") and not line.startswith("whisper")
        ]
        return " ".join(lines).strip()
    finally:
        os.unlink(wav_path)


# ── OpenAI Whisper API ────────────────────────────────────────────────────────

def _transcribe_openai(wav_bytes: bytes, language: str, settings: object) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada en .env")

    import openai
    client = openai.OpenAI(api_key=settings.openai_api_key)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        wav_path = f.name

    try:
        with open(wav_path, "rb") as audio_file:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=language,
            )
        return resp.text.strip()
    finally:
        os.unlink(wav_path)
