"""
Enumerador unificado de dispositivos de audio.

Combina todas las fuentes (BT, jack, built-in, HDMI) en una lista de
AudioDevice homogénea que alimenta la UI y el pipeline de audio.

Jerarquía de fuentes:
  1. PipeWire (pw-dump) → nodos de audio disponibles
  2. pactl list cards   → perfiles y codecs BT
  3. bluetoothctl       → RSSI, UUIDs, conexión
  4. upower             → batería de dispositivos BT
  5. pactl list sinks   → jack/built-in/HDMI
"""
from __future__ import annotations

import re
import subprocess

from audifonospro.monitor.device_info import AudioDevice, DeviceType
from audifonospro.monitor.bluetooth_monitor import (
    get_connected_bt_macs,
    get_bt_device_info,
    get_bt_cards,
    get_battery_percent,
    get_active_codec,
)
from audifonospro.monitor.pipewire_monitor import (
    get_pipewire_nodes,
    get_nodes_for_mac,
    PipeWireNode,
)


def _run(cmd: list[str], timeout: int = 4) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        ).stdout
    except Exception:
        return ""


# ── Dispositivos Bluetooth ────────────────────────────────────────────────────

def _build_bt_devices(
    pw_nodes: list[PipeWireNode],
) -> list[AudioDevice]:
    """Construye AudioDevice para cada dispositivo BT conectado."""
    devices: list[AudioDevice] = []
    bt_cards  = {c.mac: c for c in get_bt_cards()}
    connected = get_connected_bt_macs()

    # Incluir también MACs de carjetas BT aunque no estén en bluetoothctl connected
    for mac in bt_cards:
        if mac not in connected:
            connected.append(mac)

    for mac in connected:
        bt_info  = get_bt_device_info(mac)
        card     = bt_cards.get(mac)
        bt_nodes = get_nodes_for_mac(mac, pw_nodes)

        name = (bt_info.name if bt_info else None) or \
               (card.description if card else None) or \
               mac

        # Nodo PipeWire de salida (sink)
        sink_node   = next((n for n in bt_nodes if "Sink" in n.media_class), None)
        source_node = next((n for n in bt_nodes if "Source" in n.media_class), None)

        # Detectar micrófono: existe nodo fuente O el profile es HFP
        has_mic = source_node is not None or \
                  (card and "headset" in (card.active_profile or ""))
        mic_ch  = source_node.channels if source_node else (1 if has_mic else 0)

        # Codec activo
        codec = None
        if card:
            codec = get_active_codec(card.card_name)
            if not codec:
                codec = "mSBC" if "headset" in (card.active_profile or "") else "AAC"

        battery = get_battery_percent(mac)

        device = AudioDevice(
            id=mac,
            name=name,
            type=DeviceType.BLUETOOTH,
            connected=(bt_info.connected if bt_info else True),
            mac_address=mac,
            battery_percent=battery,
            rssi_dbm=(bt_info.rssi_dbm if bt_info else None),
            bt_profile=(card.active_profile if card else None),
            bt_codec=codec,
            available_profiles=(card.available_profiles if card else []),
            is_output=sink_node is not None,
            is_input=has_mic,
            mic_channels=mic_ch,
            pw_sink_name=(card.sink_name if card else None),
            pw_source_name=(card.source_name if card else None),
            pw_sink_node_id=(sink_node.node_id if sink_node else None),
            pw_xruns=(sink_node.xrun_count if sink_node else 0),
            pw_latency_ms=(sink_node.latency_ms if sink_node else 0.0),
            pw_sample_rate=(sink_node.sample_rate if sink_node else None),
            pw_state=(sink_node.state if sink_node else "unknown"),
        )
        devices.append(device)

    return devices


# ── Dispositivos ALSA (jack, built-in, HDMI) ─────────────────────────────────

def _build_alsa_devices(pw_nodes: list[PipeWireNode]) -> list[AudioDevice]:
    """
    Construye AudioDevice para dispositivos no-BT: jack, bocinas, HDMI.

    Usa pactl list sinks para descubrir los sinks ALSA disponibles.
    """
    output = _run(["pactl", "list", "sinks"])
    devices: list[AudioDevice] = []

    if not output:
        return devices

    # Dividir en bloques por sink
    blocks = re.split(r"\nSink #\d+", "\nSink #0" + output)

    for block in blocks[1:]:
        name_m = re.search(r"Name:\s+(\S+)", block)
        desc_m = re.search(r'device\.description\s*=\s*"([^"]+)"', block)
        if not desc_m:
            desc_m = re.search(r"Description:\s+(.+)", block)

        if not name_m:
            continue

        sink_name  = name_m.group(1)
        desc       = desc_m.group(1).strip() if desc_m else sink_name

        # Ignorar sinks BT (ya los procesamos arriba)
        if "bluez" in sink_name.lower():
            continue
        # Ignorar sinks virtuales (EasyEffects, filtros)
        if any(v in desc.lower() for v in ["filter", "effect", "virtual", "loopback"]):
            continue

        # Clasificar por tipo
        sname_lower = sink_name.lower()
        desc_lower  = desc.lower()
        if "hdmi" in sname_lower or "hdmi" in desc_lower:
            dev_type = DeviceType.HDMI
            dev_id   = f"hdmi-{len([d for d in devices if d.type == DeviceType.HDMI])}"
        elif "analog" in sname_lower:
            # Built-in Y jack usan el mismo sink; los diferenciamos por descripción
            if "headphone" in desc_lower or "jack" in desc_lower:
                dev_type = DeviceType.JACK
                dev_id   = "jack"
            else:
                dev_type = DeviceType.BUILTIN
                dev_id   = "builtin"
        else:
            dev_type = DeviceType.BUILTIN
            dev_id   = sink_name

        # Buscar nodo PipeWire correspondiente
        pw_node = next(
            (n for n in pw_nodes if n.node_name == sink_name), None
        )

        state_m = re.search(r"State:\s+(\S+)", block)
        state   = state_m.group(1).lower() if state_m else "unknown"

        # También buscar fuente de micrófono integrado
        source_output = _run(["pactl", "list", "sources"])
        has_builtin_mic = False
        if "alsa_input" in source_output and dev_type == DeviceType.BUILTIN:
            has_builtin_mic = True

        device = AudioDevice(
            id=dev_id,
            name=desc,
            type=dev_type,
            connected=state not in ("suspended", "unavailable"),
            is_output=True,
            is_input=(dev_type == DeviceType.BUILTIN and has_builtin_mic),
            mic_channels=(2 if has_builtin_mic else 0),
            pw_sink_name=sink_name,
            pw_sink_node_id=(pw_node.node_id if pw_node else None),
            pw_xruns=(pw_node.xrun_count if pw_node else 0),
            pw_latency_ms=(pw_node.latency_ms if pw_node else 0.0),
            pw_sample_rate=(pw_node.sample_rate if pw_node else None),
            pw_state=state,
        )

        # Evitar duplicados (builtin puede aparecer dos veces: headphone + speaker)
        if not any(d.id == dev_id for d in devices):
            devices.append(device)

    return devices


# ── Punto de entrada ──────────────────────────────────────────────────────────

def enumerate_all_devices() -> list[AudioDevice]:
    """
    Retorna la lista completa y actualizada de dispositivos de audio.

    Orden: Bluetooth primero, luego jack, built-in, HDMI.
    Llamar cada ~500ms desde el worker del monitor.
    """
    pw_nodes = get_pipewire_nodes()
    bt       = _build_bt_devices(pw_nodes)
    alsa     = _build_alsa_devices(pw_nodes)

    # Ordenar: BT conectados, BT desconectados, jack, built-in, HDMI
    def sort_key(d: AudioDevice) -> tuple[int, str]:
        order = {
            DeviceType.BLUETOOTH: 0,
            DeviceType.JACK:      1,
            DeviceType.BUILTIN:   2,
            DeviceType.HDMI:      3,
            DeviceType.VIRTUAL:   4,
        }
        connected_bonus = 0 if d.connected else 1
        return (order.get(d.type, 5) + connected_bonus, d.name)

    return sorted(bt + alsa, key=sort_key)
