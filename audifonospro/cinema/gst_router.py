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

import threading
from dataclasses import dataclass, field

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import Gst, GstPbutils, GLib

Gst.init(None)


@dataclass
class AudioTrack:
    index: int
    codec: str       = "unknown"
    language: str    = "und"     # ISO 639-2
    channels: int    = 2
    sample_rate: int = 48000
    title: str       = ""        # título de la pista si el MKV lo incluye

    @property
    def label(self) -> str:
        lang = self.language if self.language != "und" else "─"
        return f"Pista {self.index}  [{lang}]  {self.codec}  {self.channels}ch"


class CinemaRouter:
    """
    Gestiona la reproducción multi-salida de un archivo de video.
    Seguro para llamar desde hilos GTK (usa GLib.idle_add internamente).
    """

    def __init__(self) -> None:
        self._pipeline: Gst.Pipeline | None = None
        self._assignments: dict[int, str] = {}   # track_index → sink_name
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

    def assign(self, track_index: int, sink_name: str) -> None:
        """Asigna una pista de audio a un dispositivo de salida."""
        with self._lock:
            self._assignments[track_index] = sink_name

    def clear_assignments(self) -> None:
        with self._lock:
            self._assignments.clear()

    def set_on_eos(self, callback: callable) -> None:
        self._on_eos = callback

    def set_on_error(self, callback: callable) -> None:
        self._on_error = callback

    # ── Reproducción ─────────────────────────────────────────────────────

    def prepare_video_sink(self) -> tuple[None, None]:
        """
        Obsoleto — gtk4paintablesink reemplazado por autovideosink.
        Mantenido para compatibilidad de firma con devices_page.
        """
        return None, None

    def play(
        self,
        path: str,
        show_video: bool = True,
        video_sink: object | None = None,
    ) -> tuple[bool, object | None]:
        """
        Construye el pipeline y arranca la reproducción.

        Si video_sink es proporcionado (pre-creado desde el hilo GTK),
        lo añade al pipeline antes de PLAYING — esto es lo correcto para
        gtk4paintablesink y Wayland.

        Retorna (éxito, paintable_o_None).
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

        # ── Video — autovideosink (ventana Wayland nativa) ───────────────────
        # gtk4paintablesink es inestable en Intel/Wayland (GL context issues).
        # autovideosink elige el mejor sink disponible (waylandsink, xvimagesink)
        # y crea su propia ventana — funciona 100% sin configuración.
        # La cadena se construye dinámicamente en _connect_video_pad_main.
        self._video_queue: Gst.Element | None = None
        self._video_pipeline_ref = pipeline
        self._video_sink: Gst.Element | None = None

        if show_video:
            vsink = Gst.ElementFactory.make("autovideosink", "vsink")
            if vsink:
                vsink.set_property("sync", True)
                pipeline.add(vsink)
                self._video_sink = vsink

        # Subtítulos: si hay archivo pre-cargado
        self._sub_pipeline_ready = False
        self._pending_sub_file = getattr(self, "_queued_sub_file", None)

        self._audio_pads: dict[int, Gst.Pad] = {}
        src.connect("pad-added", self._on_pad_added, pipeline, assignments)

        self._pipeline = pipeline
        pipeline.set_state(Gst.State.PLAYING)
        return True, None   # paintable ya no se usa (autovideosink crea su ventana)

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
        assignments: dict[int, str],
    ) -> None:
        # Contar cuántos pads de audio hemos visto hasta ahora
        audio_idx = len(self._audio_pads)
        self._audio_pads[audio_idx] = pad

        sink_name = assignments.get(audio_idx)

        # Construir mini-pipeline de audio: convert → resample → pulsesink/fakesink
        convert  = Gst.ElementFactory.make("audioconvert",  f"conv_{audio_idx}")
        resample = Gst.ElementFactory.make("audioresample", f"res_{audio_idx}")

        if sink_name:
            out = Gst.ElementFactory.make("pulsesink", f"out_{audio_idx}")
            out.set_property("device", sink_name)
        else:
            out = Gst.ElementFactory.make("fakesink", f"fake_{audio_idx}")

        queue = Gst.ElementFactory.make("queue", f"q_{audio_idx}")
        queue.set_property("max-size-buffers", 200)

        for elem in [queue, convert, resample, out]:
            pipeline.add(elem)

        # Orden correcto: enlazar cadena interna → sync → conectar pad upstream
        queue.link(convert)
        convert.link(resample)
        resample.link(out)

        for elem in [queue, convert, resample, out]:
            elem.sync_state_with_parent()

        # Conectar el pad de uridecodebin DESPUÉS de que los elementos estén en PLAYING
        pad.link(queue.get_static_pad("sink"))

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
