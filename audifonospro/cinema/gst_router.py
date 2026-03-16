"""
Cinema Mode — reproductor multi-pista con GStreamer.

Sin VLC ni reproductor externo: GStreamer decodifica el MKV/MP4 y enruta
cada pista de audio a un dispositivo de salida diferente vía PipeWire/PulseAudio.

Flujo para 2 pistas:
    filesrc → matroskademux ─┬─ audio_0 → decodebin → audioconvert → pulsesink(JBL)
                              └─ audio_1 → decodebin → audioconvert → pulsesink(FX-313)
    El video se muestra en pantalla via autovideosink (opcional).

API:
    router = CinemaRouter()
    tracks = router.discover(path)    # [{index, codec, language, channels}]
    router.assign(0, "bluez_output.B4:...")  # track 0 → JBL
    router.assign(1, "bluez_output.12:...")  # track 1 → FX-313
    router.play(path)
    router.pause()
    router.stop()
    router.position_ns  # posición actual en nanosegundos
    router.duration_ns  # duración total
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import Gst, GstPbutils, GLib

Gst.init(None)


# Mapa de códigos ISO 639 → nombre en español
_LANG_NAMES: dict[str, str] = {
    # ISO 639-1 (2 letras) — lo que GStreamer devuelve normalmente
    "es": "Español",    "en": "Inglés",     "de": "Alemán",
    "fr": "Francés",    "it": "Italiano",   "pt": "Portugués",
    "ja": "Japonés",    "ko": "Coreano",    "zh": "Chino",
    "ru": "Ruso",       "ar": "Árabe",      "nl": "Holandés",
    "pl": "Polaco",     "sv": "Sueco",      "tr": "Turco",
    "hi": "Hindi",      "th": "Tailandés",  "vi": "Vietnamita",
    "uk": "Ucraniano",  "cs": "Checo",      "hu": "Húngaro",
    "ro": "Rumano",     "da": "Danés",      "fi": "Finlandés",
    "nb": "Noruego",    "hr": "Croata",     "bg": "Búlgaro",
    # ISO 639-2 (3 letras) — fallback
    "spa": "Español",   "eng": "Inglés",    "deu": "Alemán",
    "ger": "Alemán",    "fre": "Francés",   "fra": "Francés",
    "ita": "Italiano",  "por": "Portugués", "jpn": "Japonés",
    "kor": "Coreano",   "chi": "Chino",     "zho": "Chino",
    "rus": "Ruso",      "ara": "Árabe",     "dut": "Holandés",
    "nld": "Holandés",  "pol": "Polaco",    "swe": "Sueco",
    "tur": "Turco",     "hin": "Hindi",     "tha": "Tailandés",
    "vie": "Vietnamita","ukr": "Ucraniano", "ces": "Checo",
    "cze": "Checo",     "hun": "Húngaro",   "ron": "Rumano",
    "rum": "Rumano",    "dan": "Danés",     "fin": "Finlandés",
    "nor": "Noruego",   "hrv": "Croata",    "bul": "Búlgaro",
}


@dataclass
class AudioTrack:
    index: int
    codec: str       = "unknown"
    language: str    = "und"     # ISO 639-1 o 639-2
    channels: int    = 2
    sample_rate: int = 48000
    title: str       = ""        # título de la pista si el MKV lo incluye

    @property
    def language_name(self) -> str:
        """Nombre del idioma en español, o el código si es desconocido."""
        return _LANG_NAMES.get(self.language.lower(), self.language.upper()
                               if self.language not in ("und", "") else "Desconocido")

    @property
    def channel_label(self) -> str:
        return {1: "Mono", 2: "Estéreo", 6: "5.1", 8: "7.1"}.get(
            self.channels, f"{self.channels}ch"
        )

    @property
    def label(self) -> str:
        """Título amigable: 'Inglés — AAC 5.1' o 'Español (Comentarios) — AC3 Estéreo'."""
        lang = self.language_name
        title_part = f" ({self.title})" if self.title else ""
        codec_short = (self.codec
                       .replace("MPEG-4 AAC", "AAC")
                       .replace("Dolby Digital", "AC3")
                       .replace("DTS", "DTS")
                       .split(",")[0].strip())
        return f"{lang}{title_part} — {codec_short} {self.channel_label}"


def _safe_name(s: str) -> str:
    """Convierte un sink_name a nombre válido para elementos GStreamer."""
    return re.sub(r"[^a-zA-Z0-9]", "_", s)[:32]


class CinemaRouter:
    """
    Gestiona la reproducción multi-salida de un archivo de video.
    Seguro para llamar desde hilos GTK (usa GLib.idle_add internamente).
    """

    def __init__(self) -> None:
        self._pipeline: Gst.Pipeline | None = None
        # sink_name → track_index  (múltiples sinks pueden compartir la misma pista)
        self._assignments: dict[str, int] = {}
        # (track_idx, sink_name) → tee request src pad  (para hot-swap)
        self._tee_branches: dict[tuple[int, str], Gst.Pad] = {}
        self._audio_pads: dict[int, Gst.Pad] = {}
        self._on_eos: callable | None = None
        self._on_error: callable[[str], None] | None = None
        self._lock = threading.Lock()

    # ── Inspección de pistas ──────────────────────────────────────────────

    def discover(self, path: str) -> list[AudioTrack]:
        """
        Devuelve la lista de pistas de audio en el archivo.

        Usa GstPbutils.Discoverer (no bloquea la UI porque se llama
        desde un hilo worker).
        """
        uri = Gst.filename_to_uri(path)
        discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        try:
            info = discoverer.discover_uri(uri)
        except Exception as exc:
            raise RuntimeError(f"No se pudo analizar el archivo: {exc}") from exc

        tracks: list[AudioTrack] = []
        for i, stream in enumerate(info.get_audio_streams()):
            caps = stream.get_caps()
            codec = GstPbutils.pb_utils_get_codec_description(caps) or "PCM"
            tags = stream.get_tags()

            lang = "und"
            title = ""
            if tags:
                _, lang = tags.get_string(Gst.TAG_LANGUAGE_CODE)
                _, title = tags.get_string(Gst.TAG_TITLE)

            tracks.append(AudioTrack(
                index=i,
                codec=codec,
                language=lang or "und",
                channels=stream.get_channels(),
                sample_rate=stream.get_sample_rate(),
                title=title or "",
            ))

        return tracks

    # ── Asignación de pistas ──────────────────────────────────────────────

    def assign(self, sink_name: str, track_index: int | None) -> None:
        """
        Asigna un dispositivo de salida a una pista de audio.
        Múltiples dispositivos pueden escuchar la misma pista.
        track_index=None → silenciar ese dispositivo.
        """
        with self._lock:
            if track_index is not None:
                self._assignments[sink_name] = track_index
            else:
                self._assignments.pop(sink_name, None)
        if self._pipeline:
            threading.Thread(
                target=self._hot_swap_device,
                args=(sink_name, track_index),
                daemon=True,
            ).start()

    def clear_assignments(self) -> None:
        with self._lock:
            self._assignments.clear()

    def set_on_eos(self, callback: callable) -> None:
        self._on_eos = callback

    def set_on_error(self, callback: callable) -> None:
        self._on_error = callback

    # ── Reproducción ─────────────────────────────────────────────────────

    def prepare_video_sink(self) -> tuple[object | None, object | None]:
        """
        Crea gtk4paintablesink y devuelve (sink, paintable).

        Llamar desde el hilo GTK principal antes de play().
        Si el elemento no está disponible, devuelve (None, None).
        """
        sink = Gst.ElementFactory.make("gtk4paintablesink")
        if not sink:
            return None, None
        paintable = sink.get_property("paintable")
        return sink, paintable

    def play(
        self,
        path: str,
        show_video: bool = True,
        video_sink: object | None = None,
    ) -> tuple[bool, None]:
        """
        Construye el pipeline y arranca la reproducción.

        show_video : si True y video_sink está presente, embebe el video.
        video_sink : gtk4paintablesink creado por prepare_video_sink().
                     DEBE haberse creado en el hilo GTK principal.
                     DEBE añadirse al pipeline ANTES de set_state(PLAYING).
        """
        self.stop()

        with self._lock:
            assignments = dict(self._assignments)

        if not assignments:
            return False, None

        uri = Gst.filename_to_uri(path)
        pipeline = Gst.Pipeline.new("cinema")
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        # Fuente
        src = Gst.ElementFactory.make("uridecodebin", "src")
        src.set_property("uri", uri)
        pipeline.add(src)

        # Video: añadir el sink al pipeline ANTES de PLAYING
        self._video_queue: Gst.Element | None = None
        self._video_pipeline_ref = pipeline
        if show_video and video_sink:
            pipeline.add(video_sink)
            self._video_sink: Gst.Element | None = video_sink
        else:
            self._video_sink = None

        # Subtítulos: si hay archivo pre-cargado
        self._sub_pipeline_ready = False
        self._pending_sub_file = getattr(self, "_queued_sub_file", None)

        self._audio_pads = {}
        self._tee_branches = {}
        src.connect("pad-added", self._on_pad_added, pipeline, assignments)

        self._pipeline = pipeline
        pipeline.set_state(Gst.State.PLAYING)
        return True, None

    def pause(self) -> None:
        if self._pipeline:
            state = self._pipeline.get_state(0).state
            if state == Gst.State.PLAYING:
                self._pipeline.set_state(Gst.State.PAUSED)
            else:
                self._pipeline.set_state(Gst.State.PLAYING)

    def stop(self) -> None:
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

    @property
    def is_playing(self) -> bool:
        if not self._pipeline:
            return False
        return self._pipeline.get_state(0).state == Gst.State.PLAYING

    @property
    def position_ns(self) -> int:
        if not self._pipeline:
            return 0
        ok, pos = self._pipeline.query_position(Gst.Format.TIME)
        return pos if ok else 0

    @property
    def duration_ns(self) -> int:
        if not self._pipeline:
            return 0
        ok, dur = self._pipeline.query_duration(Gst.Format.TIME)
        return dur if ok else 0

    def seek_ns(self, position_ns: int) -> None:
        if self._pipeline:
            self._pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                position_ns,
            )

    # ── Internals ─────────────────────────────────────────────────────────

    def load_subtitle(self, path: str) -> None:
        """Carga un archivo .srt/.ass externo en el pipeline activo."""
        self._queued_sub_file = path
        if self._pipeline and self._video_sink:
            GLib.idle_add(self._apply_subtitle_main, path)

    def disable_subtitles(self) -> None:
        self._queued_sub_file = None
        # Silenciar textoverlay si existe
        if self._pipeline:
            overlay = self._pipeline.get_by_name("textoverlay")
            if overlay:
                overlay.set_property("text", "")
                overlay.set_property("silent", True)

    def _apply_subtitle_main(self, path: str) -> bool:
        """Activa el textoverlay con el archivo de subtítulos (hilo GTK)."""
        if not self._pipeline:
            return False
        overlay = self._pipeline.get_by_name("textoverlay")
        if overlay:
            overlay.set_property("silent", False)
        # La pista de texto se conecta dinámicamente en _on_pad_added
        # Aquí guardamos el path para la próxima reproducción
        return False

    def _hot_swap_device(self, sink_name: str, track_idx: int | None) -> None:
        """
        Mueve un dispositivo de salida a otra pista en tiempo real.

        Estrategia segura: pausar → eliminar rama actual → añadir nueva → reanudar.
        """
        pipeline = self._pipeline
        if not pipeline:
            return

        was_playing = pipeline.get_state(0).state == Gst.State.PLAYING
        pipeline.set_state(Gst.State.PAUSED)
        pipeline.get_state(3 * Gst.SECOND)

        # Eliminar de la pista actual si existe
        current_keys = [(t, s) for (t, s) in list(self._tee_branches.keys())
                        if s == sink_name]
        for old_track_idx, _ in current_keys:
            tee = pipeline.get_by_name(f"tee_{old_track_idx}")
            if tee:
                self._remove_tee_branch(pipeline, tee, old_track_idx, sink_name)

        # Añadir a la nueva pista
        if track_idx is not None:
            tee = pipeline.get_by_name(f"tee_{track_idx}")
            if tee:
                self._add_tee_branch(pipeline, tee, track_idx, sink_name)

        if was_playing:
            pipeline.set_state(Gst.State.PLAYING)

    def _on_pad_added(
        self,
        src: Gst.Element,
        pad: Gst.Pad,
        pipeline: Gst.Pipeline,
        assignments: dict[int, str],
    ) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        if not caps or caps.is_empty():
            return
        struct = caps.get_structure(0)
        name = struct.get_name()

        if name.startswith("audio/"):
            self._link_audio_pad(pad, pipeline, assignments)
        elif name.startswith("video/") and getattr(self, "_video_sink", None):
            # El vsink ya está en el pipeline; construimos el resto de la cadena
            # en el hilo GTK principal (gtk4paintablesink requiere main thread).
            GLib.idle_add(self._connect_video_pad_main, pad)
        elif name.startswith("text/"):
            GLib.idle_add(self._link_text_pad_main, pad, pipeline)

    def _link_audio_pad(
        self,
        pad: Gst.Pad,
        pipeline: Gst.Pipeline,
        assignments: dict[str, int],
    ) -> None:
        # assignments = {sink_name: track_idx}
        audio_idx = len(self._audio_pads)
        self._audio_pads[audio_idx] = pad

        # Cadena base: queue → audioconvert → audioresample → tee
        queue    = Gst.ElementFactory.make("queue",        f"q_{audio_idx}")
        convert  = Gst.ElementFactory.make("audioconvert", f"conv_{audio_idx}")
        resample = Gst.ElementFactory.make("audioresample", f"res_{audio_idx}")
        tee      = Gst.ElementFactory.make("tee",           f"tee_{audio_idx}")
        tee.set_property("allow-not-linked", True)
        queue.set_property("max-size-buffers", 200)

        for elem in [queue, convert, resample, tee]:
            pipeline.add(elem)

        queue.link(convert)
        convert.link(resample)
        resample.link(tee)

        for elem in [queue, convert, resample, tee]:
            elem.sync_state_with_parent()

        # Crear una rama (queue branch → pulsesink) por cada sink asignado a esta pista
        sinks_for_track = [s for s, t in assignments.items() if t == audio_idx]
        if sinks_for_track:
            for sink_name in sinks_for_track:
                self._add_tee_branch(pipeline, tee, audio_idx, sink_name)
        else:
            # Sin sinks: fakesink para que el tee no bloquee el pipeline
            fake = Gst.ElementFactory.make("fakesink", f"fake_{audio_idx}")
            fake.set_property("sync", False)
            fake.set_property("async", False)
            pipeline.add(fake)
            tee_src = tee.get_request_pad("src_%u")
            tee_src.link(fake.get_static_pad("sink"))
            fake.sync_state_with_parent()

        # Conectar el pad de uridecodebin al inicio de la cadena
        pad.link(queue.get_static_pad("sink"))

    def _add_tee_branch(
        self,
        pipeline: Gst.Pipeline,
        tee: Gst.Element,
        track_idx: int,
        sink_name: str,
    ) -> None:
        """Añade una rama queue→pulsesink al tee de una pista."""
        safe = _safe_name(sink_name)
        qb  = Gst.ElementFactory.make("queue",    f"qb_{track_idx}_{safe}")
        out = Gst.ElementFactory.make("pulsesink", f"out_{safe}")
        if not qb or not out:
            return
        out.set_property("device", sink_name)
        pipeline.add(qb)
        pipeline.add(out)

        tee_src = tee.get_request_pad("src_%u")
        tee_src.link(qb.get_static_pad("sink"))
        qb.link(out)
        qb.sync_state_with_parent()
        out.sync_state_with_parent()

        with self._lock:
            self._tee_branches[(track_idx, sink_name)] = tee_src

    def _remove_tee_branch(
        self,
        pipeline: Gst.Pipeline,
        tee: Gst.Element,
        track_idx: int,
        sink_name: str,
    ) -> None:
        """Elimina la rama de un sink del tee (pipeline debe estar en PAUSED)."""
        safe = _safe_name(sink_name)
        tee_src = self._tee_branches.pop((track_idx, sink_name), None)
        qb  = pipeline.get_by_name(f"qb_{track_idx}_{safe}")
        out = pipeline.get_by_name(f"out_{safe}")

        if tee_src and qb:
            tee_src.unlink(qb.get_static_pad("sink"))
            tee.release_request_pad(tee_src)
        for elem in [qb, out]:
            if elem:
                elem.set_state(Gst.State.NULL)
                pipeline.remove(elem)

    def _connect_video_pad_main(self, pad: Gst.Pad) -> bool:
        """
        Construye y conecta la cadena de video dinámicamente en el hilo GTK.

        Orden correcto para pipelines dinámicos (GStreamer best practice):
          1. add elements to pipeline
          2. link internal chain (queue → videoconvert → vsink)
          3. sync_state_with_parent  (elementos pasan a PLAYING)
          4. link upstream pad LAST  (empieza el flujo de datos)

        Sin glupload/glcolorconvert: gtk4paintablesink 1.22+ acepta video/x-raw
        y hace el upload GL internamente — más sencillo y evita errores de
        formato YUV con DRM modifiers en Intel.
        """
        vsink = self._video_sink
        pipeline = getattr(self, "_video_pipeline_ref", None)
        if not vsink or not pipeline:
            return False

        queue = Gst.ElementFactory.make("queue",        "vq")
        vconv = Gst.ElementFactory.make("videoconvert", "vconv")

        # 1. Añadir al pipeline
        pipeline.add(queue)
        pipeline.add(vconv)

        # 2. Enlazar la cadena interna
        queue.link(vconv)
        vconv.link(vsink)

        # 3. Sincronizar estados (los nuevos elementos pasan a PLAYING)
        queue.sync_state_with_parent()
        vconv.sync_state_with_parent()
        vsink.sync_state_with_parent()

        # 4. Conectar el pad upstream (inicia el flujo de datos)
        sink_pad = queue.get_static_pad("sink")
        if sink_pad and not sink_pad.is_linked():
            pad.link(sink_pad)

        self._video_queue = queue
        return False  # GLib.idle_add no repite

    def _link_text_pad_main(self, pad: Gst.Pad, pipeline: Gst.Pipeline) -> bool:
        """Conecta pista de texto (subtítulos embebidos) a textoverlay."""
        overlay = pipeline.get_by_name("textoverlay")
        if overlay:
            text_sink = overlay.get_static_pad("text_sink")
            if text_sink and not text_sink.is_linked():
                pad.link(text_sink)
        return False

    def _on_bus_message(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        if msg.type == Gst.MessageType.EOS:
            self.stop()
            if self._on_eos:
                GLib.idle_add(self._on_eos)
        elif msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            self.stop()
            if self._on_error:
                GLib.idle_add(self._on_error, f"{err.message} — {debug}")


# Singleton
_router: CinemaRouter | None = None


def get_router() -> CinemaRouter:
    global _router
    if _router is None:
        _router = CinemaRouter()
    return _router
