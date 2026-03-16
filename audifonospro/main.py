"""
Punto de entrada de audifonospro.

Uso:
    audifonospro              # abre la TUI completa
    audifonospro --mode cinema
    audifonospro --mode translate
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="audifonospro",
        description="Sistema de audio personal multi-dispositivo",
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

    # Verificar dependencias críticas
    try:
        from textual.app import App  # noqa: F401
    except ImportError:
        print("[ERROR] Textual no instalado. Ejecuta: pip install -e .", file=sys.stderr)
        sys.exit(1)

    from audifonospro.ui.app import AudiofonosApp
    app = AudiofonosApp(settings=settings, start_mode=args.mode)
    app.run()


if __name__ == "__main__":
    main()
