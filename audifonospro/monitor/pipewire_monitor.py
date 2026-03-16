"""
Monitor de PipeWire.

Usa 'pw-dump' para obtener el estado de todos los nodos de audio:
  - IDs de nodos por dispositivo
  - Estado (running / idle / suspended)
  - Sample rate y canales
  - Xrun count (glitches de audio)
  - Latencia reportada por el sink
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field


@dataclass
class PipeWireNode:
    node_id: int
    node_name: str          # "bluez_output.B4_84_D5_98_E8_31.1"
    description: str        # "JBL VIBE BUDS"
    media_class: str        # "Audio/Sink" | "Audio/Source" | "Stream/..."
    state: str              # "running" | "idle" | "suspended" | "error"
    sample_rate: int        = 48000
    channels: int           = 2
    xrun_count: int         = 0
    latency_ms: float       = 0.0
    # Nombre del dispositivo físico (para agrupar múltiples nodos del mismo device)
    device_name: str        = ""
    mac_hint: str | None    = None   # MAC extraída del nombre del nodo si es BT


def get_pipewire_nodes() -> list[PipeWireNode]:
    """
    Ejecuta pw-dump y parsea todos los nodos de audio.

    Retorna lista vacía si pw-dump falla o no hay nodos.
    """
    try:
        result = subprocess.run(
            ["pw-dump"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        raw: list[dict] = json.loads(result.stdout)
    except Exception:
        return []

    nodes: list[PipeWireNode] = []

    for obj in raw:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue

        info = obj.get("info", {})
        props = info.get("props", {})

        media_class: str = props.get("media.class", "")
        # Solo nodos de audio real (no streams de aplicación en este paso)
        if not media_class.startswith("Audio/"):
            continue

        node_name: str   = props.get("node.name", "")
        description: str = (
            props.get("node.description")
            or props.get("device.description")
            or node_name
        )
        state: str = info.get("state", "unknown")

        # Sample rate: puede estar en props o en format
        sr = (
            props.get("audio.rate")
            or props.get("clock.rate")
            or 48000
        )
        channels = props.get("audio.channels", 2)
        xruns    = info.get("xrun-count", 0)

        # Latencia: PipeWire la reporta como "a/b" (numerador/denominador)
        lat_str: str = props.get("latency.denominator", "")
        latency_ms   = 0.0
        if lat_str:
            try:
                latency_ms = (1 / int(lat_str)) * 1000
            except (ValueError, ZeroDivisionError):
                pass

        # Extraer MAC si el nodo es BT
        mac_hint: str | None = None
        mac_m = re.search(
            r"([0-9A-F]{2}(?:_[0-9A-F]{2}){5})",
            node_name,
            re.IGNORECASE,
        )
        if mac_m:
            mac_hint = mac_m.group(1).replace("_", ":").upper()

        nodes.append(PipeWireNode(
            node_id=obj.get("id", 0),
            node_name=node_name,
            description=description,
            media_class=media_class,
            state=state,
            sample_rate=int(sr) if sr else 48000,
            channels=int(channels),
            xrun_count=int(xruns),
            latency_ms=latency_ms,
            device_name=description,
            mac_hint=mac_hint,
        ))

    return nodes


def get_nodes_for_mac(mac: str, nodes: list[PipeWireNode]) -> list[PipeWireNode]:
    """Filtra nodos PipeWire que corresponden a una MAC de dispositivo BT."""
    mac_normalized = mac.replace(":", "_").upper()
    return [n for n in nodes if mac_normalized in n.node_name.upper()]


def get_nodes_for_sink_name(
    sink_name: str, nodes: list[PipeWireNode]
) -> list[PipeWireNode]:
    """Filtra nodos por nombre de sink/source exacto."""
    return [n for n in nodes if n.node_name == sink_name]
