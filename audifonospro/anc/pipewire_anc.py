"""
ANC software vía PipeWire.

Dos modos:
  - mic : module-echo-cancel (WebRTC AEC + supresión de ruido).
          Crea fuente virtual "audifonospro ANC Mic".
          Sin paquetes extra — usa aec/libspa-aec-webrtc (incluido en PipeWire ≥ 0.3).

  - out : module-filter-chain (pasa-bandas builtin).
          Crea sink virtual "audifonospro Filtro de Ruido".
          Sin paquetes extra — usa filtros bq_highpass/bq_lowpass de PipeWire.

Misma estrategia que pipewire_eq.py:
  - Genera un .conf en ~/.config/audifonospro/
  - Lanza `pipewire -c config` (core.daemon=false) como subproceso cliente

IMPORTANTE: context.spa-libs es obligatorio para que protocol-native encuentre
los plugins SPA de audioconvert y support. Sin esa sección el proceso falla con
"can't find protocol 'PipeWire:Protocol:Native'".
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


# ── Generadores de configuración ──────────────────────────────────────────────

_CONF_HEADER = """\
context.properties = {{
  core.daemon = false
  core.name   = {name}
  log.level   = 0
}}

context.spa-libs = {{
  audio.convert.* = audioconvert/libspa-audioconvert
  support.*       = support/libspa-support
}}

context.modules = [
  {{ name = libpipewire-module-rt
    flags = [ ifexists nofail ]
  }}
  {{ name = libpipewire-module-protocol-native }}
  {{ name = libpipewire-module-client-node }}
  {{ name = libpipewire-module-adapter }}
"""


def _generate_mic_config() -> str:
    header = _CONF_HEADER.format(name="audifonospro-anc")
    return (
        "# audifonospro ANC Micrófono (WebRTC) — generado automáticamente\n\n"
        + header
        + """\
  { name = libpipewire-module-echo-cancel
    args = {
      library.name = aec/libspa-aec-webrtc

      capture.props = {
        node.name        = "audifonospro_anc_capture"
        node.description = "audifonospro ANC (entrada mic)"
      }
      source.props = {
        node.name        = "audifonospro_anc_source"
        node.description = "audifonospro ANC Mic"
        media.class      = Audio/Source
        node.virtual     = true
      }
      sink.props = {
        node.name        = "audifonospro_anc_sink"
        node.description = "audifonospro ANC (referencia)"
        media.class      = Audio/Sink
        node.virtual     = true
      }
      playback.props = {
        node.name        = "audifonospro_anc_playback"
        node.description = "audifonospro ANC (salida referencia)"
      }
    }
  }
]
"""
    )


def _generate_out_config(hp_hz: float, lp_hz: float) -> str:
    header = _CONF_HEADER.format(name="audifonospro-anc-out")
    return (
        "# audifonospro Filtro de Ruido (salida) — generado automáticamente\n\n"
        + header
        + f"""\
  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = "audifonospro Filtro de Ruido"
      media.name       = "audifonospro Filtro de Ruido"
      filter.graph = {{
        nodes = [
          {{ type = builtin  label = bq_highpass  name = hp
             control = {{ "Freq" = {hp_hz:.1f}  "Q" = 0.707 }}
          }}
          {{ type = builtin  label = bq_lowpass  name = lp
             control = {{ "Freq" = {lp_hz:.1f}  "Q" = 0.707 }}
          }}
        ]
        links = [
          {{ output = "hp:Out"  input = "lp:In" }}
        ]
        inputs  = [ "hp:In" ]
        outputs = [ "lp:Out" ]
      }}
      capture.props = {{
        node.name        = "audifonospro_anc_out_sink"
        node.description = "audifonospro Filtro de Ruido"
        media.class      = "Audio/Sink"
        node.virtual     = true
        audio.position   = [ FL  FR ]
      }}
      playback.props = {{
        node.name        = "audifonospro_anc_out_playback"
        node.description = "audifonospro Filtro de Ruido → Salida"
        audio.position   = [ FL  FR ]
        node.passive     = true
      }}
    }}
  }}
]
"""
    )


def intensity_to_freqs(intensity: int) -> tuple[float, float]:
    """
    Convierte intensidad 0–100 a frecuencias de corte.

    hp: 20 Hz (suave) → 150 Hz (agresivo) — elimina zumbidos HVAC/AC
    lp: 22 kHz (todo pasa) → 14 kHz (agresivo) — elimina silbidos electrónicos
    """
    t = max(0, min(100, intensity)) / 100.0
    hp = 20.0 + t * 130.0       # 20 → 150 Hz
    lp = 22000.0 - t * 8000.0   # 22 kHz → 14 kHz
    return hp, lp


# ── Clase principal ───────────────────────────────────────────────────────────

class PipeWireANC:
    """
    Gestiona el proceso PipeWire de cancelación de ruido.

    Uso:
        anc = PipeWireANC()
        anc.apply("mic")                 # ANC de micrófono
        anc.apply("out", intensity=70)   # Filtro de salida
        anc.stop()
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._conf_file: Path | None = None
        self._active_mode: str = ""

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def active_mode(self) -> str:
        return self._active_mode if self.is_running else ""

    def apply(self, mode: str, intensity: int = 50) -> tuple[bool, str]:
        """
        Activa ANC en el modo indicado.

        mode      : "mic" | "out"
        intensity : 0–100 (sólo relevante para mode="out")
        Retorna   : (éxito, mensaje)
        """
        if not shutil.which("pipewire"):
            return False, "pipewire no encontrado en PATH"

        if mode == "mic":
            config_text = _generate_mic_config()
            conf_name   = "anc-mic.conf"
            device_name = "audifonospro ANC Mic"
        elif mode == "out":
            hp, lp = intensity_to_freqs(intensity)
            config_text = _generate_out_config(hp, lp)
            conf_name   = "anc-out.conf"
            device_name = "audifonospro Filtro de Ruido"
        else:
            return False, f"Modo desconocido: {mode!r}"

        conf_dir = Path.home() / ".config" / "audifonospro"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / conf_name
        conf_path.write_text(config_text)
        self._conf_file = conf_path

        self.stop()
        time.sleep(0.15)

        try:
            self._process = subprocess.Popen(
                ["pipewire", "-c", str(conf_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.6)

            if self._process.poll() is not None:
                stderr = self._process.stderr.read().decode(errors="replace")
                return False, f"pipewire terminó: {stderr[:300]}"

            self._active_mode = mode
            return True, f"ANC activo — dispositivo '{device_name}' disponible en PipeWire"

        except Exception as exc:
            return False, str(exc)

    def stop(self) -> None:
        """Detiene el proceso y elimina los nodos virtuales."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._active_mode = ""

    def __del__(self) -> None:
        self.stop()


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: PipeWireANC | None = None


def get_anc() -> PipeWireANC:
    global _instance
    if _instance is None:
        _instance = PipeWireANC()
    return _instance
