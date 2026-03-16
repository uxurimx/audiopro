"""
Routing de audio entre dispositivos de salida vía PipeWire/PulseAudio.

Usa pactl (compatible PulseAudio-over-PipeWire) para:
  - Listar sinks (dispositivos de salida)
  - Listar sink-inputs (streams activos: Spotify, VLC, etc.)
  - Mover un stream de un sink a otro
  - Cambiar el sink por defecto

Ejemplo de flujo:
    sinks  = list_sinks()             # [{id, name, description, running}]
    inputs = list_sink_inputs()       # [{serial, app_name, sink_id, corked}]
    move_stream_to_sink(4352, "bluez_output.12:11:57:94:4D:A7")
"""
from __future__ import annotations

import re
import subprocess


def _run(cmd: list[str], timeout: int = 4) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


# ── Sinks (dispositivos de salida) ────────────────────────────────────────────

def list_sinks() -> list[dict]:
    """
    Lista todos los sinks de audio disponibles.

    Retorna lista de dicts:
      id          : int   — ID numérico del sink
      name        : str   — nombre técnico (ej. bluez_output.B4:84:...)
      description : str   — nombre legible (ej. JBL VIBE BUDS)
      state       : str   — RUNNING | SUSPENDED | IDLE
      volume      : int   — volumen actual 0–100
    """
    output = _run(["pactl", "list", "sinks"])
    sinks: list[dict] = []
    current: dict = {}

    for line in output.splitlines():
        line_s = line.strip()
        m_id = re.match(r"Sink #(\d+)", line)
        if m_id:
            if current:
                sinks.append(current)
            current = {"id": int(m_id.group(1)), "name": "", "description": "",
                       "state": "", "volume": 50}
            continue
        if current:
            if line_s.startswith("Name:"):
                current["name"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("State:"):
                current["state"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("Volume:"):
                m = re.search(r"(\d+)%", line_s)
                if m:
                    current["volume"] = int(m.group(1))
            elif line_s.startswith("Description:"):
                current["description"] = line_s.split(":", 1)[1].strip()

    if current:
        sinks.append(current)

    return sinks


def list_sink_inputs() -> list[dict]:
    """
    Lista todos los streams de audio activos (sink-inputs).

    Retorna lista de dicts:
      serial    : int  — serial del stream (para moverlo)
      sink_id   : int  — ID del sink actual
      app_name  : str  — nombre de la aplicación (Spotify, VLC, etc.)
      media_name: str  — nombre de la pista/media (si disponible)
      corked    : bool — True si está pausado/silenciado
      app_icon  : str  — icon name de la app (ej. com.spotify.Client)
    """
    output = _run(["pactl", "list", "sink-inputs"])
    inputs: list[dict] = []
    current: dict | None = None

    for line in output.splitlines():
        line_s = line.strip()
        m_id = re.match(r"Sink Input #(\d+)", line)
        if m_id:
            if current:
                inputs.append(current)
            current = {
                "serial": int(m_id.group(1)),
                "sink_id": -1,
                "app_name": "Desconocido",
                "media_name": "",
                "corked": False,
                "app_icon": "",
            }
            continue
        if current is None:
            continue

        if line_s.startswith("Sink:"):
            m = re.search(r"\d+", line_s)
            if m:
                current["sink_id"] = int(m.group())
        elif line_s.startswith("Corked:"):
            current["corked"] = "yes" in line_s.lower()
        elif 'application.name' in line_s:
            m = re.search(r'"([^"]+)"', line_s)
            if m:
                current["app_name"] = m.group(1)
        elif 'media.name' in line_s:
            m = re.search(r'"([^"]+)"', line_s)
            if m:
                current["media_name"] = m.group(1)
        elif 'application.icon_name' in line_s or 'pipewire.access.portal.app_id' in line_s:
            m = re.search(r'"([^"]+)"', line_s)
            if m and not current["app_icon"]:
                current["app_icon"] = m.group(1)

    if current:
        inputs.append(current)

    return inputs


# ── Acciones de routing ───────────────────────────────────────────────────────

def move_stream_to_sink(stream_serial: int, sink_name_or_id: str | int) -> tuple[bool, str]:
    """
    Mueve un stream de audio a otro sink.

    Ejemplo:
        move_stream_to_sink(4352, "bluez_output.12:11:57:94:4D:A7")
    """
    target = str(sink_name_or_id)
    try:
        r = subprocess.run(
            ["pactl", "move-sink-input", str(stream_serial), target],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return True, f"Stream {stream_serial} movido a {target}"
        return False, r.stderr.strip() or "Error desconocido"
    except Exception as exc:
        return False, str(exc)


def set_default_sink(sink_name: str) -> tuple[bool, str]:
    """
    Cambia el dispositivo de salida por defecto.
    Las nuevas aplicaciones usarán este sink automáticamente.
    """
    try:
        r = subprocess.run(
            ["pactl", "set-default-sink", sink_name],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return True, f"Salida por defecto → {sink_name}"
        return False, r.stderr.strip()
    except Exception as exc:
        return False, str(exc)


def move_all_streams_to_sink(sink_name: str) -> list[tuple[int, bool]]:
    """
    Mueve TODOS los streams activos al sink indicado.

    Útil para "Mover todo a JBL" / "Mover todo a bocina".
    Retorna lista de (stream_serial, éxito).
    """
    results = []
    for inp in list_sink_inputs():
        ok, _ = move_stream_to_sink(inp["serial"], sink_name)
        results.append((inp["serial"], ok))
    return results


def pin_stream_to_sink(stream_serial: int, sink_name: str) -> tuple[bool, str]:
    """
    Pinea un stream a un sink vía pw-metadata target.object.

    WirePlumber detecta el cambio en la metadata 'default', lo guarda en su
    state file (~/.local/state/wireplumber/) y lo restaura la próxima vez que
    la app abra. También evita que WirePlumber mueva el stream cuando cambia
    el default sink (incluso con linking.follow-default-target=true).

    Flujo:
      1. pw-dump → encontrar PW node-id del stream (por pulse.id == serial)
                   y object.serial del sink (por node.name)
      2. pw-metadata <node-id> target.object <sink-serial>
    """
    import json

    try:
        nodes = json.loads(_run(["pw-dump"]))
    except Exception as exc:
        return False, f"pw-dump: {exc}"

    stream_node = next(
        (n for n in nodes
         if n.get("info", {}).get("props", {}).get("pulse.id") == stream_serial),
        None,
    )
    sink_node = next(
        (n for n in nodes
         if n.get("info", {}).get("props", {}).get("node.name") == sink_name
         and "Sink" in n.get("info", {}).get("props", {}).get("media.class", "")),
        None,
    )

    if not stream_node:
        return False, f"stream {stream_serial} no encontrado en pw-dump"
    if not sink_node:
        return False, f"sink '{sink_name}' no encontrado en pw-dump"

    node_id    = stream_node["id"]
    sink_serial = (sink_node.get("info", {}).get("props", {})
                   .get("object.serial", sink_node["id"]))

    try:
        r = subprocess.run(
            ["pw-metadata", str(node_id), "target.object", str(sink_serial)],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return True, f"stream {stream_serial} pineado a {sink_name}"
        return False, r.stderr.strip() or "pw-metadata error"
    except Exception as exc:
        return False, str(exc)


def get_sink_name_for_mac(mac: str) -> str | None:
    """Busca el nombre del sink de PipeWire para una MAC BT dada."""
    for sink in list_sinks():
        if mac.replace(":", ":").lower() in sink["name"].lower():
            return sink["name"]
    return None


def smart_route_stream(stream_serial: int, target_sink_name: str) -> tuple[bool, str]:
    """
    Mueve un stream de audio de forma inteligente.

    Estrategia:
    1. Cambia el sink por defecto → EasyEffects y apps con auto-connect siguen
       automáticamente sin interrumpir.
    2. También intenta mover el stream directamente (para apps que no siguen
       el default, ej. apps nativas con sink fijo).

    Esto es lo correcto para sistemas con EasyEffects:
      Spotify → easyeffects_sink → [EQ] → DEFAULT_SINK
    Cambiando el default sink, EasyEffects cambia su salida y el usuario
    escucha el resultado en el nuevo dispositivo.
    """
    # Paso 1 (principal): cambiar default sink
    ok, msg = set_default_sink(target_sink_name)

    # Paso 2 (complementario): intentar mover el stream directamente
    # Falla silenciosamente para streams en easyeffects (esperable)
    move_stream_to_sink(stream_serial, target_sink_name)

    return ok, msg


# ── Control de volumen por sink ───────────────────────────────────────────────

def get_sink_volume(sink_name: str) -> int:
    """Devuelve el volumen actual del sink (0–100)."""
    output = _run(["pactl", "get-sink-volume", sink_name])
    m = re.search(r"(\d+)%", output)
    return int(m.group(1)) if m else 50


def set_sink_volume(sink_name: str, percent: int) -> tuple[bool, str]:
    """
    Ajusta el volumen de un sink específico (0–150%).

    Ejemplo:
        set_sink_volume("bluez_output.12:11:57:94:4D:A7", 75)
    """
    pct = max(0, min(150, int(percent)))
    try:
        r = subprocess.run(
            ["pactl", "set-sink-volume", sink_name, f"{pct}%"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return True, f"{sink_name} → {pct}%"
        return False, r.stderr.strip() or "Error"
    except Exception as exc:
        return False, str(exc)

    return ok, msg
