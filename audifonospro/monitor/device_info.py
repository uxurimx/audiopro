"""
Estructuras de datos centrales del sistema.

AudioDevice es el objeto que fluye por toda la app:
  monitor → UI → pipeline → cinema router → EQ engine

Todos los dispositivos de salida/entrada se representan
con esta misma estructura, independientemente de si son
Bluetooth, jack 3.5mm, bocinas integradas o HDMI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DeviceType(Enum):
    BLUETOOTH = "bluetooth"
    JACK      = "jack"      # 3.5mm analógico
    BUILTIN   = "builtin"   # bocinas integradas de la laptop
    HDMI      = "hdmi"
    VIRTUAL   = "virtual"   # sinks virtuales de PipeWire/JACK


@dataclass
class AudioDevice:
    # ── Identidad ──────────────────────────────────────────────────────────
    id: str           # MAC para BT, "builtin", "jack", "hdmi-N", pw-node-name
    name: str         # Nombre legible: "JBL VIBE BUDS", "Built-in Audio", etc.
    type: DeviceType

    # ── Estado de conexión ─────────────────────────────────────────────────
    connected: bool = True

    # ── Bluetooth (solo si type == BLUETOOTH) ──────────────────────────────
    mac_address: str | None         = None
    battery_percent: int | None     = None   # None = no disponible
    rssi_dbm: int | None            = None   # None = no disponible
    bt_profile: str | None          = None   # "a2dp-sink" | "headset-head-unit"
    bt_codec: str | None            = None   # "AAC" | "mSBC" | "SBC"
    available_profiles: list[str]   = field(default_factory=list)

    # ── Capacidades de audio ───────────────────────────────────────────────
    is_output: bool  = True    # puede reproducir audio
    is_input: bool   = False   # tiene micrófono
    mic_channels: int = 0      # 0 = sin mic, 1 = mono, 2 = estéreo
    anc_hw_capable: bool = False   # chip ANC detectado vía GATT

    # ── PipeWire / ALSA ───────────────────────────────────────────────────
    pw_sink_name: str | None    = None  # "bluez_output.B4_84..." | "alsa_output..."
    pw_source_name: str | None  = None  # "bluez_input.B4_84..."  | "alsa_input..."
    pw_sink_node_id: int | None = None
    pw_xruns: int               = 0
    pw_latency_ms: float        = 0.0
    pw_sample_rate: int | None  = None
    pw_state: str               = "unknown"  # running | idle | suspended | error

    # ── Asignación (Cinema Mode / Traductor) ───────────────────────────────
    assigned_person: str | None  = None   # "papa" | "mama" | "hija"
    audio_track: int | None      = None   # índice de pista en el MKV

    # ── Propiedades derivadas ──────────────────────────────────────────────

    @property
    def type_icon(self) -> str:
        return {
            DeviceType.BLUETOOTH: "🎧",
            DeviceType.JACK:      "🔌",
            DeviceType.BUILTIN:   "🔊",
            DeviceType.HDMI:      "📺",
            DeviceType.VIRTUAL:   "🔧",
        }.get(self.type, "🔈")

    @property
    def connection_label(self) -> str:
        if not self.connected:
            return "Desconectado"
        if self.type == DeviceType.BLUETOOTH:
            profile_labels = {
                "a2dp-sink":         f"A2DP ({self.bt_codec or 'SBC'})",
                "headset-head-unit": f"HFP ({self.bt_codec or 'mSBC'})",
            }
            return profile_labels.get(self.bt_profile or "", "BT Conectado")
        return "Activo"

    @property
    def battery_bar(self) -> str:
        """Barra de batería Unicode de 10 caracteres."""
        if self.battery_percent is None:
            return "[dim]──────────[/dim]"
        pct = max(0, min(100, self.battery_percent))
        filled = round(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        color = "green" if pct > 30 else ("yellow" if pct > 10 else "red")
        return f"[{color}]{bar}[/] {pct}%"

    @property
    def rssi_bar(self) -> str:
        """Barras de señal RSSI (estilo móvil)."""
        if self.rssi_dbm is None:
            return "[dim]─────[/dim]"
        dbm = self.rssi_dbm
        # -50 excelente, -70 buena, -80 regular, -90 mala
        if dbm >= -50:
            bars, color = "█████", "green"
        elif dbm >= -60:
            bars, color = "████░", "green"
        elif dbm >= -70:
            bars, color = "███░░", "yellow"
        elif dbm >= -80:
            bars, color = "██░░░", "yellow"
        else:
            bars, color = "█░░░░", "red"
        return f"[{color}]{bars}[/] {dbm} dBm"

    @property
    def pw_state_indicator(self) -> str:
        colors = {
            "running":    "green",
            "idle":       "yellow",
            "suspended":  "dim",
            "error":      "red",
            "unknown":    "dim",
        }
        color = colors.get(self.pw_state, "dim")
        return f"[{color}]{self.pw_state}[/]"
