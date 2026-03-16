"""
Traducción de texto: Ollama local (llama3:8b / gemma2:2b) y OpenAI GPT-4o-mini.

Ollama debe estar corriendo: `ollama serve`
Modelos disponibles en esta máquina: llama3:8b, llama3.2:3B, gemma2:2b

Uso:
    translated = translate("Hello world", "es", provider="ollama",
                           model="llama3:8b", settings=get_settings())
"""
from __future__ import annotations


# Mapa nombre idioma (UI) → nombre completo para el prompt
LANG_NAMES: dict[str, str] = {
    "Español": "Spanish", "English": "English", "Français": "French",
    "Deutsch": "German",  "Italiano": "Italian", "Português": "Portuguese",
    "日本語": "Japanese",  "中文": "Chinese",     "한국어": "Korean",
    # códigos cortos también
    "es": "Spanish", "en": "English", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ja": "Japanese",
    "zh": "Chinese", "ko": "Korean",
}

_SYSTEM_PROMPT = (
    "You are a real-time translator. "
    "Translate the following text to {target}. "
    "Output ONLY the translation, nothing else."
)


def translate(
    text: str,
    target_language: str,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    settings: object | None = None,
) -> str:
    """
    Traduce text al idioma destino.

    target_language : nombre UI ("Español") o código ISO ("es")
    provider        : "ollama" | "openai"
    model           : nombre del modelo (gpt-4o-mini / llama3:8b / gemma2:2b …)
    Retorna         : texto traducido
    """
    if not text.strip():
        return ""

    if settings is None:
        from audifonospro.config import get_settings
        settings = get_settings()

    target = LANG_NAMES.get(target_language, target_language)

    if provider == "ollama":
        host = settings.translation.ollama_host
        return _translate_ollama(text, target, model, host)
    elif provider == "openai":
        return _translate_openai(text, target, model, settings.openai_api_key)
    else:
        raise ValueError(f"Translation provider desconocido: {provider!r}")


# ── Ollama ────────────────────────────────────────────────────────────────────

def _translate_ollama(text: str, target: str, model: str, host: str) -> str:
    import httpx

    prompt = f"{_SYSTEM_PROMPT.format(target=target)}\n\n{text}"

    try:
        resp = httpx.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except httpx.ConnectError:
        raise RuntimeError(
            f"Ollama no disponible en {host}. Ejecuta: ollama serve"
        )


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _translate_openai(text: str, target: str, model: str, api_key: str) -> str:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada en .env")

    import openai
    client = openai.OpenAI(api_key=api_key)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT.format(target=target)},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
        max_tokens=512,
    )
    return resp.choices[0].message.content.strip()
