"""
Punto de entrada de audifonospro.

Uso:
    audifonospro                 # abre la GUI GTK4/libadwaita
    audifonospro --ui tui        # TUI Textual (fallback)
    audifonospro --mode cinema
    audifonospro --mode translate
"""
from __future__ import annotations

import argparse
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="audifonospro",
        description="Sistema de audio personal multi-dispositivo",
    )
    parser.add_argument(
        "--ui",
        choices=["gtk", "tui"],
        default="gtk",
        help="Interfaz gráfica: gtk (default) o tui (Textual)",
    )
    parser.add_argument(
        "--mode",
        choices=["ui", "cinema", "translate"],
        default="ui",
        help="Modo de inicio (default: ui)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Carga de configuración — falla rápido si hay errores de config
    try:
        from audifonospro.config import get_settings
        settings = get_settings()
    except Exception as exc:
        print(f"[ERROR] No se pudo cargar la configuración: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.ui == "gtk":
        _launch_gtk(settings)
    else:
        _launch_tui(settings, args.mode)


def _launch_gtk(settings: object) -> None:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: F401
    except (ImportError, ValueError) as exc:
        print(
            f"[ERROR] GTK4/libadwaita no disponible: {exc}\n"
            "Intenta con: audifonospro --ui tui",
            file=sys.stderr,
        )
        sys.exit(1)

    from audifonospro.ui.gtk.app import AudiofonosApp
    app = AudiofonosApp(settings=settings)
    sys.exit(app.run_app())


def _launch_tui(settings: object, mode: str) -> None:
    try:
        from textual.app import App  # noqa: F401
    except ImportError:
        print("[ERROR] Textual no instalado. Ejecuta: pip install -e .", file=sys.stderr)
        sys.exit(1)

    from audifonospro.ui.app import AudiofonosApp
    app = AudiofonosApp(settings=settings, start_mode=mode)
    app.run()


if __name__ == "__main__":
    main()
