"""
Ventana de Cinema Mode.

Features:
  - Video embebido via gtk4paintablesink (GTK4/Wayland nativo)
  - Barra de progreso con seek (drag → seek al soltar)
  - Pantalla completa: botón ⛶ en header + tecla F + doble clic en video
  - Auto-ocultar controles en fullscreen (reaparecen con movimiento del ratón)
  - Clic simple en video → pause/reanudar  · Doble clic → fullscreen
  - Subtítulos renderizados como overlay GTK4 sobre el video:
      · Cargar archivo local (.srt / .vtt / .ass)
      · Buscar y descargar desde OpenSubtitles (sin pip, xmlrpc stdlib)
      · Desactivar
  - Teclado: Space → pausa · F → fullscreen · ← → → seek 10s · Esc → cerrar
"""
from __future__ import annotations

import os
import re
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gtk, Gst, GLib, Gio, Gdk

GST_SECOND = 1_000_000_000

# ── CSS ────────────────────────────────────────────────────────────────────────
_CSS = b"""
.cinema-sub {
    color: white;
    font-size: 22px;
    font-weight: bold;
    -gtk-text-shadow: 1px 1px 0 black, -1px -1px 0 black,
                       1px -1px 0 black, -1px  1px 0 black;
    background-color: rgba(0,0,0,0.55);
    padding: 4px 16px;
    border-radius: 6px;
}
.fs-controls {
    background-color: rgba(0,0,0,0.72);
}
"""


class CinemaWindow(Adw.Window):

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.set_title("Cinema — audifonospro")
        self.set_default_size(1024, 600)
        self.set_hide_on_close(True)

        self._on_pause_cb:    callable | None = None
        self._on_stop_cb:     callable | None = None
        self._on_seek_cb:     callable | None = None
        self._seeking         = False
        self._pending_seek:   float | None = None
        self._timer_id:       int = 0
        self._video_path:     str | None = None

        # Subtítulos — lista de (start_ns, end_ns, text)
        self._subtitles:      list[tuple[int, int, str]] = []
        self._sub_file:       str | None = None

        # Fullscreen auto-hide
        self._fs_hide_timer:  int = 0
        self._last_mouse_pos: tuple[float, float] = (-999.0, -999.0)

        # Delay para distinguir clic simple de doble clic
        self._click_pending_id: int = 0

        self._apply_css()
        self._build_ui()
        self._bind_keys()

    # ── CSS ───────────────────────────────────────────────────────────────

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # ── Construcción UI ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._toolbar = Adw.ToolbarView()
        self.set_content(self._toolbar)

        # ── Header ────────────────────────────────────────────────────────
        self._header = Adw.HeaderBar()
        self._header.set_show_back_button(False)

        self._title_lbl = Gtk.Label(label="Sin archivo")
        self._title_lbl.add_css_class("heading")
        self._title_lbl.set_ellipsize(3)
        self._title_lbl.set_max_width_chars(40)
        self._header.set_title_widget(self._title_lbl)

        self._header.pack_end(self._build_sub_menu_btn())

        self._fs_btn = Gtk.Button()
        self._fs_btn.set_icon_name("view-fullscreen-symbolic")
        self._fs_btn.set_tooltip_text("Pantalla completa (F)")
        self._fs_btn.connect("clicked", lambda *_: self._toggle_fullscreen())
        self._header.pack_end(self._fs_btn)

        self._toolbar.add_top_bar(self._header)

        # ── Área central — video + overlays ───────────────────────────────
        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)

        self._video_picture = Gtk.Picture()
        self._video_picture.set_keep_aspect_ratio(True)
        self._video_picture.set_hexpand(True)
        self._video_picture.set_vexpand(True)
        overlay.set_child(self._video_picture)

        # Placeholder
        self._status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._status_box.set_halign(Gtk.Align.CENTER)
        self._status_box.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("video-display-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        self._status_box.append(icon)
        self._status_lbl = Gtk.Label(label="Abre un archivo para reproducir")
        self._status_lbl.add_css_class("title-2")
        self._status_box.append(self._status_lbl)
        overlay.add_overlay(self._status_box)

        # Subtítulos
        self._sub_lbl = Gtk.Label(label="")
        self._sub_lbl.add_css_class("cinema-sub")
        self._sub_lbl.set_halign(Gtk.Align.CENTER)
        self._sub_lbl.set_valign(Gtk.Align.END)
        self._sub_lbl.set_margin_bottom(32)
        self._sub_lbl.set_wrap(True)
        self._sub_lbl.set_max_width_chars(72)
        self._sub_lbl.set_visible(False)
        overlay.add_overlay(self._sub_lbl)

        # Controles flotantes fullscreen
        self._fs_overlay_bar = self._build_fs_overlay_bar()
        overlay.add_overlay(self._fs_overlay_bar)

        self._toolbar.set_content(overlay)

        # ── Controles inferiores (visibles fuera de fullscreen) ────────────
        self._controls_bar = self._build_controls()
        self._toolbar.add_bottom_bar(self._controls_bar)

        # ── Gestos ────────────────────────────────────────────────────────

        # Clic simple → pause · Doble clic → fullscreen
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self._on_video_click)
        self._video_picture.add_controller(click)

        # Movimiento de ratón → mostrar controles en fullscreen
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_mouse_motion)
        self.add_controller(motion)

    def _build_fs_overlay_bar(self) -> Gtk.Widget:
        """Barra de controles flotante para fullscreen (aparece al mover el ratón)."""
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        bar.add_css_class("fs-controls")
        bar.set_halign(Gtk.Align.FILL)
        bar.set_valign(Gtk.Align.END)
        bar.set_visible(False)

        # Progreso
        prog_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        prog_row.set_margin_start(16); prog_row.set_margin_end(16)
        prog_row.set_margin_top(10)

        self._fs_pos_label = Gtk.Label(label="0:00")
        self._fs_pos_label.add_css_class("numeric")
        self._fs_pos_label.set_width_chars(5)
        prog_row.append(self._fs_pos_label)

        self._fs_progress = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self._fs_progress.set_range(0, 1)
        self._fs_progress.set_draw_value(False)
        self._fs_progress.set_hexpand(True)
        self._fs_progress.connect("change-value", self._on_change_value)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: self._start_seek())
        drag.connect("drag-end",   lambda *_: self._end_seek())
        self._fs_progress.add_controller(drag)
        prog_row.append(self._fs_progress)

        self._fs_dur_label = Gtk.Label(label="0:00")
        self._fs_dur_label.add_css_class("numeric")
        self._fs_dur_label.set_width_chars(5)
        prog_row.append(self._fs_dur_label)

        bar.append(prog_row)

        # Botones
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_start(16); btn_row.set_margin_end(16)
        btn_row.set_margin_top(4); btn_row.set_margin_bottom(16)

        self._fs_play_btn = Gtk.Button(label="⏸")
        self._fs_play_btn.add_css_class("pill")
        self._fs_play_btn.connect("clicked", lambda *_: self._pause_toggle())
        btn_row.append(self._fs_play_btn)

        stop_btn = Gtk.Button(label="⏹")
        stop_btn.add_css_class("pill")
        stop_btn.add_css_class("destructive-action")
        stop_btn.connect("clicked", self._on_stop)
        btn_row.append(stop_btn)

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

        exit_btn = Gtk.Button()
        exit_btn.set_icon_name("view-restore-symbolic")
        exit_btn.set_tooltip_text("Salir de pantalla completa (Esc / F)")
        exit_btn.add_css_class("flat")
        exit_btn.connect("clicked", lambda *_: self._exit_fullscreen())
        btn_row.append(exit_btn)

        bar.append(btn_row)
        return bar

    def _build_sub_menu_btn(self) -> Gtk.MenuButton:
        btn = Gtk.MenuButton()
        btn.set_icon_name("media-view-subtitles-symbolic")
        btn.set_tooltip_text("Subtítulos")

        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(8); box.set_margin_end(8)
        box.set_margin_top(8);   box.set_margin_bottom(8)

        b_local = Gtk.Button(label="Cargar archivo…")
        b_local.add_css_class("flat")
        b_local.connect("clicked", lambda *_: (popover.popdown(), self._on_sub_open()))
        box.append(b_local)

        b_dl = Gtk.Button(label="Buscar en OpenSubtitles")
        b_dl.add_css_class("flat")
        b_dl.connect("clicked", lambda *_: (popover.popdown(), self._on_sub_download()))
        box.append(b_dl)

        sep = Gtk.Separator()
        box.append(sep)

        b_off = Gtk.Button(label="Desactivar subtítulos")
        b_off.add_css_class("flat")
        b_off.connect("clicked", lambda *_: (popover.popdown(), self._on_sub_off()))
        box.append(b_off)

        popover.set_child(box)
        btn.set_popover(popover)
        return btn

    def _build_controls(self) -> Gtk.Widget:
        wrap = Adw.Bin()
        wrap.add_css_class("toolbar")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_start(12); vbox.set_margin_end(12)
        vbox.set_margin_bottom(8); vbox.set_margin_top(4)
        wrap.set_child(vbox)

        # Barra de progreso
        prog_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._pos_label = Gtk.Label(label="0:00")
        self._pos_label.add_css_class("numeric")
        self._pos_label.set_width_chars(5)
        prog_row.append(self._pos_label)

        self._progress = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self._progress.set_range(0, 1)
        self._progress.set_draw_value(False)
        self._progress.set_hexpand(True)
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

        # Botones
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

        self._sub_status = Gtk.Label(label="Sin subtítulos")
        self._sub_status.add_css_class("dim-label")
        self._sub_status.add_css_class("caption")
        btn_row.append(self._sub_status)

        vbox.append(btn_row)
        return wrap

    def _bind_keys(self) -> None:
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    # ── API pública ───────────────────────────────────────────────────────

    def attach_paintable(self, paintable: object) -> None:
        if paintable:
            self._video_picture.set_paintable(paintable)
            self._status_box.set_visible(False)
        else:
            self._video_picture.set_paintable(None)
            self._status_box.set_visible(True)

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
        self._on_seek_cb = cb

    def set_playing(self, playing: bool) -> None:
        lbl = "⏸" if playing else "▶"
        self._play_btn.set_label(lbl)
        self._fs_play_btn.set_label(lbl)
        if not self._video_picture.get_paintable():
            self._status_lbl.set_text(
                os.path.basename(self._video_path or "") if playing else
                ("Pausado" if self._video_path else "Abre un archivo para reproducir")
            )
        if playing and self._timer_id == 0:
            self._timer_id = GLib.timeout_add(200, self._update_progress)
        elif not playing and self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    # ── Pantalla completa ─────────────────────────────────────────────────

    def _toggle_fullscreen(self) -> None:
        if self.is_fullscreen():
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self) -> None:
        self._header.set_visible(False)
        self._controls_bar.set_visible(False)
        self._sub_lbl.set_margin_bottom(72)  # espacio sobre controles flotantes
        self.fullscreen()
        self._fs_btn.set_icon_name("view-restore-symbolic")
        # Mostrar controles brevemente al entrar, luego auto-ocultar
        self._last_mouse_pos = (-999.0, -999.0)
        self._show_fs_controls()

    def _exit_fullscreen(self) -> None:
        self.unfullscreen()
        self._header.set_visible(True)
        self._controls_bar.set_visible(True)
        self._fs_overlay_bar.set_visible(False)
        if self._fs_hide_timer:
            GLib.source_remove(self._fs_hide_timer)
            self._fs_hide_timer = 0
        self._sub_lbl.set_margin_bottom(32)
        self._fs_btn.set_icon_name("view-fullscreen-symbolic")
        # Restaurar cursor
        self.set_cursor(None)

    def _show_fs_controls(self) -> None:
        """Muestra controles flotantes, restaura cursor, inicia timer de 5s."""
        self._fs_overlay_bar.set_visible(True)
        self.set_cursor(None)  # cursor visible
        if self._fs_hide_timer:
            GLib.source_remove(self._fs_hide_timer)
        self._fs_hide_timer = GLib.timeout_add(5000, self._auto_hide_fs_controls)

    def _auto_hide_fs_controls(self) -> bool:
        self._fs_hide_timer = 0
        self._fs_overlay_bar.set_visible(False)
        # Ocultar cursor (Wayland: "none" cursor)
        self.set_cursor(Gdk.Cursor.new_from_name("none", None))
        return False

    # ── Gestos ────────────────────────────────────────────────────────────

    def _on_mouse_motion(
        self, _ctrl: Gtk.EventControllerMotion, x: float, y: float
    ) -> None:
        if not self.is_fullscreen():
            return
        # Filtrar eventos espurios de Wayland: solo reaccionar si el puntero
        # realmente se movió más de 3 píxeles desde la última posición registrada
        lx, ly = self._last_mouse_pos
        if abs(x - lx) < 3 and abs(y - ly) < 3:
            return
        self._last_mouse_pos = (x, y)
        self._show_fs_controls()

    def _on_video_click(
        self, _gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        if n_press == 1:
            # Esperar brevemente para ver si viene un doble clic
            if self._click_pending_id:
                GLib.source_remove(self._click_pending_id)
            self._click_pending_id = GLib.timeout_add(250, self._do_single_click)
        elif n_press == 2:
            # Cancelar el clic simple y hacer fullscreen
            if self._click_pending_id:
                GLib.source_remove(self._click_pending_id)
                self._click_pending_id = 0
            self._toggle_fullscreen()

    def _do_single_click(self) -> bool:
        self._click_pending_id = 0
        self._pause_toggle()
        return False

    # ── Progreso + subtítulos ─────────────────────────────────────────────

    def _update_progress(self) -> bool:
        from audifonospro.cinema.gst_router import get_router
        router = get_router()
        pos = router.position_ns
        dur = router.duration_ns
        if dur > 0 and not self._seeking:
            frac = pos / dur
            for scale in (self._progress, self._fs_progress):
                scale.handler_block_by_func(self._on_change_value)
                scale.set_value(frac)
                scale.handler_unblock_by_func(self._on_change_value)
            t = _fmt_time(pos)
            d = _fmt_time(dur)
            self._pos_label.set_text(t)
            self._dur_label.set_text(d)
            self._fs_pos_label.set_text(t)
            self._fs_dur_label.set_text(d)
        self._tick_subtitles(pos)
        return True

    def _tick_subtitles(self, pos_ns: int) -> None:
        if not self._subtitles:
            return
        text = ""
        for start, end, line in self._subtitles:
            if start <= pos_ns <= end:
                text = line
                break
        if text != self._sub_lbl.get_text():
            self._sub_lbl.set_text(text)
        self._sub_lbl.set_visible(bool(text))

    # ── Seek ──────────────────────────────────────────────────────────────

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
        self, _scale: Gtk.Scale, _scroll: Gtk.ScrollType, value: float,
    ) -> bool:
        from audifonospro.cinema.gst_router import get_router
        dur = get_router().duration_ns
        if dur <= 0:
            return False
        if self._seeking:
            t = _fmt_time(int(max(0.0, min(1.0, value)) * dur))
            self._pos_label.set_text(t)
            self._fs_pos_label.set_text(t)
            self._pending_seek = value
        else:
            pos_ns = int(max(0.0, min(1.0, value)) * dur)
            get_router().seek_ns(pos_ns)
            if self._on_seek_cb:
                self._on_seek_cb(pos_ns / GST_SECOND)
        return False

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
        self, _ctrl: Gtk.EventControllerKey,
        keyval: int, _keycode: int, _state: object,
    ) -> bool:
        match keyval:
            case Gdk.KEY_space:
                self._pause_toggle()
            case Gdk.KEY_f | Gdk.KEY_F:
                self._toggle_fullscreen()
            case Gdk.KEY_Escape:
                if self.is_fullscreen():
                    self._exit_fullscreen()
                else:
                    self.set_visible(False)
            case Gdk.KEY_Left:
                self._seek_relative(-10)
            case Gdk.KEY_Right:
                self._seek_relative(10)
            case _:
                return False
        return True

    # ── Subtítulos ────────────────────────────────────────────────────────

    def _on_sub_open(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Seleccionar archivo de subtítulos")
        f = Gtk.FileFilter()
        f.set_name("Subtítulos (.srt, .vtt, .ass)")
        for pat in ["*.srt", "*.vtt", "*.ass", "*.ssa", "*.sub"]:
            f.add_pattern(pat)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dialog.set_filters(store)
        dialog.open(self, None, self._on_sub_file_chosen)

    def _on_sub_file_chosen(self, dialog: Gtk.FileDialog, result: object) -> None:
        try:
            path = dialog.open_finish(result).get_path()
            self._load_subtitle(path)
        except Exception:
            pass

    def _on_sub_off(self) -> None:
        self._subtitles = []
        self._sub_file = None
        self._sub_lbl.set_text("")
        self._sub_lbl.set_visible(False)
        self._sub_status.set_text("Sin subtítulos")

    def _on_sub_download(self) -> None:
        if not self._video_path:
            return
        self._sub_status.set_text("Buscando en OpenSubtitles…")
        threading.Thread(
            target=self._download_thread, args=(self._video_path,), daemon=True
        ).start()

    def _download_thread(self, path: str) -> None:
        try:
            from audifonospro.cinema.subtitles import auto_download
            saved = auto_download(path, languages=["es", "en"])
            GLib.idle_add(self._load_subtitle, saved)
        except Exception as exc:
            GLib.idle_add(self._sub_status.set_text, f"Error: {exc}")

    def _load_subtitle(self, path: str) -> None:
        self._sub_file = path
        self._subtitles = _parse_subtitles(path)
        name = os.path.basename(path)
        n = len(self._subtitles)
        self._sub_status.set_text(f"Subs: {name} ({n} líneas)")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(ns: int) -> str:
    s = ns // GST_SECOND
    return f"{s // 60}:{s % 60:02d}"


_TS_LONG  = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)"
)
_TS_SHORT = re.compile(
    r"(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+)[,.](\d+)"
)
_TAG      = re.compile(r"<[^>]+>|\{[^}]+\}")   # HTML + ASS tags


def _read_subtitle_file(path: str) -> str:
    """
    Lee el archivo intentando varias codificaciones en orden.
    Muchos .srt en español están en latin-1/cp1252, no en UTF-8.
    """
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return open(path, encoding=enc).read()
        except (UnicodeDecodeError, LookupError):
            continue
    return open(path, encoding="latin-1").read()


def _parse_subtitles(path: str) -> list[tuple[int, int, str]]:
    """
    Parser de subtítulos para .srt, .vtt y .ass básico.
    Devuelve [(start_ns, end_ns, text), ...] ordenado por tiempo.
    """
    try:
        content = _read_subtitle_file(path)
    except Exception:
        return []

    subs: list[tuple[int, int, str]] = []
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        for i, line in enumerate(lines):
            m = _TS_LONG.search(line)
            if m:
                h1,m1,s1,ms1 = int(m[1]),int(m[2]),int(m[3]),int(m[4])
                h2,m2,s2,ms2 = int(m[5]),int(m[6]),int(m[7]),int(m[8])
            else:
                m = _TS_SHORT.search(line)
                if m:
                    h1,m1,s1,ms1 = 0,int(m[1]),int(m[2]),int(m[3])
                    h2,m2,s2,ms2 = 0,int(m[4]),int(m[5]),int(m[6])
                else:
                    continue

            start_ns = ((h1*3600 + m1*60 + s1)*1000 + ms1) * 1_000_000
            end_ns   = ((h2*3600 + m2*60 + s2)*1000 + ms2) * 1_000_000

            text = "\n".join(lines[i+1:])
            text = _TAG.sub("", text).strip()
            if text:
                subs.append((start_ns, end_ns, text))
            break

    subs.sort(key=lambda t: t[0])
    return subs
