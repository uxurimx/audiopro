/**
 * audioPro — GNOME Shell Quick Settings Extension
 *
 * Layout final:
 *   [🎧] ────────────────── 80%  >    ← volumen master (GNOME, sin tocar)
 *   [🌞] ────────────────── 60%       ← brillo (GNOME, sin tocar)
 *   [●] Spotify  ──────────  72%      ← stream row (esta extensión, colSpan=2)
 *   [●] Firefox  ──────────  45%      ← stream row
 *        └→ clic icono = menú de sinks (JBL / Bocina / ...)
 *   [WiFi] [BT] [Power] ...           ← tiles nativos
 *   [🎧 audioPro  >]                  ← tile detalles: batería + EQ
 *
 * FIX vs versión anterior:
 *   ❌ _grid.addItem → falla silenciosamente (QuickSettingsItem fuera del flujo)
 *   ❌ instanceof Gvc.MixerSinkInput → en GJS lookup_stream_id retorna MixerStream base
 *   ✅ addExternalIndicator con container único que maneja filas internamente
 *   ✅ filtro por get_sink_inputs().some(si => si.id === id) en vez de instanceof
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
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

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

// ─────────────────────────────────────────────────────────────────────────────
// StreamRow: un widget por app activa (NO es QuickSettingsItem, es St.BoxLayout)
// ─────────────────────────────────────────────────────────────────────────────
class StreamRow {
    constructor(stream, mixer) {
        this.id        = stream.id;
        this._stream   = stream;
        this._mixer    = mixer;
        this._volMax   = mixer.get_vol_max_norm();
        this._notifyId = 0;
        this._sliderId = 0;
        this._sinkMenu = null;
        this._pressId  = 0;

        // Contenedor con el mismo CSS que usa el slider nativo de GNOME
        this.actor = new St.BoxLayout({
            style_class: 'quick-slider-bin',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });

        // Ícono real de la app (GvcMixerStream tiene icon_name desde PipeWire)
        const iconName = stream.icon_name || 'audio-volume-medium-symbolic';
        this._iconBtn = new St.Button({
            style_class: 'icon-button flat',
            can_focus: true,
            y_align: Clutter.ActorAlign.CENTER,
            child: new St.Icon({
                gicon: Gio.ThemedIcon.new_with_default_fallbacks(iconName),
                fallback_icon_name: 'audio-volume-medium-symbolic',
                icon_size: 16,
            }),
        });
        this._iconBtn.connect('clicked', () => this._openSinkMenu());
        this.actor.add_child(this._iconBtn);

        // Nombre de la app
        const name = (stream.description || stream.name || 'App').slice(0, 20);
        this.actor.add_child(new St.Label({
            text: name,
            y_align: Clutter.ActorAlign.CENTER,
            style: 'min-width: 90px; padding-right: 6px; font-size: 0.85em;',
        }));

        // Slider de volumen (Slider es de ui/slider.js, no de gi://St)
        this._slider = new Slider(stream.volume / this._volMax);
        this._slider.x_expand = true;
        this._sliderId = this._slider.connect('notify::value', () => {
            this._stream.volume = Math.round(this._slider.value * this._volMax);
            this._stream.push_volume();
            this._pct.text = `${Math.round(this._slider.value * 100)}%`;
        });
        this.actor.add_child(this._slider);

        // Porcentaje
        this._pct = new St.Label({
            text: `${Math.round(stream.volume / this._volMax * 100)}%`,
            y_align: Clutter.ActorAlign.CENTER,
            style: 'min-width: 38px; text-align: right; font-size: 0.8em;',
        });
        this.actor.add_child(this._pct);

        // Sincronizar cuando el volumen cambia desde otra fuente
        this._notifyId = stream.connect('notify::volume', () => {
            const pct = stream.volume / this._volMax;
            if (Math.abs(this._slider.value - pct) > 0.01) {
                this._slider.value = pct;
                this._pct.text = `${Math.round(pct * 100)}%`;
            }
        });
    }

    // ── Menú de selección de sink ──────────────────────────────────────────
    _openSinkMenu() {
        this._closeSinkMenu();
        const sinks = this._mixer.get_sinks();
        if (!sinks?.length) return;

        // St.Side.TOP evita que el menú quede fuera de pantalla en el panel superior
        this._sinkMenu = new PopupMenu.PopupMenu(this._iconBtn, 0.0, St.Side.TOP);
        Main.uiGroup.add_child(this._sinkMenu.actor);
        this._sinkMenu.actor.add_style_class_name('popup-menu');

        for (const sink of sinks) {
            const label = (sink.description || sink.name || '?').slice(0, 40);
            const item = new PopupMenu.PopupMenuItem(label);
            item.connect('activate', () => {
                this._moveSinkInput(sink);
                this._closeSinkMenu();
            });
            this._sinkMenu.addMenuItem(item);
        }

        // Cerrar al hacer click fuera del menú
        this._pressId = global.stage.connect('button-press-event', (_stage, event) => {
            const src = event.get_source();
            let actor = src;
            while (actor) {
                if (actor === this._sinkMenu?.actor) return;
                actor = actor.get_parent?.();
            }
            this._closeSinkMenu();
        });

        this._sinkMenu.open(true);
    }

    _moveSinkInput(sink) {
        // 1. pactl move-sink-input — mueve el stream ahora mismo
        try {
            Gio.Subprocess.new(
                ['pactl', 'move-sink-input', `${this._stream.id}`, sink.name || `${sink.id}`],
                Gio.SubprocessFlags.STDERR_SILENCE
            );
        } catch (e) {
            console.warn(`[audioPro] pactl: ${e}`);
        }

        // 2. pw-metadata target.object — PINEA el stream a ese sink en WirePlumber.
        //    WirePlumber detecta este cambio y lo guarda en su state file, así la
        //    próxima vez que abras la app va al mismo dispositivo. También evita que
        //    WirePlumber mueva el stream cuando cambia el default sink.
        //    Necesitamos el node.id del stream (PW) y el object.serial del sink.
        this._pinStreamToPWSink(sink);
    }

    _pinStreamToPWSink(sink) {
        // Obtener el PipeWire node-id del stream y el object.serial del sink via pw-dump
        try {
            const proc = Gio.Subprocess.new(
                ['pw-dump'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
            );
            proc.communicate_utf8_async(null, null, (_p, res) => {
                try {
                    const [, out] = _p.communicate_utf8_finish(res);
                    const nodes = JSON.parse(out);

                    // Buscar el stream node por PA sink-input index (coincide con pa.id)
                    const streamNode = nodes.find(n =>
                        n.info?.props?.['pulse.id'] === this._stream.id ||
                        n.info?.props?.['object.id'] === this._stream.id
                    );
                    // Buscar el sink node por PA sink index
                    const sinkNode = nodes.find(n =>
                        n.info?.props?.['pulse.id'] === sink.id ||
                        (n.info?.props?.['node.name'] === sink.name &&
                         n.info?.props?.['media.class']?.includes('Sink'))
                    );

                    if (!streamNode || !sinkNode) return;

                    const nodeId = streamNode.id;
                    const sinkSerial = sinkNode.info?.props?.['object.serial'] ?? sinkNode.id;

                    Gio.Subprocess.new(
                        ['pw-metadata', `${nodeId}`, 'target.object', `${sinkSerial}`],
                        Gio.SubprocessFlags.STDERR_SILENCE
                    );
                } catch (_) {}
            });
        } catch (_) {}
    }

    _closeSinkMenu() {
        if (this._pressId) {
            global.stage.disconnect(this._pressId);
            this._pressId = 0;
        }
        if (this._sinkMenu) {
            this._sinkMenu.destroy();
            this._sinkMenu = null;
        }
    }

    destroy() {
        this._closeSinkMenu();
        if (this._notifyId && this._stream) {
            try { this._stream.disconnect(this._notifyId); } catch (_) {}
            this._notifyId = 0;
        }
        if (this._sliderId && this._slider) {
            try { this._slider.disconnect(this._sliderId); } catch (_) {}
            this._sliderId = 0;
        }
        this.actor.destroy();
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// StreamsContainer: QuickSettingsItem que contiene todas las filas de streams
// Registrado via addExternalIndicator(indicator, 2) → colSpan=2 = ancho completo
// ─────────────────────────────────────────────────────────────────────────────
const StreamsContainer = GObject.registerClass(
class StreamsContainer extends QuickSettings.QuickSettingsItem {
    _init() {
        super._init({
            style_class: '',   // sin fondo de tile
            reactive: false,
            can_focus: false,
        });

        this._vbox = new St.BoxLayout({
            vertical: true,
            x_expand: true,
            style: 'spacing: 0px;',
        });
        this.child = this._vbox;
        this.hide();  // oculto hasta que aparezca el primer stream
    }

    addRow(row) {
        this._vbox.add_child(row.actor);
        this.show();
    }

    removeRow(row) {
        try { this._vbox.remove_child(row.actor); } catch (_) {}
        if (!this._vbox.get_first_child()) this.hide();
    }
});

// ─────────────────────────────────────────────────────────────────────────────
// StreamsIndicator: SystemIndicator que expone el container al panel
// ─────────────────────────────────────────────────────────────────────────────
const StreamsIndicator = GObject.registerClass(
class StreamsIndicator extends QuickSettings.SystemIndicator {
    _init() {
        super._init();
        this._rows = new Map();  // streamId → StreamRow
        this._container = new StreamsContainer();
        this.quickSettingsItems.push(this._container);
    }

    loadAll(mixer) {
        try {
            for (const s of mixer.get_sink_inputs())
                this._addIfNew(s, mixer);
        } catch (_) {}
    }

    tryAdd(id, mixer) {
        try {
            // Verificar que es un sink input (app de audio) y no un sink de hardware
            // Usamos get_sink_inputs() en vez de instanceof para evitar problemas de tipos GJS
            const inputs = mixer.get_sink_inputs();
            const isSinkInput = inputs.some(si => si.id === id);
            if (!isSinkInput) return;

            const s = mixer.lookup_stream_id(id);
            if (s) this._addIfNew(s, mixer);
        } catch (_) {}
    }

    _addIfNew(stream, mixer) {
        if (!stream || this._rows.has(stream.id)) return;
        if ((stream.name || '').includes('audiopro-qs')) return;
        const row = new StreamRow(stream, mixer);
        this._container.addRow(row);
        this._rows.set(stream.id, row);
    }

    removeStream(id) {
        const row = this._rows.get(id);
        if (!row) return;
        this._rows.delete(id);
        this._container.removeRow(row);
        row.destroy();
    }

    destroy() {
        for (const row of this._rows.values()) {
            this._container.removeRow(row);
            row.destroy();
        }
        this._rows.clear();
        this.quickSettingsItems.forEach(i => i.destroy());
        super.destroy();
    }
});

// ─────────────────────────────────────────────────────────────────────────────
// AudioProToggle: tile para detalles de dispositivos + EQ
// ─────────────────────────────────────────────────────────────────────────────
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

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Dispositivos'));
        this._deviceSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._deviceSection);
        this._noDevicesItem = new PopupMenu.PopupMenuItem('Sin dispositivos', {
            reactive: false, style_class: 'popup-inactive-menu-item',
        });
        this._deviceSection.addMenuItem(this._noDevicesItem);

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
        try {
            GLib.mkdir_with_parents(CONFIG_DIR, 0o755);
            GLib.file_set_contents(EQ_FILE, id);
        } catch (_) {}
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

// ─────────────────────────────────────────────────────────────────────────────
// Extension entry point
// ─────────────────────────────────────────────────────────────────────────────
export default class AudioProExtension extends Extension {
    enable() {
        this._mixer   = null;
        this._stateId = 0;
        this._addedId = 0;
        this._removedId = 0;

        // 1. Indicator de streams (full-width, colSpan=2)
        this._streamsIndicator = new StreamsIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(
            this._streamsIndicator, 2
        );
        // Mover las filas de streams entre el slider de volumen y el de brillo
        this._repositionStreams();

        // 2. Tile de audioPro (batería + EQ) — va al final (entre los tiles)
        this._audioProIndicator = new AudioProIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(
            this._audioProIndicator
        );

        // 3. Gvc mixer para leer streams activos
        this._initMixer();
    }

    // Mueve el StreamsContainer para que quede justo debajo del slider de volumen
    // (primer hijo del grid) y encima del slider de brillo (segundo hijo).
    // QuickSettingsLayout itera container.get_children() en orden, así que
    // set_child_above_sibling funciona para cambiar la posición en el layout.
    _repositionStreams() {
        try {
            const grid = Main.panel.statusArea.quickSettings._grid;
            const container = this._streamsIndicator._container;
            if (!grid || !container) return;
            const firstItem = grid.get_first_child();
            if (firstItem && firstItem !== container)
                grid.set_child_above_sibling(container, firstItem);
        } catch (_) {}
    }

    disable() {
        // Cleanup mixer
        if (this._mixer) {
            try {
                if (this._stateId)   this._mixer.disconnect(this._stateId);
                if (this._addedId)   this._mixer.disconnect(this._addedId);
                if (this._removedId) this._mixer.disconnect(this._removedId);
                this._mixer.close();
            } catch (_) {}
            this._mixer = null;
        }

        // Cleanup indicadores
        this._streamsIndicator?.destroy();
        this._streamsIndicator = null;

        this._audioProIndicator?.destroy();
        this._audioProIndicator = null;
    }

    _initMixer() {
        this._mixer = new Gvc.MixerControl({ name: 'audiopro-qs' });

        // state-changed: carga streams cuando la conexión a PipeWire está lista
        this._stateId = this._mixer.connect('state-changed', (_c, state) => {
            if (state === Gvc.MixerControlState.READY)
                this._streamsIndicator.loadAll(this._mixer);
        });

        // stream-added: stream nuevo (hardware o app)
        // tryAdd filtra: solo agrega si el id aparece en get_sink_inputs() (apps)
        this._addedId = this._mixer.connect('stream-added', (_c, id) => {
            this._streamsIndicator.tryAdd(id, this._mixer);
        });

        // stream-removed: app cerrada o silenciada
        this._removedId = this._mixer.connect('stream-removed', (_c, id) => {
            this._streamsIndicator.removeStream(id);
        });

        this._mixer.open();

        // Fallback: el mixer puede estar en READY antes del tick del event loop
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            try {
                if (this._streamsIndicator && this._mixer)
                    this._streamsIndicator.loadAll(this._mixer);
            } catch (_) {}
            return GLib.SOURCE_REMOVE;
        });
    }
}
