"""
EQ de 10 bandas vía PipeWire filter-chain.

Estrategia:
  1. Genera un archivo de configuración PipeWire con 10 nodos bq_peaking en serie.
  2. Lanza `pipewire -c <config>` como subproceso → crea un sink virtual llamado
     "audifonospro EQ" SIN reiniciar el servicio principal de PipeWire.
  3. Para actualizar las ganancias, mata el proceso y lo relanza con el nuevo config.

El sink virtual aparece como destino en pavucontrol/GNOME Sound Settings.
El usuario puede enrutar su aplicación (ej. VLC, mpv) a ese sink para que pase
por el EQ antes de llegar al dispositivo físico.

Bandas ISO de 10 bandas:  32 · 64 · 125 · 250 · 500 · 1k · 2k · 4k · 8k · 16kHz
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

# Hz de cada banda (ISO 1/3 de octava, 10 bandas)
BANDS_HZ: list[int] = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

# Q factor para biquad peaking (≈ una octava de ancho)
_Q = 0.707


def _generate_config(gains: list[float]) -> str:
    """
    Genera el texto del archivo .conf de PipeWire para el filter-chain EQ.

    gains: lista de 10 valores en dB, uno por banda.
    """
    if len(gains) != 10:
        raise ValueError(f"Se esperan 10 ganancias, se recibieron {len(gains)}")

    nodes_lines: list[str] = []
    links_lines: list[str] = []

    for i, (hz, gain_db) in enumerate(zip(BANDS_HZ, gains)):
        name = f"eq{i + 1}"
        nodes_lines.append(
            f'        {{ type = builtin  label = bq_peaking  name = {name}\n'
            f'          control = {{ "Freq" = {float(hz)}  "Q" = {_Q}  "Gain" = {float(gain_db)} }}\n'
            f'        }}'
        )
        if i < 9:
            next_name = f"eq{i + 2}"
            links_lines.append(
                f'        {{ output = "{name}:Out"  input = "{next_name}:In" }}'
            )

    nodes_block = "\n".join(nodes_lines)
    links_block = "\n".join(links_lines)

    return f"""# audifonospro EQ filter-chain — generado automáticamente
# No editar manualmente: será sobreescrito por la aplicación.

# IMPORTANTE: core.daemon = false hace que pipewire arranque como CLIENTE
# del servidor existente en lugar de intentar ser un segundo servidor.
context.properties = {{
  core.daemon = false
  core.name   = audifonospro-eq
}}

context.modules = [
  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = "audifonospro EQ"
      media.name       = "audifonospro EQ"
      filter.graph = {{
        nodes = [
{nodes_block}
        ]
        links = [
{links_block}
        ]
        inputs  = [ "eq1:In" ]
        outputs = [ "eq10:Out" ]
      }}
      capture.props = {{
        node.name        = "audifonospro_eq_sink"
        node.description = "audifonospro EQ"
        audio.position   = [ FL  FR ]
        media.class      = "Audio/Sink"
        node.virtual     = true
      }}
      playback.props = {{
        node.name        = "audifonospro_eq_output"
        node.description = "audifonospro EQ → Salida"
        audio.position   = [ FL  FR ]
        node.passive     = true
      }}
    }}
  }}
]
"""


class PipeWireEQ:
    """
    Gestiona el proceso pipewire del filter-chain EQ.

    Uso:
        eq = PipeWireEQ()
        eq.apply([0, 0, 0, 2, 4, 5, 4, 3, 2, 1])  # Vocal clarity
        eq.stop()
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._conf_file: Path | None = None
        self._active_gains: list[float] = [0.0] * 10

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def active_gains(self) -> list[float]:
        return list(self._active_gains)

    def apply(self, gains: list[float]) -> tuple[bool, str]:
        """
        Aplica la curva EQ.  Crea o recrea el proceso PipeWire.

        Retorna (éxito, mensaje).
        """
        if not shutil.which("pipewire"):
            return False, "pipewire no encontrado en PATH"

        config_text = _generate_config(gains)

        # Escribir config en archivo persistente (no temp) para poder inspeccionarlo
        conf_dir = Path.home() / ".config" / "audifonospro"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "eq-filter-chain.conf"
        conf_path.write_text(config_text)
        self._conf_file = conf_path

        # Matar el proceso anterior si existe
        self.stop()
        time.sleep(0.15)  # dar tiempo a PipeWire para liberar el nodo

        try:
            self._process = subprocess.Popen(
                ["pipewire", "-c", str(conf_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.5)  # esperar a que el nodo registre en el grafo

            # Verificar que no falló inmediatamente
            if self._process.poll() is not None:
                stderr = self._process.stderr.read().decode(errors="replace")
                return False, f"pipewire terminó: {stderr[:200]}"

            self._active_gains = list(gains)
            return True, f"EQ activo — sink 'audifonospro EQ' disponible en {conf_path}"

        except Exception as exc:
            return False, str(exc)

    def stop(self) -> None:
        """Detiene el proceso y elimina el sink virtual."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def __del__(self) -> None:
        self.stop()


# Instancia singleton (un solo proceso EQ por aplicación)
_instance: PipeWireEQ | None = None


def get_eq() -> PipeWireEQ:
    global _instance
    if _instance is None:
        _instance = PipeWireEQ()
    return _instance
