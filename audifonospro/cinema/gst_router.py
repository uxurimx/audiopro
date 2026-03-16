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

    def play(self, path: str, show_video: bool = True) -> bool:
        """
        Construye el pipeline GStreamer y arranca la reproducción.

        El video se muestra en una ventana flotante si show_video=True.
        El audio de cada pista asignada sale por el dispositivo correspondiente.
        Pistas sin asignación se silencian (fakesink).
        """
        self.stop()

        with self._lock:
            assignments = dict(self._assignments)

        if not assignments:
            return False

        uri = Gst.filename_to_uri(path)
        pipeline = Gst.Pipeline.new("cinema")
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        # Fuente + demuxer
        src = Gst.ElementFactory.make("uridecodebin", "src")
        src.set_property("uri", uri)
        pipeline.add(src)

        # Una salida de audio por pista asignada
        self._audio_pads: dict[int, Gst.Pad] = {}
        src.connect("pad-added", self._on_pad_added, pipeline, assignments, show_video)

        self._pipeline = pipeline
        pipeline.set_state(Gst.State.PLAYING)
        return True

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

    def _on_pad_added(
        self,
        src: Gst.Element,
        pad: Gst.Pad,
        pipeline: Gst.Pipeline,
        assignments: dict[int, str],
        show_video: bool,
    ) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        if not caps or caps.is_empty():
            return
        struct = caps.get_structure(0)
        name = struct.get_name()

        if name.startswith("audio/"):
            self._link_audio_pad(pad, pipeline, assignments)
        elif name.startswith("video/") and show_video:
            self._link_video_pad(pad, pipeline)

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

        pad.link(queue.get_static_pad("sink"))
        queue.link(convert)
        convert.link(resample)
        resample.link(out)

        for elem in [queue, convert, resample, out]:
            elem.sync_state_with_parent()

    def _link_video_pad(self, pad: Gst.Pad, pipeline: Gst.Pipeline) -> None:
        queue   = Gst.ElementFactory.make("queue",          "vq")
        convert = Gst.ElementFactory.make("videoconvert",   "vconv")
        sink    = Gst.ElementFactory.make("autovideosink",  "vsink")
        sink.set_property("sync", True)

        for elem in [queue, convert, sink]:
            pipeline.add(elem)

        pad.link(queue.get_static_pad("sink"))
        queue.link(convert)
        convert.link(sink)

        for elem in [queue, convert, sink]:
            elem.sync_state_with_parent()

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
