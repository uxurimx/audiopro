"""
Ventana flotante de Cinema Mode.

Features:
  - Video embebido via gtk4paintablesink (GTK4/Wayland nativo)
  - Barra de progreso con seek
  - Soporte de subtítulos (.srt/.ass/.vtt) — internos y externos
  - Descarga automática de subtítulos (via subliminal)
  - Menú contextual con click derecho
  - F → fullscreen · Space/click → play/pause · ← → → seek 10s
"""
from __future__ import annotations

import os
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gtk, Gst, GLib, Gio

GST_SECOND = 1_000_000_000


class CinemaWindow(Adw.Window):

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title("Cinema — audifonospro")
        self.set_default_size(1024, 600)
        self.set_hide_on_close(True)

        self._on_pause_cb:        callable | None = None
        self._on_stop_cb:         callable | None = None
        self._on_seek_cb:         callable | None = None  # cb(seconds: float)
        self._seeking             = False
        self._pending_seek: float | None = None   # valor 0–1 acumulado durante drag
        self._timer_id: int       = 0
        self._sub_file: str | None = None
        self._video_path: str | None = None

        self._build_ui()
        self._build_context_menu()
        self._bind_keys()

    # ── Construcción UI ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        # Header
        header = Adw.HeaderBar()
        header.set_show_back_button(False)

        self._title_lbl = Gtk.Label(label="Sin archivo")
        self._title_lbl.add_css_class("heading")
        self._title_lbl.set_ellipsize(3)
        self._title_lbl.set_max_width_chars(40)
        header.set_title_widget(self._title_lbl)

        sub_btn = Gtk.Button()
        sub_btn.set_icon_name("media-view-subtitles-symbolic")
        sub_btn.set_tooltip_text("Subtítulos")
        sub_btn.connect("clicked", self._on_sub_btn)
        header.pack_end(sub_btn)

        toolbar.add_top_bar(header)

        # ── Área central — indicador de estado ────────────────────────────
        # El video se muestra en la ventana nativa de autovideosink (Wayland).
        # Esta ventana solo controla reproducción/seek/subtítulos.
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        center.set_halign(Gtk.Align.CENTER)
        center.set_valign(Gtk.Align.CENTER)
        center.set_hexpand(True)
        center.set_vexpand(True)

        icon = Gtk.Image.new_from_icon_name("video-display-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        center.append(icon)

        self._status_lbl = Gtk.Label(label="Abre un archivo para reproducir")
        self._status_lbl.add_css_class("title-2")
        center.append(self._status_lbl)

        hint = Gtk.Label(label="El video aparece en su propia ventana Wayland")
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        center.append(hint)

        toolbar.set_content(center)

        # ── Controles inferiores ───────────────────────────────────────────
        toolbar.add_bottom_bar(self._build_controls())

    def _build_controls(self) -> Gtk.Widget:
        wrap = Adw.Bin()
        wrap.add_css_class("toolbar")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_bottom(8)
        vbox.set_margin_top(4)
        wrap.set_child(vbox)

        # ── Barra de progreso ──────────────────────────────────────────────
        prog_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._pos_label = Gtk.Label(label="0:00")
        self._pos_label.add_css_class("numeric")
        self._pos_label.set_width_chars(5)
        prog_row.append(self._pos_label)

        self._progress = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self._progress.set_range(0, 1)
        self._progress.set_draw_value(False)
        self._progress.set_hexpand(True)
        # GTK4: usar change-value (solo dispara en interacción del usuario,
        # no en set_value() programático) + GestureClick para detectar
        # inicio/fin del arrastre y activar el flag _seeking.
        self._progress.connect("change-value", self._on_change_value)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: self._start_seek())
        drag.connect("drag-end",   lambda *_: self._end_seek())
        self._progress.add_controller(drag)

        prog_row.append(self._progress)

        self._dur_label = Gtk.Label(label="0:00")
        self._dur_label.add_css_class("numeric")
        self._dur_label.set_width_chars(5)
        prog_row.append(self._dur_label)

        vbox.append(prog_row)

        # ── Botones ────────────────────────────────────────────────────────
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._play_btn = Gtk.Button(label="⏸")
        self._play_btn.add_css_class("pill")
        self._play_btn.connect("clicked", lambda *_: self._pause_toggle())
        btn_row.append(self._play_btn)

        stop_btn = Gtk.Button(label="⏹")
        stop_btn.add_css_class("pill")
        stop_btn.add_css_class("destructive-action")
        stop_btn.connect("clicked", self._on_stop)
        btn_row.append(stop_btn)

        # Saltar ±10s
        back_btn = Gtk.Button(label="⏪ 10s")
        back_btn.add_css_class("flat")
        back_btn.connect("clicked", lambda *_: self._seek_relative(-10))
        btn_row.append(back_btn)

        fwd_btn = Gtk.Button(label="10s ⏩")
        fwd_btn.add_css_class("flat")
        fwd_btn.connect("clicked", lambda *_: self._seek_relative(10))
        btn_row.append(fwd_btn)

        spacer = Gtk.Box(); spacer.set_hexpand(True)
        btn_row.append(spacer)

        # Subtítulos
        self._sub_label = Gtk.Label(label="Sin subtítulos")
        self._sub_label.add_css_class("dim-label")
        self._sub_label.add_css_class("caption")
        btn_row.append(self._sub_label)

        vbox.append(btn_row)
        return wrap

    def _build_context_menu(self) -> None:
        ag = Gio.SimpleActionGroup()
        for name, cb in [
            ("sub-open",     self._on_sub_open),
            ("sub-download", self._on_sub_download),
            ("sub-off",      self._on_sub_off),
            ("close",        lambda *_: self.set_visible(False)),
        ]:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            ag.add_action(a)
        self.insert_action_group("cinema", ag)

    def _bind_keys(self) -> None:
        from gi.repository import Gdk
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    # ── API pública ───────────────────────────────────────────────────────

    def attach_paintable(self, paintable: object) -> None:
        pass  # ya no se usa (autovideosink crea su propia ventana)

    def set_file(self, path: str) -> None:
        self._video_path = path
        name = os.path.basename(path)
        self._title_lbl.set_text(name)
        self._status_lbl.set_text(name)

    def set_on_pause(self, cb: callable) -> None:
        self._on_pause_cb = cb

    def set_on_stop(self, cb: callable) -> None:
        self._on_stop_cb = cb

    def set_on_seek(self, cb: callable) -> None:
        """cb(seconds: float) — llamado cuando el usuario hace seek."""
        self._on_seek_cb = cb

    def set_playing(self, playing: bool) -> None:
        self._play_btn.set_label("⏸" if playing else "▶")
        self._status_lbl.set_text(
            os.path.basename(self._video_path or "") if playing else
            ("Pausado" if self._video_path else "Abre un archivo para reproducir")
        )
        if playing and self._timer_id == 0:
            self._timer_id = GLib.timeout_add(400, self._update_progress)
        elif not playing and self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    # ── Progreso ──────────────────────────────────────────────────────────

    def _update_progress(self) -> bool:
        from audifonospro.cinema.gst_router import get_router
        router = get_router()
        pos = router.position_ns
        dur = router.duration_ns
        if dur > 0 and not self._seeking:
            self._progress.handler_block_by_func(self._on_change_value)
            self._progress.set_value(pos / dur)
            self._progress.handler_unblock_by_func(self._on_change_value)
            self._pos_label.set_text(_fmt_time(pos))
            self._dur_label.set_text(_fmt_time(dur))
        return True  # mantener el timer

    def _start_seek(self) -> None:
        self._seeking = True
        self._pending_seek = None

    def _end_seek(self) -> None:
        self._seeking = False
        val = self._pending_seek
        self._pending_seek = None
        if val is not None:
            from audifonospro.cinema.gst_router import get_router
            dur = get_router().duration_ns
            if dur > 0:
                pos_ns = int(max(0.0, min(1.0, val)) * dur)
                get_router().seek_ns(pos_ns)
                if self._on_seek_cb:
                    self._on_seek_cb(pos_ns / GST_SECOND)

    def _on_change_value(
        self,
        scale: Gtk.Scale,
        _scroll: Gtk.ScrollType,
        value: float,
    ) -> bool:
        """Dispara solo en interacción del usuario (no en set_value programático).

        Durante el arrastre solo actualiza la etiqueta de tiempo — el seek real
        ocurre en _end_seek() al soltar, evitando que FLUSH continuo congele el audio.
        """
        from audifonospro.cinema.gst_router import get_router
        dur = get_router().duration_ns
        if dur <= 0:
            return False

        if self._seeking:
            # Arrastre en curso: sólo actualizar etiqueta, acumular valor
            self._pos_label.set_text(_fmt_time(int(max(0.0, min(1.0, value)) * dur)))
            self._pending_seek = value
        else:
            # Click puntual o teclado: seek inmediato
            pos_ns = int(max(0.0, min(1.0, value)) * dur)
            get_router().seek_ns(pos_ns)
            if self._on_seek_cb:
                self._on_seek_cb(pos_ns / GST_SECOND)
        return False  # permitir que el scale actualice su valor visualmente

    def _seek_relative(self, seconds: int) -> None:
        from audifonospro.cinema.gst_router import get_router
        router = get_router()
        new_pos = max(0, router.position_ns + seconds * GST_SECOND)
        router.seek_ns(new_pos)
        if self._on_seek_cb:
            self._on_seek_cb(new_pos / GST_SECOND)

    # ── Callbacks controles ───────────────────────────────────────────────

    def _pause_toggle(self) -> None:
        if self._on_pause_cb:
            self._on_pause_cb()

    def _on_stop(self, *_: object) -> None:
        self.set_visible(False)
        if self._on_stop_cb:
            self._on_stop_cb()

    def _on_key(
        self,
        _ctrl: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: object,
    ) -> bool:
        from gi.repository import Gdk
        match keyval:
            case Gdk.KEY_space:
                self._pause_toggle()
            case Gdk.KEY_Escape:
                self.set_visible(False)
            case Gdk.KEY_Left:
                self._seek_relative(-10)
            case Gdk.KEY_Right:
                self._seek_relative(10)
            case _:
                return False
        return True

    # ── Subtítulos UI ─────────────────────────────────────────────────────

    def _on_sub_btn(self, *_: object) -> None:
        self._on_sub_open(None, None)

    def _on_sub_open(self, _action: object, _param: object) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Seleccionar archivo de subtítulos")
        f = Gtk.FileFilter()
        f.set_name("Subtítulos (.srt, .ass, .vtt)")
        for pat in ["*.srt", "*.ass", "*.ssa", "*.vtt", "*.sub"]:
            f.add_pattern(pat)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dialog.set_filters(store)
        dialog.open(self, None, self._on_sub_file_chosen)

    def _on_sub_file_chosen(self, dialog: Gtk.FileDialog, result: object) -> None:
        try:
            gfile = dialog.open_finish(result)
            path = gfile.get_path()
            self._load_subtitle(path)
        except Exception:
            pass

    def _on_sub_off(self, *_: object) -> None:
        from audifonospro.cinema.gst_router import get_router
        get_router().disable_subtitles()
        self._sub_label.set_text("Sin subtítulos")
        self._sub_file = None

    def _on_sub_download(self, *_: object) -> None:
        if not self._video_path:
            return
        path = self._video_path
        self._sub_label.set_text("Descargando subtítulos…")
        threading.Thread(
            target=self._download_subtitles, args=(path,), daemon=True
        ).start()

    def _download_subtitles(self, path: str) -> None:
        """Descarga subtítulos automáticamente via OpenSubtitles XML-RPC (stdlib, sin pip)."""
        try:
            from audifonospro.cinema.subtitles import auto_download
            saved = auto_download(path, languages=["es", "en"])
            GLib.idle_add(self._load_subtitle, saved)
        except Exception as exc:
            GLib.idle_add(self._sub_label.set_text, f"Sin subtítulos: {exc}")

    def _load_subtitle(self, path: str) -> None:
        """Carga un archivo de subtítulos en el pipeline GStreamer."""
        from audifonospro.cinema.gst_router import get_router
        self._sub_file = path
        get_router().load_subtitle(path)
        self._sub_label.set_text(f"Subs: {os.path.basename(path)}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(ns: int) -> str:
    s = ns // GST_SECOND
    return f"{s // 60}:{s % 60:02d}"
