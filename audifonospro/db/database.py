"""
Capa de acceso a datos — SQLite local.

El archivo vive en:
  ~/.local/share/audifonospro/audifonospro.db

No hay servidor, no hay red. El archivo ES la base de datos.
Se puede copiar, respaldar o abrir con DB Browser for SQLite.

Migrations automáticas: al arrancar compara el SCHEMA_VERSION
actual contra el del archivo y aplica los cambios necesarios.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Paths ─────────────────────────────────────────────────────────────────
_DATA_DIR = Path.home() / ".local" / "share" / "audifonospro"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = _DATA_DIR / "audifonospro.db"

# Incrementa este número cada vez que cambies el schema
SCHEMA_VERSION = 1


# ── Conexión ──────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    """
    Context manager que entrega una conexión sqlite3 lista para usar.

    - WAL mode: múltiples lectores concurrentes, escrituras no bloquean UI
    - row_factory: resultados como dicts (row["campo"] en vez de row[0])
    - Commit automático al salir del bloque; rollback en excepción
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema + Migrations ───────────────────────────────────────────────────

_SCHEMA_V1 = """
-- ── Sesiones de uso ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    mode        TEXT NOT NULL,   -- translator | cinema | monitor
    src_lang    TEXT,
    dst_lang    TEXT,
    quality     TEXT,            -- local | balanced | high
    device_ids  TEXT             -- JSON array de IDs de dispositivos
);

-- ── Frases traducidas ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS phrases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    timestamp   TEXT NOT NULL,
    original    TEXT NOT NULL,
    translated  TEXT NOT NULL,
    src_lang    TEXT,
    dst_lang    TEXT,
    stt_ms      INTEGER,
    trans_ms    INTEGER,
    tts_ms      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_phrases_session  ON phrases(session_id);
CREATE INDEX IF NOT EXISTS idx_phrases_ts       ON phrases(timestamp);

-- ── Dispositivos vistos ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS devices (
    id              TEXT PRIMARY KEY,   -- pw_node_name o mac_address
    name            TEXT NOT NULL,
    type            TEXT,               -- bluetooth | jack | builtin | hdmi
    mac_address     TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    total_sessions  INTEGER DEFAULT 0,
    avg_latency_ms  INTEGER,
    notes           TEXT
);

-- ── Log de batería ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS battery_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT REFERENCES devices(id) ON DELETE CASCADE,
    timestamp   TEXT NOT NULL,
    percent     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_battery_device ON battery_log(device_id, timestamp);

-- ── Reglas de routing automático ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS routing_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    app_name    TEXT NOT NULL,          -- "Spotify", "Firefox", "*"
    sink_name   TEXT NOT NULL,          -- nombre del sink destino
    time_start  TEXT,                   -- "22:00" o NULL = siempre
    time_end    TEXT,
    days        TEXT DEFAULT '1111111', -- lun-dom, '1' = activo
    priority    INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL
);

-- ── Presets de Cinema ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cinema_presets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    file_path   TEXT,
    assignments TEXT NOT NULL,  -- JSON: {"sink_name": track_idx}
    used_count  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    last_used   TEXT
);

-- ── Perfiles por persona ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    trigger_mac TEXT,           -- auto-carga cuando este MAC conecta
    eq_preset   TEXT DEFAULT 'flat',
    anc_level   INTEGER DEFAULT 0,
    src_lang    TEXT DEFAULT '',
    dst_lang    TEXT DEFAULT 'English',
    quality     TEXT DEFAULT 'balanced',
    volume      INTEGER DEFAULT 80,
    created_at  TEXT NOT NULL,
    last_used   TEXT
);

-- ── Metadata / versión ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db() -> None:
    """
    Inicializa la DB y aplica migrations.
    Llama esto una vez al arrancar la app.
    """
    with get_db() as db:
        # Leer versión actual
        db.executescript(_SCHEMA_V1)

        row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        current = int(row["value"]) if row else 0

        if current < SCHEMA_VERSION:
            db.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            db.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('created_at', ?)",
                (datetime.now().isoformat(),),
            )


# ── Helpers ───────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def from_json(s: str | None) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


def get_stat(key: str) -> str | None:
    with get_db() as db:
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
