"""
audioPro — instalador de la extensión GNOME Shell.

Empaqueta los archivos JS/CSS/metadata junto al paquete Python y los copia
al directorio de extensiones del usuario cuando el usuario lo solicita desde
la UI de Ajustes.

Uso:
    from audifonospro.gnome_ext.installer import get_status, install, uninstall
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

EXT_UUID = "audiopro@robit.dev"
EXT_SRC  = Path(__file__).parent                                     # archivos bundled
EXT_DST  = Path.home() / ".local/share/gnome-shell/extensions" / EXT_UUID
_DCONF_KEY = "/org/gnome/shell/enabled-extensions"
_BUNDLE_FILES = ("metadata.json", "extension.js", "stylesheet.css")


# ── Estado ────────────────────────────────────────────────────────────────────

def get_status() -> dict:
    """
    Devuelve:
        installed: bool — archivos presentes en ~/.local/share/...
        enabled:   bool — UUID en dconf enabled-extensions
        running:   bool — GNOME Shell la tiene cargada en esta sesión
    """
    installed = (EXT_DST / "metadata.json").exists()
    enabled   = _is_in_dconf()
    running   = _is_running()
    return {"installed": installed, "enabled": enabled, "running": running}


# ── Instalar / desinstalar ─────────────────────────────────────────────────────

def install() -> tuple[bool, str]:
    """Copia archivos y activa en dconf. Devuelve (ok, mensaje)."""
    try:
        EXT_DST.mkdir(parents=True, exist_ok=True)
        for fname in _BUNDLE_FILES:
            shutil.copy2(EXT_SRC / fname, EXT_DST / fname)
        _set_dconf_enabled(True)
        return True, "Extensión instalada. Cierra sesión y vuelve a entrar para activarla."
    except Exception as exc:
        return False, f"Error al instalar: {exc}"


def uninstall() -> tuple[bool, str]:
    """Desactiva en dconf y elimina archivos."""
    try:
        _set_dconf_enabled(False)
        shutil.rmtree(EXT_DST, ignore_errors=True)
        return True, "Extensión desinstalada correctamente."
    except Exception as exc:
        return False, f"Error al desinstalar: {exc}"


# ── dconf ─────────────────────────────────────────────────────────────────────

def _read_dconf_list() -> list[str]:
    try:
        r = subprocess.run(
            ["dconf", "read", _DCONF_KEY],
            capture_output=True, text=True, timeout=3
        )
        raw = r.stdout.strip()
        if not raw or raw == "@as []":
            return []
        # dconf usa comillas simples: ['a', 'b']  → JSON válido con "
        return json.loads(raw.replace("'", '"'))
    except Exception:
        return []


def _write_dconf_list(uuids: list[str]) -> None:
    val = "[" + ", ".join(f"'{u}'" for u in uuids) + "]"
    subprocess.run(
        ["dconf", "write", _DCONF_KEY, val],
        timeout=3, check=True
    )


def _is_in_dconf() -> bool:
    return EXT_UUID in _read_dconf_list()


def _set_dconf_enabled(enable: bool) -> None:
    current = _read_dconf_list()
    if enable and EXT_UUID not in current:
        current.append(EXT_UUID)
    elif not enable and EXT_UUID in current:
        current.remove(EXT_UUID)
    else:
        return
    _write_dconf_list(current)


def _is_running() -> bool:
    """Verifica si GNOME Shell tiene la extensión cargada en la sesión actual."""
    try:
        r = subprocess.run(
            ["gnome-extensions", "info", EXT_UUID],
            capture_output=True, text=True, timeout=3
        )
        return "ENABLED" in r.stdout or "ACTIVE" in r.stdout
    except Exception:
        return False
