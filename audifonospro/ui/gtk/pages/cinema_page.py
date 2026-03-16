"""
CinemaPage — modo cinema con routing multi-pista.

Carga un MKV/MP4 y asigna cada pista de audio a un dispositivo distinto
mediante GStreamer (hot-swap en tiempo real).
"""
from __future__ import annotations

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from audifonospro.config import Settings


class CinemaPage(Adw.PreferencesPage):
    def __init__(self, settings: Settings, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.settings = settings

        self.set_title("Cinema")
        self.set_icon_name("video-display-symbolic")

        self._cinema_path: str | None = None
        self._cinema_win = None
        self._video_sink = None
        self._cinema_track_rows: list[Adw.ActionRow] = []

        # ── Instrucción ───────────────────────────────────────────────────
        info_group = Adw.PreferencesGroup()
        self.add(info_group)
        info_row = Adw.ActionRow()
        info_row.set_title("¿Cómo usar?")
        info_row.set_subtitle(
            "Abre un MKV o MP4 con múltiples pistas de audio. "
            "Asigna cada pista a un dispositivo — cada persona escucha su idioma."
        )
        info_row.set_icon_name("video-display-symbolic")
        info_group.add(info_row)

        # ── Sección principal ─────────────────────────────────────────────
        self._cinema_group = Adw.PreferencesGroup()
        self._cinema_group.set_title("Archivo y pistas")
        self.add(self._cinema_group)
        self._build_cinema_row()

    # ── Construcción UI ───────────────────────────────────────────────────

    def _build_cinema_row(self) -> None:
        # Fila: selector de archivo
        file_row = Adw.ActionRow()
        file_row.set_title("Archivo de video")
        self._mkv_path_label = Gtk.Label(label="Ningún archivo seleccionado")
        self._mkv_path_label.add_css_class("dim-label")
        self._mkv_path_label.set_ellipsize(3)
        self._mkv_path_label.set_max_width_chars(32)
        self._mkv_path_label.set_valign(Gtk.Align.CENTER)
        file_row.add_suffix(self._mkv_path_label)

        open_btn = Gtk.Button(label="Abrir…")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.add_css_class("suggested-action")
        open_btn.connect("clicked", self._on_open_mkv)
        file_row.add_suffix(open_btn)
        self._cinema_group.add(file_row)

        # Fila: controles de reproducción (ocultos hasta abrir archivo)
        ctrl_row = Adw.ActionRow()
        ctrl_row.set_title("Reproducción")
        ctrl_row.set_visible(False)
        self._cinema_ctrl_row = ctrl_row

        ctrl_box = Gtk.Box(spacing=8)
        ctrl_box.set_valign(Gtk.Align.CENTER)

        self._cinema_play_btn = Gtk.Button(label="▶  Reproducir")
        self._cinema_play_btn.add_css_class("suggested-action")
        self._cinema_play_btn.connect("clicked", self._on_cinema_play)
        ctrl_box.append(self._cinema_play_btn)

        stop_btn = Gtk.Button(label="⏹")
        stop_btn.add_css_class("destructive-action")
        stop_btn.connect("clicked", self._on_cinema_stop)
        ctrl_box.append(stop_btn)

        ctrl_row.add_suffix(ctrl_box)
        self._cinema_group.add(ctrl_row)

    # ── Callbacks de archivo / pistas ─────────────────────────────────────

    def _on_open_mkv(self, _btn: Gtk.Button) -> None:
        from gi.repository import Gio
        dialog = Gtk.FileDialog()
        dialog.set_title("Abrir archivo de video")

        f = Gtk.FileFilter()
        f.set_name("Video (MKV, MP4, AVI)")
        for pat in ["*.mkv", "*.mp4", "*.avi", "*.mov", "*.webm"]:
            f.add_pattern(pat)

        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dialog.set_filters(store)
        dialog.open(self.get_root(), None, self._on_mkv_chosen)

    def _on_mkv_chosen(self, dialog: Gtk.FileDialog, result: object) -> None:
        try:
            gfile = dialog.open_finish(result)
            path = gfile.get_path()
        except Exception:
            return

        import os
        self._mkv_path_label.set_text(os.path.basename(path))
        self._cinema_path = path
        self._mkv_path_label.set_tooltip_text(path)
        self._cinema_ctrl_row.set_subtitle("Detectando pistas…")

        threading.Thread(target=self._discover_tracks, args=(path,), daemon=True).start()

    def _discover_tracks(self, path: str) -> None:
        try:
            from audifonospro.cinema.gst_router import get_router
            tracks = get_router().discover(path)
        except Exception as exc:
            GLib.idle_add(self._on_tracks_error, str(exc))
            return
        GLib.idle_add(self._on_tracks_found, tracks)

    def _on_tracks_error(self, msg: str) -> bool:
        self._mkv_path_label.set_text(f"Error: {msg[:40]}")
        return False

    def _on_tracks_found(self, tracks: list) -> bool:
        for row in self._cinema_track_rows:
            self._cinema_group.remove(row)
        self._cinema_track_rows.clear()

        from audifonospro.audio.routing import list_sinks
        sinks = list_sinks()
        sink_labels = ["─ Sin audio"] + [
            f"{s['description'] or s['name']}  [{s['state']}]" for s in sinks
        ]
        sink_names = [None] + [s["name"] for s in sinks]

        for track in tracks:
            row = Adw.ActionRow()
            row.set_title(track.label)
            row.set_subtitle(f"Pista {track.index}  ·  {track.sample_rate // 1000} kHz")

            model = Gtk.StringList.new(sink_labels)
            dd = Gtk.DropDown(model=model)
            dd.set_valign(Gtk.Align.CENTER)
            dd.connect("notify::selected", self._on_track_sink_selected,
                       track.index, sink_names)

            if track.index == 0 and len(sink_names) > 1:
                dd.set_selected(1)
            elif track.index == 1 and len(sink_names) > 2:
                dd.set_selected(2)
            else:
                dd.set_selected(0)

            row.add_suffix(dd)
            row.set_activatable_widget(dd)
            self._cinema_group.add(row)
            self._cinema_track_rows.append(row)

        self._cinema_ctrl_row.set_visible(True)
        self._cinema_ctrl_row.set_subtitle(f"{len(tracks)} pista(s) de audio detectadas")
        return False

    def _on_track_sink_selected(
        self, dd: Gtk.DropDown, _param: object, track_idx: int, sink_names: list
    ) -> None:
        idx = dd.get_selected()
        if idx < len(sink_names):
            from audifonospro.cinema.gst_router import get_router
            get_router().assign(track_idx, sink_names[idx])

    # ── Callbacks de reproducción ─────────────────────────────────────────

    def _get_cinema_window(self):
        if self._cinema_win is None:
            from audifonospro.ui.gtk.cinema_window import CinemaWindow
            self._cinema_win = CinemaWindow(application=self.get_root().get_application())
            self._cinema_win.set_on_pause(self._on_cinema_pause_from_window)
            self._cinema_win.set_on_stop(self._on_cinema_stop_from_window)
        return self._cinema_win

    def _on_cinema_play(self, _btn: Gtk.Button) -> None:
        from audifonospro.cinema.gst_router import get_router
        router = get_router()

        if router.is_playing:
            router.pause()
            self._cinema_play_btn.set_label("▶  Reanudar")
            if self._cinema_win:
                self._cinema_win.set_playing(False)
        elif self._cinema_path:
            import gi as _gi; _gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            state = router._pipeline.get_state(0).state if router._pipeline else None
            if state == Gst.State.PAUSED:
                router.pause()
                self._cinema_play_btn.set_label("⏸  Pausar")
                if self._cinema_win:
                    self._cinema_win.set_playing(True)
            else:
                router.set_on_eos(self._on_cinema_eos)
                router.set_on_error(self._on_cinema_error)

                vsink, paintable = router.prepare_video_sink()

                win = self._get_cinema_window()
                win.set_file(self._cinema_path)
                win.attach_paintable(paintable)
                win.present()

                ok, _ = router.play(
                    self._cinema_path,
                    show_video=(vsink is not None),
                    video_sink=vsink,
                )
                if ok:
                    self._cinema_play_btn.set_label("⏸  Pausar")
                    win.set_playing(True)
                else:
                    self._cinema_ctrl_row.set_subtitle(
                        "Asigna al menos una pista antes de reproducir"
                    )

    def _on_cinema_stop(self, _btn: object) -> None:
        from audifonospro.cinema.gst_router import get_router
        get_router().stop()
        self._cinema_play_btn.set_label("▶  Reproducir")
        if self._cinema_win:
            self._cinema_win.set_visible(False)
            self._cinema_win.set_playing(False)

    def _on_cinema_pause_from_window(self) -> None:
        from audifonospro.cinema.gst_router import get_router
        router = get_router()
        router.pause()
        is_playing = router.is_playing
        self._cinema_play_btn.set_label("⏸  Pausar" if is_playing else "▶  Reanudar")
        if self._cinema_win:
            self._cinema_win.set_playing(is_playing)

    def _on_cinema_stop_from_window(self) -> None:
        self._on_cinema_stop(None)

    def _on_cinema_eos(self) -> None:
        self._cinema_play_btn.set_label("▶  Reproducir")
        self._cinema_ctrl_row.set_subtitle("Reproducción finalizada")
        if self._cinema_win:
            self._cinema_win.set_visible(False)
            self._cinema_win.set_playing(False)

    def _on_cinema_error(self, msg: str) -> None:
        self._cinema_play_btn.set_label("▶  Reproducir")
        self._cinema_ctrl_row.set_subtitle(f"Error: {msg[:60]}")
