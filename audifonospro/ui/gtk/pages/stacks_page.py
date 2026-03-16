"""
StacksPage — selector de configuraciones (stacks) de audio.

Stacks disponibles:
  LOCAL       — todo local, sin red, para uso offline
  SWEET_SPOT  — whisper.cpp + GPT-4o-mini + edge-tts (balance calidad/costo)
  CLOUD_PRO   — OpenAI Whisper API + GPT-4o + OpenAI TTS
  CINEMA      — modo familia, multi-pista MKV por persona

Cada stack muestra: descripción, costo estimado, latencia esperada.
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings

_STACKS = [
    {
        "id":          "LOCAL",
        "title":       "Local",
        "icon":        "computer-symbolic",
        "description": "100% offline. Ollama llama3.2:3B + whisper.cpp tiny + piper TTS.",
        "cost":        "Gratis",
        "latency":     "~8-12 s / frase",
        "badge":       None,
    },
    {
        "id":          "SWEET_SPOT",
        "title":       "Sweet Spot",
        "icon":        "starred-symbolic",
        "description": "whisper.cpp (local) + GPT-4o-mini + edge-tts. Balance óptimo.",
        "cost":        "~$0.004 / sesión",
        "latency":     "~1.5-3 s / frase",
        "badge":       "Recomendado",
    },
    {
        "id":          "CLOUD_PRO",
        "title":       "Cloud Pro",
        "icon":        "cloud-symbolic",
        "description": "OpenAI Whisper API + GPT-4o + OpenAI TTS (voz HD).",
        "cost":        "~$0.08 / sesión",
        "latency":     "~0.8-1.5 s / frase",
        "badge":       None,
    },
    {
        "id":          "CINEMA",
        "title":       "Cinema",
        "icon":        "video-display-symbolic",
        "description": "Multi-pista MKV vía GStreamer. Cada persona recibe su canal.",
        "cost":        "Gratis",
        "latency":     "Síncrono (< 50 ms)",
        "badge":       "Familia",
    },
]


class StacksPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings
        self._active_stack: str = "SWEET_SPOT"
        self._stack_rows: dict[str, Adw.ActionRow] = {}

        self.set_title("Stacks")
        self.set_icon_name("view-app-grid-symbolic")

        # ── Stack activo ──────────────────────────────────────────────────
        active_group = Adw.PreferencesGroup()
        active_group.set_title("Stack activo")
        active_group.set_description(
            "El stack define qué motores STT, traducción y TTS se usan en esta sesión"
        )
        self.add(active_group)

        self._active_row = Adw.ActionRow()
        self._active_row.set_title("Sweet Spot")
        self._active_row.set_subtitle("whisper.cpp + GPT-4o-mini + edge-tts")
        self._active_row.set_icon_name("starred-symbolic")
        active_group.add(self._active_row)

        # ── Tarjetas de stacks ────────────────────────────────────────────
        stacks_group = Adw.PreferencesGroup()
        stacks_group.set_title("Stacks disponibles")
        self.add(stacks_group)

        for stack in _STACKS:
            row = Adw.ActionRow()
            row.set_title(stack["title"])
            row.set_subtitle(stack["description"])
            row.set_icon_name(stack["icon"])
            row.set_activatable(True)
            row.connect("activated", self._on_stack_selected, stack["id"])

            # Metadata (costo + latencia)
            meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            meta_box.set_valign(Gtk.Align.CENTER)

            cost_lbl = Gtk.Label(label=stack["cost"])
            cost_lbl.add_css_class("caption")
            cost_lbl.add_css_class("dim-label")
            cost_lbl.set_xalign(1)
            meta_box.append(cost_lbl)

            lat_lbl = Gtk.Label(label=stack["latency"])
            lat_lbl.add_css_class("caption")
            lat_lbl.add_css_class("dim-label")
            lat_lbl.set_xalign(1)
            meta_box.append(lat_lbl)

            row.add_suffix(meta_box)

            # Badge (recomendado, familia…)
            if stack["badge"]:
                badge = Gtk.Label(label=stack["badge"])
                badge.add_css_class("pill")
                badge.add_css_class("accent")
                badge.set_valign(Gtk.Align.CENTER)
                row.add_suffix(badge)

            # Checkmark si está activo
            if stack["id"] == self._active_stack:
                check = Gtk.Image.new_from_icon_name("object-select-symbolic")
                check.set_valign(Gtk.Align.CENTER)
                row.add_suffix(check)
                self._stack_rows[stack["id"]] = (row, check)
            else:
                self._stack_rows[stack["id"]] = (row, None)

            stacks_group.add(row)

        # ── Cinema — asignación de personas ──────────────────────────────
        cinema_group = Adw.PreferencesGroup()
        cinema_group.set_title("Cinema — asignación de canales")
        cinema_group.set_description(
            "Cada persona recibe una pista de audio del MKV en su dispositivo"
        )
        self.add(cinema_group)

        persons = [
            ("Papa",  "Audífonos JBL Vive Buds (Bluetooth A2DP)"),
            ("Mamá",  "Jack 3.5 mm (EQ vocal clarity activo)"),
            ("Hija",  "Audífonos BT adicionales"),
        ]
        for person, hint in persons:
            p_row = Adw.ActionRow()
            p_row.set_title(person)
            p_row.set_subtitle(hint)
            track_model = Gtk.StringList.new(
                ["─ Sin asignar", "Pista 1 (original)", "Pista 2 (doblaje)",
                 "Pista 3 (comentarios)", "Pista 4"]
            )
            track_dd = Gtk.DropDown(model=track_model)
            track_dd.set_valign(Gtk.Align.CENTER)
            p_row.add_suffix(track_dd)
            p_row.set_activatable_widget(track_dd)
            cinema_group.add(p_row)

    # ── Callbacks ────────────────────────────────────────────────────────

    def _on_stack_selected(self, _row: Adw.ActionRow, stack_id: str) -> None:
        if stack_id == self._active_stack:
            return

        # Quitar checkmark del anterior
        if self._active_stack in self._stack_rows:
            old_row, old_check = self._stack_rows[self._active_stack]
            if old_check:
                old_row.remove(old_check)
                self._stack_rows[self._active_stack] = (old_row, None)

        # Poner checkmark en el nuevo
        self._active_stack = stack_id
        new_row, _ = self._stack_rows[stack_id]
        check = Gtk.Image.new_from_icon_name("object-select-symbolic")
        check.set_valign(Gtk.Align.CENTER)
        new_row.add_suffix(check)
        self._stack_rows[stack_id] = (new_row, check)

        # Actualizar fila de "activo"
        stack_info = next(s for s in _STACKS if s["id"] == stack_id)
        self._active_row.set_title(stack_info["title"])
        self._active_row.set_subtitle(stack_info["description"])
        self._active_row.set_icon_name(stack_info["icon"])

        # Aplicar stack al pipeline si está corriendo
        from audifonospro.stacks.manager import get_stack_manager
        from audifonospro.pipeline.coordinator import get_pipeline
        get_stack_manager().activate(stack_id, get_pipeline())
