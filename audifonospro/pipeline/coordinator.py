"""
Pipeline de traducción en tiempo real.

Arquitectura: 4 hilos + 3 colas con backpressure.

  CaptureThread (sounddevice + VAD)
      ↓  q_segments (WAV bytes)
  STTThread (whisper.cpp / OpenAI Whisper)
      ↓  q_texts (str original)
  TranslateThread (Ollama / OpenAI GPT)
      ↓  q_translated (str traducido)
  TTSThread (edge-tts / piper / OpenAI TTS → GStreamer play)

Callbacks de estado (llamados desde hilos worker, usar GLib.idle_add en GTK):
  on_status(stage: str, text: str)
      stage ∈ {"stt", "trans", "tts", "latency"}
  on_transcript(original: str, translated: str)

Uso:
    pipe = get_pipeline()
    pipe.on_status = lambda s, t: GLib.idle_add(update_ui, s, t)
    pipe.start(src_lang="en", dst_lang="es", ...)
    ...
    pipe.stop()
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable


# Mapa nombre UI → (código whisper, voz edge-tts defecto)
LANG_INFO: dict[str, tuple[str, str]] = {
    "Español":  ("es", "es-MX-JorgeNeural"),
    "English":  ("en", "en-US-AriaNeural"),
    "Français": ("fr", "fr-FR-DeniseNeural"),
    "Deutsch":  ("de", "de-DE-KatjaNeural"),
    "Italiano": ("it", "it-IT-ElsaNeural"),
    "Português":("pt", "pt-BR-FranciscaNeural"),
    "日本語":   ("ja", "ja-JP-NanamiNeural"),
    "中文":     ("zh", "zh-CN-XiaoxiaoNeural"),
    "한국어":   ("ko", "ko-KR-SunHiNeural"),
}

_SENTINEL = object()   # señal de parada para las colas


class TranslationPipeline:
    """
    Coordina los 4 hilos del pipeline de traducción.

    Thread-safe: start() y stop() se pueden llamar desde cualquier hilo.
    Los callbacks se llaman desde los hilos worker — en GTK usar GLib.idle_add.
    """

    def __init__(self) -> None:
        self._running = False
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

        # Colas inter-hilo
        self._q_segments: queue.Queue = queue.Queue(maxsize=3)
        self._q_texts:    queue.Queue = queue.Queue(maxsize=5)
        self._q_translated: queue.Queue = queue.Queue(maxsize=5)

        # Callbacks (asignar antes de start)
        self.on_status:     Callable[[str, str], None] | None = None
        self.on_transcript: Callable[[str, str], None] | None = None

        # DB — sesión activa
        self._session_id: int | None = None

        # Config activa (se establece en start / reconfigure)
        self._src_code:      str = "en"
        self._dst_name:      str = "Español"
        self._dst_code:      str = "es"
        self._dst_voice:     str = "es-MX-JorgeNeural"
        self._stt_provider:  str = "whisper_cpp"
        self._trans_provider:str = "openai"
        self._trans_model:   str = "gpt-4o-mini"
        self._tts_provider:  str = "edge_tts"
        self._tts_voice:     str = "es-MX-JorgeNeural"
        self._output_device: str | None = None
        self._mic_source:    str | None = None  # PulseAudio/PipeWire source name
        self._translate_enabled: bool = True

    # ── API pública ───────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        src_lang: str = "English",
        dst_lang: str = "Español",
        stt_provider: str = "whisper_cpp",
        trans_provider: str = "openai",
        trans_model: str = "gpt-4o-mini",
        tts_provider: str = "edge_tts",
        tts_voice: str | None = None,
        output_device: str | None = None,
        mic_source: str | None = None,
        translate: bool = True,
    ) -> None:
        with self._lock:
            if self._running:
                self._stop_internal()

            # Resolver códigos de idioma
            src_code, _    = LANG_INFO.get(src_lang, ("en", ""))
            dst_code, dflt_voice = LANG_INFO.get(dst_lang, ("es", "es-MX-JorgeNeural"))

            self._src_code       = src_code
            self._dst_name       = dst_lang
            self._dst_code       = dst_code
            self._dst_voice      = tts_voice or dflt_voice
            self._stt_provider   = stt_provider
            self._trans_provider = trans_provider
            self._trans_model    = trans_model
            self._tts_provider   = tts_provider
            self._tts_voice      = tts_voice or dflt_voice
            self._output_device      = output_device
            self._mic_source         = mic_source
            self._translate_enabled  = translate

            # Vaciar colas
            for q in (self._q_segments, self._q_texts, self._q_translated):
                while not q.empty():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

            self._running = True

            # Iniciar sesión en la DB
            try:
                from audifonospro.db.sessions import start_session
                quality_map = {"whisper_cpp": "local", "openai": "high"}
                quality = "balanced" if trans_provider == "openai" and stt_provider == "whisper_cpp" else quality_map.get(stt_provider, "local")
                self._session_id = start_session(
                    mode="translator",
                    src_lang=src_code,
                    dst_lang=dst_code,
                    quality=quality,
                )
            except Exception:
                self._session_id = None

            # Notificar a la extensión GNOME
            try:
                from audifonospro.dbus.status_writer import write_status
                write_status(pipeline_running=True, src_lang=src_code, dst_lang=dst_code)
            except Exception:
                pass

            self._threads = [
                threading.Thread(target=self._capture_thread, daemon=True, name="t-capture"),
                threading.Thread(target=self._stt_thread,     daemon=True, name="t-stt"),
                threading.Thread(target=self._trans_thread,   daemon=True, name="t-trans"),
                threading.Thread(target=self._tts_thread,     daemon=True, name="t-tts"),
            ]
            for t in self._threads:
                t.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_internal()

    def reconfigure(
        self,
        stt_provider: str | None = None,
        trans_provider: str | None = None,
        trans_model: str | None = None,
        tts_provider: str | None = None,
        tts_voice: str | None = None,
    ) -> None:
        """Actualiza los motores sin detener el pipeline."""
        if stt_provider:   self._stt_provider   = stt_provider
        if trans_provider: self._trans_provider = trans_provider
        if trans_model:    self._trans_model    = trans_model
        if tts_provider:   self._tts_provider   = tts_provider
        if tts_voice:      self._tts_voice      = tts_voice

    # ── Internals ─────────────────────────────────────────────────────────

    def _stop_internal(self) -> None:
        self._running = False
        # Cerrar sesión en la DB
        if self._session_id is not None:
            try:
                from audifonospro.db.sessions import end_session
                end_session(self._session_id)
            except Exception:
                pass
            self._session_id = None
        # Notificar a la extensión GNOME
        try:
            from audifonospro.dbus.status_writer import write_status
            write_status(pipeline_running=False, src_lang="", dst_lang="")
        except Exception:
            pass
        # Desbloquear hilos con sentinelas
        for q in (self._q_segments, self._q_texts, self._q_translated):
            try:
                q.put_nowait(_SENTINEL)
            except queue.Full:
                pass
        for t in self._threads:
            t.join(timeout=3.0)
        self._threads.clear()

    def _update(self, stage: str, text: str) -> None:
        if self.on_status:
            try:
                self.on_status(stage, text)
            except Exception:
                pass

    # ── Hilo 1: Captura + VAD ─────────────────────────────────────────────

    def _capture_thread(self) -> None:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GstApp", "1.0")
            from gi.repository import Gst, GstApp  # noqa: F401 — GstApp needed for try_pull_sample
            import numpy as np
            from audifonospro.vad.energy_vad import EnergyVAD
            from audifonospro.config import get_settings

            if not Gst.is_initialized():
                Gst.init(None)

            s = get_settings()
            vad = EnergyVAD(
                sample_rate=16000,
                silence_threshold_db=s.vad.silence_threshold_db,
                silence_duration_ms=s.vad.silence_duration_ms,
                min_speech_ms=s.vad.min_speech_ms,
                max_speech_ms=s.vad.max_speech_ms,
            )

            self._update("stt", "Escuchando…")
            self._last_rms: float = 0.0

            # pulsesrc habla directamente con PipeWire vía compatibilidad PulseAudio
            device_part = f'device="{self._mic_source}"' if self._mic_source else ""
            pipe_str = (
                f"pulsesrc {device_part} ! "
                "audio/x-raw,rate=16000,channels=1,format=S16LE ! "
                "appsink name=sink max-buffers=10 drop=true sync=false"
            )
            gst_pipe = Gst.parse_launch(pipe_str)
            appsink = gst_pipe.get_by_name("sink")

            ret = gst_pipe.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError(
                    f"GStreamer no pudo iniciar captura "
                    f"(device={'default' if not self._mic_source else self._mic_source})"
                )

            while self._running:
                sample = appsink.try_pull_sample(100 * Gst.MSECOND)
                if sample is None:
                    continue
                buf = sample.get_buffer()
                ok, mapinfo = buf.map(Gst.MapFlags.READ)
                if ok:
                    chunk = np.frombuffer(bytes(mapinfo.data), dtype=np.int16)
                    buf.unmap(mapinfo)
                    self._last_rms = float(
                        np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
                    )
                    segment = vad.feed(chunk)
                    if segment:
                        self._update("stt", "Voz detectada — transcribiendo…")
                        try:
                            self._q_segments.put_nowait(segment)
                        except queue.Full:
                            pass  # backpressure: descartar segmento

            gst_pipe.set_state(Gst.State.NULL)

        except Exception as exc:
            self._update("stt", f"Error captura: {str(exc)[:80]}")
        finally:
            # Enviar sentinela para los hilos downstream
            try:
                self._q_segments.put_nowait(_SENTINEL)
            except queue.Full:
                pass

    # ── Hilo 2: STT ───────────────────────────────────────────────────────

    def _stt_thread(self) -> None:
        from audifonospro.stt.whisper_stt import transcribe
        from audifonospro.config import get_settings
        s = get_settings()

        while True:
            try:
                item = self._q_segments.get(timeout=1.0)
            except queue.Empty:
                if not self._running:
                    break
                continue

            if item is _SENTINEL or not self._running:
                break

            self._update("stt", "Transcribiendo…")
            t0 = time.monotonic()
            try:
                text = transcribe(
                    item,
                    language=self._src_code,
                    settings=s,
                    provider=self._stt_provider,
                )
                elapsed = int((time.monotonic() - t0) * 1000)
                if text.strip():
                    self._update("stt", f"✓ {text[:50]}  ({elapsed} ms)")
                    try:
                        self._q_texts.put((text.strip(), elapsed), timeout=5.0)
                    except queue.Full:
                        pass
                else:
                    self._update("stt", "Escuchando…")
            except Exception as exc:
                self._update("stt", f"Error: {str(exc)[:60]}")

        try:
            self._q_texts.put_nowait(_SENTINEL)
        except queue.Full:
            pass

    # ── Hilo 3: Traducción ────────────────────────────────────────────────

    def _trans_thread(self) -> None:
        from audifonospro.translation.translator import translate
        from audifonospro.config import get_settings
        s = get_settings()

        while True:
            try:
                item = self._q_texts.get(timeout=1.0)
            except queue.Empty:
                if not self._running:
                    break
                continue

            if item is _SENTINEL or not self._running:
                break

            # Desempaquetar (text, stt_ms) que envía el hilo STT
            if isinstance(item, tuple):
                original_text, stt_ms = item
            else:
                original_text, stt_ms = item, 0

            t0 = time.monotonic()
            try:
                if self._translate_enabled:
                    self._update("trans", "Traduciendo…")
                    translated = translate(
                        original_text,
                        target_language=self._dst_name,
                        provider=self._trans_provider,
                        model=self._trans_model,
                        settings=s,
                    )
                    elapsed = int((time.monotonic() - t0) * 1000)
                    if not translated.strip():
                        self._update("trans", "En espera")
                        continue
                    self._update("trans", f"✓ {translated[:50]}  ({elapsed} ms)")
                else:
                    # Modo transcripción: pasar el texto original sin traducir
                    translated = original_text
                    elapsed = 0
                    self._update("trans", "Solo transcripción")

                # Guardar en DB
                try:
                    from audifonospro.db.phrases import save_phrase
                    save_phrase(
                        session_id  = self._session_id,
                        original    = original_text,
                        translated  = translated.strip(),
                        src_lang    = self._src_code,
                        dst_lang    = self._dst_code if self._translate_enabled else self._src_code,
                        stt_ms      = stt_ms,
                        trans_ms    = elapsed,
                    )
                except Exception:
                    pass
                if self.on_transcript:
                    try:
                        self.on_transcript(original_text, translated)
                    except Exception:
                        pass
                try:
                    self._q_translated.put(translated.strip(), timeout=5.0)
                except queue.Full:
                    pass
            except Exception as exc:
                self._update("trans", f"Error: {str(exc)[:60]}")

        try:
            self._q_translated.put_nowait(_SENTINEL)
        except queue.Full:
            pass

    # ── Hilo 4: TTS + Reproducción ────────────────────────────────────────

    def _tts_thread(self) -> None:
        import os
        from audifonospro.tts.tts_engine import synthesize, play_audio
        from audifonospro.config import get_settings
        s = get_settings()

        while True:
            try:
                item = self._q_translated.get(timeout=1.0)
            except queue.Empty:
                if not self._running:
                    break
                continue

            if item is _SENTINEL or not self._running:
                break

            # "none" = solo texto, sin síntesis ni reproducción
            if self._tts_provider == "none":
                self._update("tts", "Solo texto — sin voz")
                continue

            self._update("tts", "Sintetizando…")
            t0 = time.monotonic()
            audio_path: str | None = None
            try:
                audio_path = synthesize(
                    item,
                    language=self._dst_name,
                    provider=self._tts_provider,
                    settings=s,
                )
                synth_ms = int((time.monotonic() - t0) * 1000)
                self._update("tts", f"Reproduciendo…  (síntesis {synth_ms} ms)")
                play_audio(audio_path, device=self._output_device)
                total_ms = int((time.monotonic() - t0) * 1000)
                self._update("tts", f"✓ listo  ({total_ms} ms)")
                self._update("latency", f"{total_ms} ms")
            except Exception as exc:
                self._update("tts", f"Error: {str(exc)[:60]}")
            finally:
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.unlink(audio_path)
                    except OSError:
                        pass


# ── Singleton ─────────────────────────────────────────────────────────────────

_pipeline: TranslationPipeline | None = None


def get_pipeline() -> TranslationPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = TranslationPipeline()
    return _pipeline
