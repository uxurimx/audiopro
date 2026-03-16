"""
Gestión de dispositivos Bluetooth desde la app.

Usa `bluetoothctl` (disponible en Fedora sin privilegios de root) para:
  - Listar dispositivos emparejados
  - Escanear nuevos dispositivos
  - Conectar / desconectar
  - Emparejar nuevos dispositivos

API:
    devices = list_paired()          # [BTDevice(mac, name, connected, ...)]
    scan_result = scan(timeout=8)    # lista de BTDevice descubiertos
    ok, msg = connect(mac)
    ok, msg = disconnect(mac)
    ok, msg = pair(mac)
    ok, msg = remove(mac)
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field


@dataclass
class BTDevice:
    mac: str
    name: str
    connected: bool = False
    paired: bool = False
    trusted: bool = False
    rssi: int | None = None

    @property
    def label(self) -> str:
        status = []
        if self.connected:
            status.append("conectado")
        elif self.paired:
            status.append("emparejado")
        return f"{self.name}  [{self.mac}]" + (f"  — {', '.join(status)}" if status else "")


def _run(cmd: list[str], timeout: int = 6) -> tuple[bool, str]:
    """Ejecuta un comando y devuelve (éxito, salida)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)


def _ctl(*args: str, timeout: int = 6) -> tuple[bool, str]:
    return _run(["bluetoothctl", *args], timeout=timeout)


# ── Consultas ──────────────────────────────────────────────────────────────────

def list_paired() -> list[BTDevice]:
    """Lista dispositivos emparejados con estado de conexión."""
    _, out = _ctl("devices")
    devices: list[BTDevice] = []
    for line in out.splitlines():
        m = re.match(r"Device\s+([0-9A-F:]{17})\s+(.*)", line, re.IGNORECASE)
        if m:
            mac, name = m.group(1).upper(), m.group(2).strip()
            connected, trusted = _get_device_props(mac)
            devices.append(BTDevice(
                mac=mac, name=name or mac,
                connected=connected, paired=True, trusted=trusted,
            ))
    return devices


def _get_device_props(mac: str) -> tuple[bool, bool]:
    """Consulta connected/trusted de un dispositivo emparejado."""
    _, out = _ctl("info", mac)
    connected = bool(re.search(r"Connected:\s+yes", out, re.IGNORECASE))
    trusted   = bool(re.search(r"Trusted:\s+yes",   out, re.IGNORECASE))
    return connected, trusted


def list_connected() -> list[BTDevice]:
    """Solo los dispositivos actualmente conectados."""
    return [d for d in list_paired() if d.connected]


# ── Escaneo ────────────────────────────────────────────────────────────────────

def scan(
    timeout: int = 8,
    on_device_found: callable | None = None,
) -> list[BTDevice]:
    """
    Escanea nuevos dispositivos BT durante `timeout` segundos.

    on_device_found(BTDevice) se llama en el hilo de escaneo cada vez
    que aparece un dispositivo nuevo (útil para actualizar la UI en tiempo real).

    Devuelve todos los dispositivos encontrados (emparejados + nuevos).
    """
    found: dict[str, BTDevice] = {}
    stop_event = threading.Event()

    def _reader(proc: subprocess.Popen) -> None:
        pat = re.compile(r"\[NEW\]\s+Device\s+([0-9A-F:]{17})\s+(.*)", re.IGNORECASE)
        for line in proc.stdout:
            m = pat.search(line)
            if m:
                mac  = m.group(1).upper()
                name = m.group(2).strip()
                if mac not in found:
                    dev = BTDevice(mac=mac, name=name or mac, paired=False)
                    found[mac] = dev
                    if on_device_found:
                        on_device_found(dev)
            if stop_event.is_set():
                break

    try:
        proc = subprocess.Popen(
            ["bluetoothctl", "scan", "on"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        reader = threading.Thread(target=_reader, args=(proc,), daemon=True)
        reader.start()
        time.sleep(timeout)
    finally:
        stop_event.set()
        _ctl("scan", "off")
        try:
            proc.terminate()
        except Exception:
            pass

    # Combinar con dispositivos ya emparejados
    for dev in list_paired():
        if dev.mac not in found:
            found[dev.mac] = dev
        else:
            found[dev.mac].paired    = True
            found[dev.mac].connected = dev.connected
            found[dev.mac].trusted   = dev.trusted

    return list(found.values())


# ── Acciones ───────────────────────────────────────────────────────────────────

def connect(mac: str) -> tuple[bool, str]:
    """Conecta a un dispositivo emparejado."""
    ok, out = _ctl("connect", mac, timeout=15)
    if ok or "successful" in out.lower():
        return True, f"Conectado a {mac}"
    return False, out or "Error al conectar"


def disconnect(mac: str) -> tuple[bool, str]:
    """Desconecta un dispositivo."""
    ok, out = _ctl("disconnect", mac, timeout=10)
    if ok or "successful" in out.lower():
        return True, f"Desconectado {mac}"
    return False, out or "Error al desconectar"


def pair(mac: str) -> tuple[bool, str]:
    """Inicia emparejamiento. Puede requerir confirmación en terminal."""
    ok, out = _ctl("pair", mac, timeout=30)
    if ok or "successful" in out.lower():
        _ctl("trust", mac)
        return True, f"Emparejado {mac}"
    return False, out or "Error al emparejar"


def remove(mac: str) -> tuple[bool, str]:
    """Elimina un dispositivo emparejado."""
    ok, out = _ctl("remove", mac, timeout=8)
    if ok:
        return True, f"Dispositivo {mac} eliminado"
    return False, out or "Error al eliminar"
