/**
 * audioPro — GNOME Shell Quick Settings Extension
 *
 * Estructura FINAL:
 *
 *  [🎧 volumen master ]  ──────────────────────  >    ← GNOME nativo (sin tocar)
 *  [🌞 brillo         ]  ──────────────────────        ← GNOME nativo (sin tocar)
 *  [ spotify-icon ] Spotify  ─────────────────  72%   ← NUEVO: stream slider
 *  [ app-icon    ] Firefox   ─────────────────  45%   ← NUEVO: stream slider
 *    └→ clic en icono = menú de sinks (JBL / bocina / TV...)
 *  ┌────────────────────────────────────────────────┐
 *  │  Wi-Fi  BT  Power  Night  Dark  DND  ...      │  ← tiles nativos
 *  └────────────────────────────────────────────────┘
 *  [🎧 audioPro    >]   ← tile: batería, codec, EQ, abrir app
 *
 * Señales Gvc verificadas en Gvc-1.0.gir de este sistema:
 *   state-changed, stream-added, stream-removed  ✓
 *   (sink-input-added NO existe en este Gvc — se filtra con instanceof)
 */

import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gvc from 'gi://Gvc';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const STATUS_FILE = `${GLib.get_home_dir()}/.cache/audifonospro/status.json`;
const EQ_FILE     = `${GLib.get_home_dir()}/.config/audifonospro/eq_preset`;
const CONFIG_DIR  = `${GLib.get_home_dir()}/.config/audifonospro`;

const EQ_PRESETS = [
    ['flat',   'Plano'],
    ['vocal',  'Claridad vocal'],
    ['cinema', 'Cinema'],
    ['bass',   'Graves +'],
    ['treble', 'Agudos +'],
];

// ═══════════════════════════════════════════════════════════════════════════════
// AppStreamItem — una fila por app activa (aspecto idéntico al slider de volumen)
// ═══════════════════════════════════════════════════════════════════════════════
const AppStreamItem = GObject.registerClass(
class AppStreamItem extends QuickSettings.QuickSettingsItem {
    _init(stream, mixer) {
        super._init({ style_class: 'quick-slider', reactive: true });

        this._stream     = stream;
        this._mixer      = mixer;
        this._volMax     = mixer.get_vol_max_norm();
        this._notifyId   = 0;
        this._sliderId   = 0;
        this._sinkMenu   = null;
        this._pressId    = 0;

        // ── Layout idéntico al native quick-slider ──
        const bin = new St.BoxLayout({ style_class: 'quick-slider-bin' });
        this.child = bin;

        // Icono de la app (clicable → menú de sinks)
        this._iconBtn = new St.Button({
            style_class: 'icon-button flat',
            can_focus: true,
            y_align: Clutter.ActorAlign.CENTER,
            child: new St.Icon({
                icon_name: 'audio-volume-medium-symbolic',
                icon_size: 16,
            }),
        });
        this._iconBtn.connect('clicked', () => this._openSinkMenu());
        bin.add_child(this._iconBtn);

        // Nombre de la app
        const appName = (stream.description || stream.name || 'App').slice(0, 20);
        bin.add_child(new St.Label({
            text: appName,
            y_align: Clutter.ActorAlign.CENTER,
            style: 'min-width: 90px; padding-right: 6px; font-size: 0.85em;',
        }));

        // Slider de volumen
        this._slider = new St.Slider({ value: stream.volume / this._volMax, x_expand: true });
        this._sliderId = this._slider.connect('notify::value', () => {
            this._stream.volume = Math.round(this._slider.value * this._volMax);
            this._stream.push_volume();
            this._pct.text = `${Math.round(this._slider.value * 100)}%`;
        });
        bin.add_child(this._slider);

        // Porcentaje
        this._pct = new St.Label({
            text: `${Math.round(stream.volume / this._volMax * 100)}%`,
            y_align: Clutter.ActorAlign.CENTER,
            style: 'min-width: 38px; text-align: right; font-size: 0.8em;',
        });
        bin.add_child(this._pct);

        // Sincronizar cuando el volumen cambia desde afuera
        this._notifyId = stream.connect('notify::volume', () => {
            const pct = stream.volume / this._volMax;
            if (Math.abs(this._slider.value - pct) > 0.01) {
                this._slider.value = pct;
                this._pct.text = `${Math.round(pct * 100)}%`;
            }
        });
    }

    // ── Menú de selección de sink ────────────────────────────────────────────
    _openSinkMenu() {
        this._closeSinkMenu(); // cerrar si ya estaba abierto

        const sinks = this._mixer.get_sinks();
        if (!sinks?.length) return;

        this._sinkMenu = new PopupMenu.PopupMenu(this._iconBtn, 0.0, St.Side.BOTTOM);
        Main.uiGroup.add_child(this._sinkMenu.actor);

        for (const sink of sinks) {
            const label = (sink.description || sink.name || '?').slice(0, 32);
            const item = new PopupMenu.PopupMenuItem(label);
            item.connect('activate', () => {
                try {
                    Gio.Subprocess.new(
                        ['pactl', 'move-sink-input', `${this._stream.id}`, `${sink.name}`],
                        Gio.SubprocessFlags.NONE
                    );
                } catch (_) {}
                this._closeSinkMenu();
            });
            this._sinkMenu.addMenuItem(item);
        }

        // Cerrar al hacer click fuera del menú
        this._pressId = global.stage.connect('button-press-event', (_stage, event) => {
            const src = event.get_source();
            if (!this._sinkMenu?.actor.contains(src))
                this._closeSinkMenu();
        });

        this._sinkMenu.open(true);
    }

    _closeSinkMenu() {
        if (this._pressId) {
            global.stage.disconnect(this._pressId);
            this._pressId = 0;
        }
        if (this._sinkMenu) {
            this._sinkMenu.close(false);
            this._sinkMenu.destroy();
            this._sinkMenu = null;
        }
    }

    destroy() {
        this._closeSinkMenu();
        if (this._notifyId) { this._stream?.disconnect(this._notifyId); this._notifyId = 0; }
        if (this._sliderId) { this._slider?.disconnect(this._sliderId); this._sliderId = 0; }
        super.destroy();
    }
});

// ═══════════════════════════════════════════════════════════════════════════════
// AudioProToggle — tile para batería de dispositivos + EQ
// ═══════════════════════════════════════════════════════════════════════════════
const AudioProToggle = GObject.registerClass(
class AudioProToggle extends QuickSettings.QuickMenuToggle {
    _init() {
        super._init({
            title: 'audioPro',
            subtitle: 'Control de audio',
            iconName: 'audio-headphones-symbolic',
        });

        this._deviceItems = [];
        this._deviceTimer = null;

        this._buildMenu();
        this._scheduleDeviceRefresh();
        this.connect('destroy', () => this._cleanup());
    }

    _buildMenu() {
        this.menu.setHeader('audio-headphones-symbolic', 'audioPro', 'Control de audio');

        // Dispositivos
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Dispositivos'));
        this._deviceSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._deviceSection);
        this._noDevicesItem = new PopupMenu.PopupMenuItem('Sin dispositivos', {
            reactive: false, style_class: 'popup-inactive-menu-item',
        });
        this._deviceSection.addMenuItem(this._noDevicesItem);

        // Ecualizador
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Ecualizador'));
        this._eqSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._eqSection);
        this._eqItems = {};
        for (const [id, label] of EQ_PRESETS) {
            const item = new PopupMenu.PopupMenuItem(label);
            item.connect('activate', () => this._applyEQ(id));
            this._eqSection.addMenuItem(item);
            this._eqItems[id] = item;
        }
        this._eqItems['flat'].setOrnament(PopupMenu.Ornament.DOT);

        // Abrir app
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const openItem = new PopupMenu.PopupMenuItem('Abrir audioPro →');
        openItem.connect('activate', () => {
            try { Gio.Subprocess.new(['audifonospro'], Gio.SubprocessFlags.NONE); } catch (_) {}
        });
        this.menu.addMenuItem(openItem);
    }

    _scheduleDeviceRefresh() {
        this._refreshDevices();
        this._deviceTimer = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, 5, () => {
            this._refreshDevices();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _refreshDevices() {
        // Prioridad 1: status.json del daemon
        try {
            const [ok, bytes] = GLib.file_get_contents(STATUS_FILE);
            if (ok) {
                const s = JSON.parse(new TextDecoder().decode(bytes));
                this._renderDevices((s.devices || []).map(d => ({
                    name: d.name || 'Dispositivo',
                    battery: d.battery_pct ?? null,
                    codec: d.codec ?? null,
                })));
                if (s.eq_preset && this._eqItems[s.eq_preset])
                    this._setEQOrnament(s.eq_preset);
                return;
            }
        } catch (_) {}

        // Fallback: pactl list cards
        try {
            const proc = Gio.Subprocess.new(
                ['pactl', '--format=json', 'list', 'cards'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
            );
            proc.communicate_utf8_async(null, null, (_p, res) => {
                try {
                    const [, out] = _p.communicate_utf8_finish(res);
                    const bt = JSON.parse(out)
                        .filter(c => JSON.stringify(c.properties || {}).toLowerCase().includes('bluetooth'))
                        .map(c => ({ name: this._friendlyName(c.name), battery: null, codec: null }));
                    this._renderDevices(bt);
                } catch (_) {}
            });
        } catch (_) {}
    }

    _friendlyName(raw) {
        const m = (raw || '').match(/bluez_card\.([0-9A-Fa-f_]+)/i);
        return m ? m[1].replace(/_/g, ':') : (raw || 'BT').split('.').pop();
    }

    _renderDevices(devices) {
        for (const item of this._deviceItems) item.destroy();
        this._deviceItems = [];
        if (!devices.length) { this._noDevicesItem.visible = true; return; }
        this._noDevicesItem.visible = false;
        for (const dev of devices) {
            let label = dev.name;
            if (dev.battery !== null) label += `   🔋 ${dev.battery}%`;
            if (dev.codec) label += `   ${dev.codec}`;
            const item = new PopupMenu.PopupMenuItem(label, { reactive: false });
            this._deviceSection.addMenuItem(item);
            this._deviceItems.push(item);
        }
    }

    _applyEQ(id) {
        this._setEQOrnament(id);
        try { GLib.mkdir_with_parents(CONFIG_DIR, 0o755); GLib.file_set_contents(EQ_FILE, id); } catch (_) {}
    }

    _setEQOrnament(id) {
        for (const [k, item] of Object.entries(this._eqItems))
            item.setOrnament(k === id ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
    }

    _cleanup() {
        if (this._deviceTimer) { GLib.source_remove(this._deviceTimer); this._deviceTimer = null; }
    }
});

const AudioProIndicator = GObject.registerClass(
class AudioProIndicator extends QuickSettings.SystemIndicator {
    _init() {
        super._init();
        this._toggle = new AudioProToggle();
        this.quickSettingsItems.push(this._toggle);
    }
    destroy() { this.quickSettingsItems.forEach(i => i.destroy()); super.destroy(); }
});

// ═══════════════════════════════════════════════════════════════════════════════
// Extension principal
// ═══════════════════════════════════════════════════════════════════════════════
export default class AudioProExtension extends Extension {
    enable() {
        this._streamItems = new Map();  // streamId → AppStreamItem
        this._mixer = null;

        this._initMixer();

        // Tile de dispositivos + EQ
        this._indicator = new AudioProIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        // Destruir todos los stream items del grid
        for (const item of this._streamItems.values())
            item.destroy();
        this._streamItems.clear();

        // Destruir mixer
        if (this._mixer) {
            try {
                this._mixer.disconnect(this._stateId);
                this._mixer.disconnect(this._addedId);
                this._mixer.disconnect(this._removedId);
                this._mixer.close();
            } catch (_) {}
            this._mixer = null;
        }

        // Destruir tile
        this._indicator?.destroy();
        this._indicator = null;
    }

    // ── Gvc ──────────────────────────────────────────────────────────────────

    _initMixer() {
        this._mixer = new Gvc.MixerControl({ name: 'audiopro-qs' });

        // state-changed: conexión PipeWire/PA lista
        this._stateId = this._mixer.connect('state-changed', (_c, state) => {
            if (state === Gvc.MixerControlState.READY)
                this._loadExistingStreams();
        });

        // stream-added: cualquier stream nuevo (sink, sink-input, source…)
        // Filtramos con instanceof para quedarnos SOLO con sink inputs (apps)
        this._addedId = this._mixer.connect('stream-added', (_c, id) => {
            try {
                const s = this._mixer.lookup_stream_id(id);
                if (s instanceof Gvc.MixerSinkInput)
                    this._addStream(s);
            } catch (_) {}
        });

        // stream-removed: limpiar el item del grid
        this._removedId = this._mixer.connect('stream-removed', (_c, id) => {
            this._removeStream(id);
        });

        this._mixer.open();

        // Fallback: si ya estaba en READY antes de conectar las señales
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            try { this._loadExistingStreams(); } catch (_) {}
            return GLib.SOURCE_REMOVE;
        });
    }

    _loadExistingStreams() {
        if (!this._mixer) return;
        try {
            for (const s of this._mixer.get_sink_inputs())
                this._addStream(s);
        } catch (_) {}
    }

    _addStream(stream) {
        if (!stream || this._streamItems.has(stream.id)) return;
        // Ignorar nuestra propia conexión Gvc
        if ((stream.name || '').includes('audiopro-qs')) return;

        const item = new AppStreamItem(stream, this._mixer);

        // Insertar en el grid de Quick Settings como fila completa (colSpan=2)
        try {
            Main.panel.statusArea.quickSettings._grid.addItem(item, 2);
        } catch (_) {
            // Si _grid no existe (API futura), usar addExternalIndicator no es posible aquí.
            // Destruir y no mostrar antes que crashear GNOME.
            item.destroy();
            return;
        }

        this._streamItems.set(stream.id, item);
    }

    _removeStream(id) {
        const item = this._streamItems.get(id);
        if (!item) return;
        item.destroy();
        this._streamItems.delete(id);
    }
}
