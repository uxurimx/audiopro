"""
Escribe ~/.cache/audifonospro/status.json para que la extensión GNOME lo lea.

Formato del JSON:
{
  "pipeline_running": bool,
  "src_lang": "en",
  "dst_lang": "es",
  "eq_preset": "flat",
  "devices": [
    {"name": "JBL Vive Buds", "battery_pct": 78, "codec": "AAC", "connected": true}
  ],
  "updated_at": 1234567890.0
}
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_STATUS_DIR  = Path.home() / ".cache" / "audifonospro"
_STATUS_FILE = _STATUS_DIR / "status.json"

# Estado global en memoria (actualizado por pipeline y monitor)
_state: dict = {
    "pipeline_running": False,
    "src_lang": "",
    "dst_lang": "",
    "eq_preset": "flat",
    "devices": [],
}


def write_status(
    *,
    pipeline_running: bool | None = None,
    src_lang: str | None = None,
    dst_lang: str | None = None,
    eq_preset: str | None = None,
    devices: list[dict] | None = None,
) -> None:
    """
    Actualiza y persiste el estado.  Solo escribe los campos proporcionados.
    Thread-safe: el GIL protege el dict en CPython; el archivo se escribe
    atómicamente (write + rename).
    """
    if pipeline_running is not None: _state["pipeline_running"] = pipeline_running
    if src_lang  is not None:        _state["src_lang"]  = src_lang
    if dst_lang  is not None:        _state["dst_lang"]  = dst_lang
    if eq_preset is not None:        _state["eq_preset"] = eq_preset
    if devices   is not None:        _state["devices"]   = devices

    _state["updated_at"] = time.time()

    try:
        _STATUS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_STATUS_FILE)
    except OSError:
        pass


def clear_status() -> None:
    """Resetea el estado al detener audioPro."""
    write_status(pipeline_running=False, src_lang="", dst_lang="")
    try:
        _STATUS_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def update_devices_from_audio_devices(devices: list) -> None:
    """
    Convierte la lista de AudioDevice del monitor al formato del status JSON.
    Llamar desde device_enumerator cada vez que actualice la lista.
    """
    result = []
    for d in devices:
        if not getattr(d, "connected", False):
            continue
        result.append({
            "name":        d.name,
            "battery_pct": getattr(d, "battery_level", None),
            "codec":       getattr(d, "codec", None),
            "connected":   True,
        })
    write_status(devices=result)
