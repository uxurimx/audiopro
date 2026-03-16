# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project

```bash
pip install -e .                              # install (first time)
audifonospro                                  # launch GTK4 GUI (default)
audifonospro --ui tui                         # TUI Textual (fallback)
audifonospro --mode cinema                    # cinema mode
make run                                      # same as audifonospro
make install-whisper                          # build whisper.cpp (~150MB model)
make install-piper                            # download piper TTS binary
```

## Architecture

Multi-device personal audio system. Three primary modes:
- **Translator**: mic → VAD → STT → LLM → TTS → speaker (real-time translation)
- **Cinema**: MKV multi-track → GStreamer pipeline → one audio track per person per device
- **Monitor**: real-time device status (battery, RSSI, codec, PipeWire stats)

### Data flow

```
Device Enumerator (every 500ms, background thread)
  ├── bluetooth_monitor.py  → bluetoothctl + pactl + upower (subprocess)
  ├── pipewire_monitor.py   → pw-dump JSON
  └── device_enumerator.py → AudioDevice list (BT + jack + built-in + HDMI)
        │
        ▼ post_message() thread-safe
  Textual TUI (main thread)
  ├── Tab 1: Devices  → DataTable with all devices
  ├── Tab 2: Monitor  → DeviceCard widgets, updated in-place
  └── ...
```

### Module layout

```
audifonospro/
├── config.py          Pydantic Settings — reads config.yaml + .env, priority: env > .env > yaml
├── main.py            Entry point, argparse (--mode ui|cinema|translate)
│
├── monitor/
│   ├── device_info.py         AudioDevice dataclass + DeviceType enum (central data structure)
│   ├── bluetooth_monitor.py   bluetoothctl/pactl/upower subprocess parsers
│   ├── pipewire_monitor.py    pw-dump JSON parser → PipeWireNode list
│   └── device_enumerator.py   combines all sources → sorted AudioDevice list
│
├── audio/             (Phase 2) capture.py, playback.py, bluetooth.py, resampler.py
├── anc/               (Phase 3) 5 ANC levels: off/spectral/rnnoise/lms/hardware-gatt
├── ble/               (Phase 3) bleak GATT scanner for hardware ANC control
├── cinema/            (Phase 2) GStreamer multi-track router + MKV inspector
├── eq/                (Phase 3) scipy IIR filter chain + presets per person
├── vad/               (Phase 4) energy VAD (RMS + zero-crossing, no C extensions)
├── stt/               (Phase 4) whisper.cpp subprocess wrapper + OpenAI fallback
├── translation/       (Phase 4) Ollama REST + OpenAI GPT-4o-mini streaming
├── tts/               (Phase 4) piper binary + edge-tts (free) + OpenAI tts-1
├── controls/          (Phase 5) evdev watcher + gesture state machine + mapper
├── pipeline/          (Phase 4) coordinator: 5 threads + 4 queues with backpressure
├── stacks/            (Phase 4) named config bundles: LOCAL/SWEET_SPOT/CLOUD_PRO/CINEMA
├── profiles/          (Phase 5) per-person YAML profiles (EQ, ANC, device, language)
│
└── ui/
    ├── app.py          Textual App — 7 tabs, keyboard shortcuts 1-7
    ├── tabs/           One Widget per tab, lazy imports to avoid startup cost
    ├── widgets/        device_card.py — renders AudioDevice as Rich markup
    └── gtk/            GTK4 + libadwaita GUI (default)
        ├── app.py          Adw.Application (id: dev.robit.audifonospro)
        ├── window.py       MainWindow — Adw.ToolbarView + ViewStack + ToastOverlay
        ├── widgets/
        │   └── device_row.py  Adw.ExpanderRow — battery bar, RSSI, profile dropdown, HFP switch
        └── pages/          One Adw.PreferencesPage per tab
            ├── devices_page.py    device list, 2s poll, refresh button
            ├── monitor_page.py    live metrics, 500ms poll, DeviceRow in-place updates
            ├── controls_page.py   evdev gesture mapper (UI complete, backend Fase 5)
            ├── eq_page.py         10-band vertical faders + presets (Vocal clarity, Cinema…)
            ├── translator_page.py pipeline start/stop + STT/trans/TTS status rows
            ├── stacks_page.py     LOCAL / SWEET_SPOT / CLOUD_PRO / CINEMA selector
            └── settings_page.py   Adw.PreferencesPage with audio/BT/ANC/STT/UI groups
```

### Key design decisions

- **Python 3.14 compat**: all audio monitoring uses subprocess (not D-Bus Python bindings with C extensions). BLE uses `bleak` (pure Python asyncio).
- **Thread model**: background polling in daemon threads. GTK: `GLib.idle_add()` to post back to main thread. Textual: `post_message()`. Never block UI loop.
- **Device abstraction**: everything (BT, jack, built-in, HDMI) is an `AudioDevice`. Cinema mode and translator use the same device list.
- **Local-first**: whisper.cpp binary + Ollama (already installed: llama3:8b) + edge-tts = $0/session. OpenAI is optional fallback.
- **ANC**: JBL hardware ANC controlled via BLE GATT (bleak). Software fallback: noisereduce spectral + adaptive LMS using laptop mic as noise reference.

### System facts (this machine)

- OS: Fedora 43, Python 3.14.3, PipeWire 1.4.10, GStreamer 1.26.10
- JBL Vive Buds MAC: `B4:84:D5:98:E8:31` — currently connects in A2DP/AAC at 48kHz
- Ollama installed with: llama3:8b, llama3.2:3B, gemma2:2b (all free, local)
- No discrete GPU — Ollama translation is ~6-8s/sentence on CPU (use GPT-4o-mini for real-time)

### Config override pattern

```bash
# Override any setting via env var (double-underscore = nested)
TRANSLATION__PROVIDER=ollama audifonospro
ANC__DEFAULT_LEVEL=3 audifonospro
AUDIO__INPUT_DEVICE="JBL" audifonospro
```
