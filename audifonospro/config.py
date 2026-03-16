"""
Configuración central del sistema.

Jerarquía de prioridad (mayor → menor):
  1. Variables de entorno (export AUDIO__INPUT_DEVICE=JBL)
  2. Archivo .env
  3. config.yaml
  4. Valores por defecto en los modelos

Uso:
    from audifonospro.config import get_settings
    s = get_settings()
    print(s.stt.model)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# Raíz del proyecto (carpeta que contiene pyproject.toml)
_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_YAML = _PROJECT_ROOT / "config.yaml"
_ENV_FILE = _PROJECT_ROOT / ".env"


# ── Modelos de configuración por subsistema ────────────────────────────────────

class AudioConfig(BaseModel):
    input_device: str = "auto"
    output_device: str = "auto"
    sample_rate: int = 48000
    buffer_ms: int = 30
    channels: int = 1


class BluetoothConfig(BaseModel):
    auto_reconnect: bool = True
    preferred_codec: str = "aac"
    mic_profile: str = "headset-head-unit"
    music_profile: str = "a2dp-sink"
    restore_profile_on_exit: bool = True


class ANCConfig(BaseModel):
    default_level: int = Field(default=1, ge=0, le=5)
    lms_filter_len: int = 128
    lms_mu: float = 0.005
    spectral_stationary: bool = True
    reference_device: str = "auto"


class EQConfig(BaseModel):
    default_preset: str = "flat"


class VADConfig(BaseModel):
    silence_threshold_db: float = -40.0
    silence_duration_ms: int = 600
    min_speech_ms: int = 300
    max_speech_ms: int = 30000


class STTConfig(BaseModel):
    provider: str = "whisper_cpp"
    model: str = "small"
    language: str = "es"
    whisper_cpp_binary: str = "~/whisper.cpp/main"
    whisper_cpp_model: str = "~/whisper.cpp/models/ggml-small.bin"


class TranslationConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    ollama_model: str = "llama3:8b"
    ollama_host: str = "http://localhost:11434"
    target_language: str = "English"
    system_prompt: str = (
        "Translate the following to {target_language}. "
        "Be concise and natural. Output only the translation."
    )


class TTSConfig(BaseModel):
    provider: str = "edge_tts"
    edge_tts_voice: str = "es-MX-JorgeNeural"
    piper_binary: str = "~/piper/piper"
    piper_model: str = "~/piper/es_MX-claude-medium.onnx"
    openai_voice: str = "nova"
    openai_model: str = "tts-1"


class CinemaConfig(BaseModel):
    sync_threshold_ms: int = 50
    bt_latency_compensation_ms: int = 280
    jack_latency_compensation_ms: int = 15
    builtin_latency_compensation_ms: int = 30
    default_eq_per_device: bool = True


class PipelineConfig(BaseModel):
    queue_maxsize_raw: int = 10
    queue_maxsize_segment: int = 3
    queue_maxsize_tts: int = 5
    queue_maxsize_audio_out: int = 10


class UIConfig(BaseModel):
    refresh_rate_ms: int = 500
    theme: str = "dark"
    log_level: str = "INFO"


# ── Fuente YAML para pydantic-settings ────────────────────────────────────────

class YamlConfigSource(PydanticBaseSettingsSource):
    """Lee config.yaml como fuente de configuración de menor prioridad."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = {}
        if yaml_file.is_file():
            with open(yaml_file) as f:
                self._data = yaml.safe_load(f) or {}

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, True

    def field_is_complex(self, field: FieldInfo) -> bool:
        return True

    def prepare_field_value(
        self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
    ) -> Any:
        return value

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field_name, field_info in self.settings_cls.model_fields.items():
            value, key, is_complex = self.get_field_value(field_info, field_name)
            if value is not None:
                prepared = self.prepare_field_value(field_name, field_info, value, is_complex)
                if prepared is not None:
                    result[key] = prepared
        return result


# ── Settings principal ─────────────────────────────────────────────────────────

class Settings(BaseSettings):
    # Secrets — solo en .env, nunca en config.yaml
    openai_api_key: str = ""

    # Subsistemas
    audio: AudioConfig = AudioConfig()
    bluetooth: BluetoothConfig = BluetoothConfig()
    anc: ANCConfig = ANCConfig()
    eq: EQConfig = EQConfig()
    vad: VADConfig = VADConfig()
    stt: STTConfig = STTConfig()
    translation: TranslationConfig = TranslationConfig()
    tts: TTSConfig = TTSConfig()
    cinema: CinemaConfig = CinemaConfig()
    pipeline: PipelineConfig = PipelineConfig()
    ui: UIConfig = UIConfig()

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        **_kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Prioridad: init > env vars > .env file > config.yaml > defaults
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSource(settings_cls, _CONFIG_YAML),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna la instancia singleton de Settings. Thread-safe tras primera llamada."""
    return Settings()
