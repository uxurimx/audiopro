/**
 * audioPro — GNOME Shell Quick Settings Extension
 *
 * Agrega al panel de Quick Settings:
 *   1. Tile "audioPro" (QuickMenuToggle, como Wi-Fi/BT) que expande a:
 *      - Streams activos: slider de volumen por app (usa Gvc.MixerControl)
 *      - Dispositivos: batería, codec de cada dispositivo BT
 *      - Ecualizador: presets (Plano / Vocal / Cinema / Graves / Agudos)
 *      - Botón para abrir la app completa
 *
 * Comunicación con audioPro daemon:
 *   Lee  → ~/.cache/audifonospro/status.json  (escrito por el daemon)
 *   Escribe → ~/.config/audifonospro/eq_preset  (leído por el daemon)
 *
 * Compatible con GNOME Shell 45–49 (ESM modules).
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

// ─── Constantes ───────────────────────────────────────────────────────────────

const STATUS_FILE  = `${GLib.get_home_dir()}/.cache/audifonospro/status.json`;
const EQ_FILE      = `${GLib.get_home_dir()}/.config/audifonospro/eq_preset`;
const CONFIG_DIR   = `${GLib.get_home_dir()}/.config/audifonospro`;

const EQ_PRESETS = [
    ['flat',    'Plano'],
    ['vocal',   'Claridad vocal'],
    ['cinema',  'Cinema'],
    ['bass',    'Graves +'],
    ['treble',  'Agudos +'],
];

// ─── Slider de volumen por stream ─────────────────────────────────────────────

const AppStreamRow = GObject.registerClass(
class AppStreamRow extends St.BoxLayout {
    _init(stream, volMax) {
        super._init({
            style_class: 'audiopro-stream-row',
            vertical: false,
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });

        this._stream = stream;
        this._volMax = volMax;

        // Icono
        this.add_child(new St.Icon({
            icon_name: 'audio-volume-medium-symbolic',
            icon_size: 14,
            style: 'min-width:18px; color: #aaa;',
            y_align: Clutter.ActorAlign.CENTER,
        }));

        // Nombre de la app (máx 18 chars)
        const rawName = stream.description || stream.name || 'App';
        this.add_child(new St.Label({
            text: rawName.slice(0, 18),
            y_align: Clutter.ActorAlign.CENTER,
            style: 'min-width:90px; font-size:0.85em;',
        }));

        // Slider
        this._slider = new St.Slider({
            value: stream.volume / volMax,
            x_expand: true,
        });
        this._slider.connect('notify::value', () => {
            const vol = Math.round(this._slider.value * this._volMax);
            this._stream.volume = vol;
            this._stream.push_volume();
            this._pctLabel.text = `${Math.round(this._slider.value * 100)}%`;
        });
        this.add_child(this._slider);

        // % label
        this._pctLabel = new St.Label({
            text: `${Math.round(stream.volume / volMax * 100)}%`,
            y_align: Clutter.ActorAlign.CENTER,
            style: 'min-width:34px; font-size:0.8em; text-align:right;',
        });
        this.add_child(this._pctLabel);

        // Sincronizar cuando el volumen cambia desde otra fuente
        this._notifyId = stream.connect('notify::volume', () => {
            const pct = stream.volume / this._volMax;
            if (Math.abs(this._slider.value - pct) > 0.01) {
                this._slider.value = pct;
                this._pctLabel.text = `${Math.round(pct * 100)}%`;
            }
        });
    }

    destroy() {
        if (this._notifyId && this._stream) {
            this._stream.disconnect(this._notifyId);
            this._notifyId = 0;
        }
        super.destroy();
    }
});

// ─── Toggle principal ─────────────────────────────────────────────────────────

const AudioProToggle = GObject.registerClass(
class AudioProToggle extends QuickSettings.QuickMenuToggle {
    _init() {
        super._init({
            title: 'audioPro',
            subtitle: 'Sin streams',
            iconName: 'audio-headphones-symbolic',
        });

        this._streamRows = new Map();   // streamId → { item, row }
        this._deviceItems = [];         // PopupMenuItems de dispositivos
        this._deviceTimer = null;
        this._activeEQ = 'flat';

        this._buildMenu();
        this._initMixer();
        this._scheduleDeviceRefresh();

        this.connect('destroy', () => this._cleanup());
    }

    // ── Construcción del menú ──────────────────────────────────────────────

    _buildMenu() {
        this.menu.setHeader('audio-headphones-symbolic', 'audioPro', 'Control de audio');

        // ── Streams ──
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Streams activos'));
        this._streamSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._streamSection);

        this._noStreamsItem = new PopupMenu.PopupMenuItem('Sin apps reproduciéndose', {
            reactive: false,
            style_class: 'popup-inactive-menu-item',
        });
        this._streamSection.addMenuItem(this._noStreamsItem);

        // ── Dispositivos ──
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Dispositivos'));
        this._deviceSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._deviceSection);

        this._noDevicesItem = new PopupMenu.PopupMenuItem('Sin dispositivos BT', {
            reactive: false,
            style_class: 'popup-inactive-menu-item',
        });
        this._deviceSection.addMenuItem(this._noDevicesItem);

        // ── EQ ──
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

        // ── Abrir app ──
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const openItem = new PopupMenu.PopupMenuItem('Abrir audioPro →');
        openItem.connect('activate', () => this._openApp());
        this.menu.addMenuItem(openItem);
    }

    // ── Gvc: streams de audio ──────────────────────────────────────────────

    _initMixer() {
        this._mixer = new Gvc.MixerControl({ name: 'audiopro-qs' });

        this._mixerStateId = this._mixer.connect('state-changed', (_ctrl, state) => {
            if (state === Gvc.MixerControlState.READY)
                this._loadStreams();
        });
        this._mixerAddedId = this._mixer.connect('stream-added', (_ctrl, id) => {
            const s = this._mixer.lookup_stream_id(id);
            if (s) this._addStream(s);
        });
        this._mixerRemovedId = this._mixer.connect('stream-removed', (_ctrl, id) => {
            this._removeStream(id);
        });

        this._mixer.open();
    }

    _loadStreams() {
        for (const stream of this._mixer.get_sink_inputs())
            this._addStream(stream);
    }

    _addStream(stream) {
        if (!stream || this._streamRows.has(stream.id)) return;
        // Ignorar nuestra propia conexión de control
        if ((stream.name || '').includes('audiopro-qs')) return;

        this._noStreamsItem.visible = false;

        const volMax = this._mixer.get_vol_max_norm();
        const item = new PopupMenu.PopupBaseMenuItem({ reactive: false });
        item.style = 'padding: 3px 12px;';
        const row = new AppStreamRow(stream, volMax);
        item.add_child(row);
        this._streamSection.addMenuItem(item);
        this._streamRows.set(stream.id, { item, row });

        this._updateSubtitle();
    }

    _removeStream(id) {
        const entry = this._streamRows.get(id);
        if (!entry) return;
        entry.row.destroy();
        entry.item.destroy();
        this._streamRows.delete(id);
        if (this._streamRows.size === 0)
            this._noStreamsItem.visible = true;
        this._updateSubtitle();
    }

    _updateSubtitle() {
        const n = this._streamRows.size;
        this.subtitle = n === 0 ? 'Sin streams' : n === 1 ? '1 app activa' : `${n} apps activas`;
    }

    // ── Dispositivos BT ───────────────────────────────────────────────────

    _scheduleDeviceRefresh() {
        this._refreshDevices();
        this._deviceTimer = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, 5, () => {
            this._refreshDevices();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _refreshDevices() {
        // Prioridad 1: status.json escrito por el daemon de audioPro
        try {
            const [ok, bytes] = GLib.file_get_contents(STATUS_FILE);
            if (ok) {
                const status = JSON.parse(new TextDecoder().decode(bytes));
                this._applyStatus(status);
                return;
            }
        } catch (_) {}

        // Fallback: pactl directo
        this._refreshViaPactl();
    }

    _applyStatus(status) {
        const devices = (status.devices || []).map(d => ({
            name:      d.name || 'Dispositivo',
            battery:   d.battery_pct ?? null,
            codec:     d.codec ?? null,
            connected: d.connected ?? true,
        }));
        this._renderDevices(devices);

        if (status.pipeline_running && status.src_lang && status.dst_lang)
            this.subtitle = `Traduciendo: ${status.src_lang} → ${status.dst_lang}`;
        else
            this._updateSubtitle();

        // Sync EQ activo
        if (status.eq_preset && this._eqItems[status.eq_preset])
            this._setEQOrnament(status.eq_preset);
    }

    _refreshViaPactl() {
        try {
            const proc = Gio.Subprocess.new(
                ['pactl', '--format=json', 'list', 'cards'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
            );
            proc.communicate_utf8_async(null, null, (_proc, res) => {
                try {
                    const [, out] = _proc.communicate_utf8_finish(res);
                    const cards = JSON.parse(out);
                    const btDevices = cards
                        .filter(c => JSON.stringify(c.properties || {}).toLowerCase().includes('bluetooth'))
                        .map(c => ({
                            name:    this._friendlyCardName(c.name),
                            battery: null,
                            codec:   c.properties?.['bluetooth.codec'] ?? null,
                        }));
                    this._renderDevices(btDevices);
                } catch (_) {}
            });
        } catch (_) {}
    }

    _friendlyCardName(raw) {
        // "bluez_card.B4_84_D5_98_E8_31" → "B4:84:D5:98:E8:31"
        const m = (raw || '').match(/bluez_card\.([0-9A-Fa-f_]+)/i);
        return m ? m[1].replace(/_/g, ':') : (raw || 'BT').split('.').pop();
    }

    _renderDevices(devices) {
        // Destruir items anteriores
        for (const item of this._deviceItems)
            item.destroy();
        this._deviceItems = [];

        if (devices.length === 0) {
            this._noDevicesItem.visible = true;
            return;
        }
        this._noDevicesItem.visible = false;

        for (const dev of devices) {
            let label = dev.name;
            if (dev.battery !== null) label += `   🔋 ${dev.battery}%`;
            if (dev.codec)            label += `   ${dev.codec}`;
            if (dev.connected === false) label += '  (off)';

            const item = new PopupMenu.PopupMenuItem(label, { reactive: false });
            this._deviceSection.addMenuItem(item);
            this._deviceItems.push(item);
        }
    }

    // ── EQ ────────────────────────────────────────────────────────────────

    _applyEQ(id) {
        this._setEQOrnament(id);
        this._activeEQ = id;
        // Escribir preset para que el daemon de audioPro lo lea
        try {
            GLib.mkdir_with_parents(CONFIG_DIR, 0o755);
            GLib.file_set_contents(EQ_FILE, id);
        } catch (_) {}
    }

    _setEQOrnament(id) {
        for (const [k, item] of Object.entries(this._eqItems))
            item.setOrnament(k === id ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
    }

    // ── Abrir app ─────────────────────────────────────────────────────────

    _openApp() {
        try {
            Gio.Subprocess.new(
                ['audifonospro'],
                Gio.SubprocessFlags.NONE
            );
        } catch (_) {}
    }

    // ── Limpieza ──────────────────────────────────────────────────────────

    _cleanup() {
        if (this._deviceTimer) {
            GLib.source_remove(this._deviceTimer);
            this._deviceTimer = null;
        }
        if (this._mixer) {
            try {
                this._mixer.disconnect(this._mixerStateId);
                this._mixer.disconnect(this._mixerAddedId);
                this._mixer.disconnect(this._mixerRemovedId);
                this._mixer.close();
            } catch (_) {}
            this._mixer = null;
        }
    }
});

// ─── SystemIndicator (contenedor requerido por Quick Settings) ────────────────

const AudioProIndicator = GObject.registerClass(
class AudioProIndicator extends QuickSettings.SystemIndicator {
    _init() {
        super._init();
        this._toggle = new AudioProToggle();
        this.quickSettingsItems.push(this._toggle);
    }

    destroy() {
        this.quickSettingsItems.forEach(i => i.destroy());
        super.destroy();
    }
});

// ─── Entry point ──────────────────────────────────────────────────────────────

export default class AudioProExtension extends Extension {
    enable() {
        this._indicator = new AudioProIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
