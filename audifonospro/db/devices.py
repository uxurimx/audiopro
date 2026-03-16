"""Registro de dispositivos vistos y su historial."""
from __future__ import annotations

from .database import get_db, now_iso


def upsert_device(device_id: str, name: str, device_type: str,
                  mac_address: str | None = None) -> None:
    """Registra o actualiza un dispositivo. Se llama cada vez que aparece."""
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM devices WHERE id=?", (device_id,)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE devices SET name=?, last_seen=? WHERE id=?",
                (name, now_iso(), device_id),
            )
        else:
            db.execute(
                """INSERT INTO devices(id, name, type, mac_address, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (device_id, name, device_type, mac_address, now_iso(), now_iso()),
            )


def log_battery(device_id: str, percent: int) -> None:
    """Guarda una lectura de batería."""
    with get_db() as db:
        # Solo guardar si cambió más de 2% desde la última lectura
        last = db.execute(
            "SELECT percent FROM battery_log WHERE device_id=? ORDER BY timestamp DESC LIMIT 1",
            (device_id,),
        ).fetchone()
        if last is None or abs(last["percent"] - percent) >= 2:
            db.execute(
                "INSERT INTO battery_log(device_id, timestamp, percent) VALUES (?, ?, ?)",
                (device_id, now_iso(), percent),
            )


def get_battery_history(device_id: str, days: int = 7) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT timestamp, percent FROM battery_log
               WHERE device_id=? AND timestamp > datetime('now', ?)
               ORDER BY timestamp ASC""",
            (device_id, f"-{days} days"),
        ).fetchall()
        return [dict(r) for r in rows]


def get_known_devices() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT d.*, b.percent as last_battery
               FROM devices d
               LEFT JOIN battery_log b ON b.device_id = d.id
                 AND b.timestamp = (
                   SELECT MAX(timestamp) FROM battery_log WHERE device_id = d.id
                 )
               ORDER BY d.last_seen DESC""",
        ).fetchall()
        return [dict(r) for r in rows]
