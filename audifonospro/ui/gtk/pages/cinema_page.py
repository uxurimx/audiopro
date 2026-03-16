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
        self._tracks: list = []          # pistas descubiertas en el archivo
        self._device_rows: list[Adw.ActionRow] = []   # filas de dispositivos

        # ── Sección: archivo ──────────────────────────────────────────────
        self._cinema_group = Adw.PreferencesGroup()
        self._cinema_group.set_title("Archivo")
        self.add(self._cinema_group)
        self._build_cinema_row()

        # ── Sección: dispositivos (se rellena al abrir archivo) ───────────
        self._dev_group = Adw.PreferencesGroup()
        self._dev_group.set_title("Dispositivos")
        self._dev_group.set_description(
            "Cada dispositivo conectado puede escuchar una pista diferente. "
            "Varios pueden compartir la misma pista."
        )
        self._dev_group.set_visible(False)
        self._dev_group.set_header_suffix(self._build_refresh_btn())
        self.add(self._dev_group)

        self._no_devices_row = Adw.ActionRow()
        self._no_devices_row.set_title("Sin dispositivos de audio")
        self._no_devices_row.set_subtitle("Conecta un altavoz o audífono primero")
        self._dev_group.add(self._no_devices_row)

    # ── Construcción UI ───────────────────────────────────────────────────

    def _build_refresh_btn(self) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_icon_name("view-refresh-symbolic")
        btn.set_tooltip_text("Actualizar dispositivos")
        btn.add_css_class("flat")
        btn.connect("clicked", self._on_refresh_devices)
        return btn

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
        self._tracks = tracks
        n = len(tracks)
        self._cinema_ctrl_row.set_visible(True)
        self._cinema_ctrl_row.set_subtitle(f"{n} pista(s) de audio detectadas")
        self._dev_group.set_visible(True)
        self._populate_device_rows()
        return False

    def _populate_device_rows(self) -> None:
        """Construye una fila por cada sink disponible con selector de pista."""
        # Limpiar filas anteriores (excepto _no_devices_row)
        for row in self._device_rows:
            self._dev_group.remove(row)
        self._device_rows.clear()

        from audifonospro.audio.routing import list_sinks
        sinks = list_sinks()

        self._no_devices_row.set_visible(len(sinks) == 0)

        # Opciones de pista para el dropdown
        track_labels = ["Sin audio"] + [
            f"Pista {t.index} — {t.label}" for t in self._tracks
        ]
        # índice 0 = sin audio, índice i+1 = pista i

        for i, sink in enumerate(sinks):
            row = Adw.ActionRow()
            row.set_title(sink.get("description") or sink["name"])
            row.set_subtitle(sink["name"])
            row.set_icon_name("audio-speakers-symbolic")

            model = Gtk.StringList.new(track_labels)
            dd = Gtk.DropDown(model=model)
            dd.set_valign(Gtk.Align.CENTER)
            dd.set_tooltip_text("Pista de audio que escucha este dispositivo")

            # Asignación inicial: repartir pistas disponibles en orden
            if self._tracks and i < len(self._tracks):
                dd.set_selected(i + 1)   # pista i (saltando "Sin audio")
            else:
                dd.set_selected(0)

            dd.connect("notify::selected", self._on_device_track_selected,
                       sink["name"])
            row.add_suffix(dd)
            row.set_activatable_widget(dd)
            self._dev_group.add(row)
            self._device_rows.append(row)

            # Registrar asignación inicial en el router
            if self._tracks and i < len(self._tracks):
                self._assign(sink["name"], self._tracks[i].index)

    def _on_device_track_selected(
        self, dd: Gtk.DropDown, _param: object, sink_name: str
    ) -> None:
        idx = dd.get_selected()
        if idx == 0:
            self._assign(sink_name, None)
        else:
            track_idx = idx - 1   # offset por "Sin audio"
            if track_idx < len(self._tracks):
                self._assign(sink_name, self._tracks[track_idx].index)

    def _on_refresh_devices(self, _btn: Gtk.Button | None = None) -> None:
        if self._tracks:
            self._populate_device_rows()

    @staticmethod
    def _assign(sink_name: str, track_idx: int | None) -> None:
        try:
            from audifonospro.cinema.gst_router import get_router
            get_router().assign(sink_name, track_idx)
        except Exception:
            pass

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
