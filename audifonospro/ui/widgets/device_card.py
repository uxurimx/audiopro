"""
Widget DeviceCard — tarjeta completa de un dispositivo de audio.

Muestra en una sola vista compacta:
  - Nombre, tipo, estado de conexión
  - Batería (barra) y señal RSSI (barras)
  - Perfil/codec activo
  - Info de PipeWire: nodo, estado, xruns, latencia
  - Capacidades: micrófonos, ANC hardware
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from audifonospro.monitor.device_info import AudioDevice, DeviceType


def _render_card(device: AudioDevice) -> str:
    """Genera el markup Rich completo para una tarjeta de dispositivo."""
    # ── Header ──
    status_color = "green" if device.connected else "red"
    status_text  = "● Conectado" if device.connected else "○ Desconectado"
    type_label   = device.type.value.upper()

    header = (
        f"[bold]{device.type_icon} {device.name}[/bold]"
        f"  [{status_color}]{status_text}[/]"
        f"  [dim]{type_label}[/dim]"
    )

    lines = [header, ""]

    # ── Fila 1: batería + señal (solo BT) ──
    if device.type == DeviceType.BLUETOOTH:
        bat_label = f"🔋 {device.battery_bar}" if device.battery_percent is not None \
                    else "[dim]🔋 ─── sin datos ───[/dim]"
        sig_label = f"📶 {device.rssi_bar}" if device.rssi_dbm is not None \
                    else "[dim]📶 ─── sin datos ───[/dim]"
        lines.append(f"  {bat_label}    {sig_label}")

    # ── Fila 2: perfil / codec / PipeWire ──
    parts: list[str] = []
    if device.bt_profile:
        parts.append(f"[cyan]{device.connection_label}[/cyan]")
    if device.pw_sample_rate:
        parts.append(f"{device.pw_sample_rate // 1000}kHz")
    if device.pw_sink_node_id is not None:
        parts.append(f"PW #{device.pw_sink_node_id}")
    if parts:
        lines.append("  " + "  ·  ".join(parts))

    # ── Fila 3: estado PipeWire + métricas ──
    if device.pw_state != "unknown":
        xruns_color = "red" if device.pw_xruns > 0 else "green"
        lat = f"{device.pw_latency_ms:.1f}ms" if device.pw_latency_ms > 0 else "─"
        lines.append(
            f"  Estado {device.pw_state_indicator}  "
            f"Xruns [{xruns_color}]{device.pw_xruns}[/]  "
            f"Latencia {lat}"
        )

    # ── Fila 4: capacidades ──
    caps: list[str] = []
    if device.is_input and device.mic_channels > 0:
        caps.append(f"🎤 {device.mic_channels} mic{'s' if device.mic_channels > 1 else ''}")
    if device.anc_hw_capable:
        caps.append("[green]ANC hardware[/green]")
    if device.available_profiles:
        profs = " | ".join(device.available_profiles)
        caps.append(f"[dim]{profs}[/dim]")
    if caps:
        lines.append("  " + "  ·  ".join(caps))

    # ── Asignación persona (Cinema Mode) ──
    if device.assigned_person:
        lines.append(
            f"  👤 Asignado a: [bold]{device.assigned_person}[/bold]"
            + (f"  Pista #{device.audio_track}" if device.audio_track is not None else "")
        )

    return "\n".join(lines)


class DeviceCard(Widget):
    """
    Tarjeta interactiva de un dispositivo de audio.

    El contenido se actualiza in-place con update_device(),
    sin recrear el widget (evita parpadeo).
    """

    DEFAULT_CSS = """
    DeviceCard {
        height: auto;
        border: round $primary-darken-2;
        margin: 0 0 1 0;
        padding: 1 2;
        background: $surface-darken-1;
    }
    DeviceCard:hover {
        border: round $primary;
        background: $surface;
    }
    DeviceCard.-selected {
        border: round $accent;
        background: $surface;
    }
    """

    def __init__(self, device: AudioDevice, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._device = device

    def compose(self) -> ComposeResult:
        yield Static(_render_card(self._device), id="card-content")

    def update_device(self, device: AudioDevice) -> None:
        """Actualiza el contenido de la tarjeta con nuevos datos."""
        self._device = device
        try:
            self.query_one("#card-content", Static).update(_render_card(device))
        except Exception:
            pass  # Widget puede no estar montado todavía
