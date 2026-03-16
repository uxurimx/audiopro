"""
Monitor de dispositivos Bluetooth.

Fuentes de datos (todas vía subprocess para máxima compatibilidad con Python 3.14):
  - bluetoothctl  → nombre, MAC, RSSI, UUIDs, estado de conexión
  - pactl          → perfil activo, codec, perfiles disponibles
  - upower         → porcentaje de batería
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field


@dataclass
class BTCardInfo:
    """Info de una tarjeta Bluetooth desde pactl list cards."""
    card_name: str               # "bluez_card.B4_84_D5_98_E8_31"
    mac: str                     # "B4:84:D5:98:E8:31"
    description: str             # "JBL VIBE BUDS"
    active_profile: str          # "a2dp-sink"
    available_profiles: list[str] = field(default_factory=list)
    sink_name: str | None = None
    source_name: str | None = None


@dataclass
class BTDeviceInfo:
    """Info de un dispositivo BT desde bluetoothctl info."""
    mac: str
    name: str
    connected: bool
    rssi_dbm: int | None
    uuids: list[str] = field(default_factory=list)
    has_mic: bool = False       # UUID Headset (0x1108) o Handsfree (0x111E) presentes


def _run(cmd: list[str], timeout: int = 4) -> str:
    """Ejecuta un comando y retorna stdout, o cadena vacía en caso de error."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout
    except Exception:
        return ""


# ── bluetoothctl ──────────────────────────────────────────────────────────────

def get_connected_bt_macs() -> list[str]:
    """Lista las MACs de los dispositivos BT conectados actualmente."""
    output = _run(["bluetoothctl", "devices", "Connected"])
    macs: list[str] = []
    for line in output.splitlines():
        # Formato: "Device B4:84:D5:98:E8:31 JBL VIBE BUDS"
        match = re.search(r"Device\s+([0-9A-F:]{17})", line)
        if match:
            macs.append(match.group(1))
    return macs


def get_bt_device_info(mac: str) -> BTDeviceInfo | None:
    """
    Obtiene nombre, RSSI, UUIDs y estado de conexión de un device BT.

    bluetoothctl info B4:84:D5:98:E8:31
    """
    output = _run(["bluetoothctl", "info", mac])
    if not output:
        return None

    name = ""
    connected = False
    rssi: int | None = None
    uuids: list[str] = []

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Connected:"):
            connected = "yes" in line.lower()
        elif line.startswith("RSSI:"):
            m = re.search(r"-?\d+", line)
            if m:
                rssi = int(m.group())
        elif line.startswith("UUID:"):
            uuids.append(line)

    # Detectar capacidad de micrófono por UUIDs estándar
    mic_uuids = {
        "00001108",  # Headset (HSP)
        "0000111e",  # Handsfree (HFP)
        "00001112",  # Headset - Audio Gateway
    }
    has_mic = any(u_id in " ".join(uuids).lower() for u_id in mic_uuids)

    return BTDeviceInfo(
        mac=mac,
        name=name or mac,
        connected=connected,
        rssi_dbm=rssi,
        uuids=uuids,
        has_mic=has_mic,
    )


# ── pactl list cards ──────────────────────────────────────────────────────────

def get_bt_cards() -> list[BTCardInfo]:
    """
    Parsea 'pactl list cards' para extraer info de tarjetas Bluetooth.

    Extrae: nombre, MAC, descripción, perfil activo y perfiles disponibles.
    """
    output = _run(["pactl", "list", "cards"])
    cards: list[BTCardInfo] = []

    if not output:
        return cards

    # Dividir en bloques por tarjeta
    blocks = re.split(r"\nCard #\d+", "\nCard #0" + output)

    for block in blocks[1:]:  # saltar el primer fragmento vacío
        if "bluez_card" not in block:
            continue

        card_name_m = re.search(r"Name:\s+(bluez_card\.\S+)", block)
        desc_m      = re.search(r'device\.description\s*=\s*"([^"]+)"', block)
        profile_m   = re.search(r"Active Profile:\s+(\S+)", block)
        sink_m      = re.search(r"sink:.*?Name:\s+(\S+)", block)
        source_m    = re.search(r"source:.*?Name:\s+(\S+)", block)

        if not card_name_m:
            continue

        card_name = card_name_m.group(1)
        # bluez_card.B4_84_D5_98_E8_31 → B4:84:D5:98:E8:31
        mac = card_name.replace("bluez_card.", "").replace("_", ":")

        # Perfiles disponibles (líneas que empiezan con nombre-perfil:)
        profiles = re.findall(
            r"^\s+(a2dp-sink|headset-head-unit(?:-cvsd|-msbc)?|off):",
            block,
            re.MULTILINE,
        )

        cards.append(BTCardInfo(
            card_name=card_name,
            mac=mac,
            description=desc_m.group(1) if desc_m else card_name,
            active_profile=profile_m.group(1) if profile_m else "unknown",
            available_profiles=profiles,
            sink_name=sink_m.group(1) if sink_m else None,
            source_name=source_m.group(1) if source_m else None,
        ))

    return cards


# ── upower (batería) ──────────────────────────────────────────────────────────

def get_battery_percent(mac: str) -> int | None:
    """
    Lee el porcentaje de batería de un dispositivo BT vía upower.

    La ruta UPower: /org/freedesktop/UPower/devices/headset_dev_B4_84_D5_98_E8_31
    """
    upower_path = (
        "/org/freedesktop/UPower/devices/headset_dev_"
        + mac.replace(":", "_")
    )
    output = _run(["upower", "-i", upower_path])
    if not output:
        # Intento alternativo: listar todos los dispositivos upower y buscar por MAC
        all_devices = _run(["upower", "-e"])
        for dev_path in all_devices.splitlines():
            if mac.replace(":", "_").lower() in dev_path.lower():
                output = _run(["upower", "-i", dev_path.strip()])
                break

    if output:
        m = re.search(r"percentage:\s+(\d+)%", output)
        if m:
            return int(m.group(1))

    return None


# ── Codec activo ──────────────────────────────────────────────────────────────

def get_active_codec(card_name: str) -> str | None:
    """
    Intenta detectar el codec activo de una tarjeta BT.

    PipeWire expone el codec como propiedad en pactl list cards.
    """
    output = _run(["pactl", "list", "cards"])
    if not output or card_name not in output:
        return None

    # Buscar el bloque de esta tarjeta
    start = output.find(card_name)
    block = output[start:start + 2000]

    # Buscar bluetooth.codec o a2dp.codec
    for pattern in [
        r'bluetooth\.codec\s*=\s*"([^"]+)"',
        r'a2dp\.codec\s*=\s*"([^"]+)"',
        r'"codec"\s*=\s*"([^"]+)"',
    ]:
        m = re.search(pattern, block)
        if m:
            return m.group(1).upper()

    # Inferir por perfil activo
    if "headset-head-unit" in block:
        active_m = re.search(r"Active Profile:\s+(\S+)", block)
        if active_m and "headset" in active_m.group(1):
            return "mSBC"

    return None
