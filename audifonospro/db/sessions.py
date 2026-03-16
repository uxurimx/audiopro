"""Registro de sesiones de uso (traductor, cinema, monitor)."""
from __future__ import annotations

from .database import get_db, now_iso, to_json


def start_session(mode: str, src_lang: str = "", dst_lang: str = "",
                  quality: str = "", device_ids: list[str] | None = None) -> int:
    """Inicia una sesión y devuelve su ID."""
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO sessions(started_at, mode, src_lang, dst_lang, quality, device_ids)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now_iso(), mode, src_lang, dst_lang, quality, to_json(device_ids or [])),
        )
        return cur.lastrowid


def end_session(session_id: int) -> None:
    with get_db() as db:
        db.execute("UPDATE sessions SET ended_at=? WHERE id=?",
                   (now_iso(), session_id))


def get_recent_sessions(limit: int = 20) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT id, started_at, ended_at, mode, src_lang, dst_lang, quality,
                      ROUND((julianday(COALESCE(ended_at, datetime('now')))
                             - julianday(started_at)) * 1440) AS duration_min
               FROM sessions
               ORDER BY started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_weekly_stats() -> dict:
    """Estadísticas de la última semana."""
    with get_db() as db:
        row = db.execute("""
            SELECT
              COUNT(*) as total_sessions,
              SUM(ROUND((julianday(COALESCE(ended_at, datetime('now')))
                         - julianday(started_at)) * 60)) AS total_minutes,
              COUNT(CASE WHEN mode='translator' THEN 1 END) as translator_sessions,
              COUNT(CASE WHEN mode='cinema' THEN 1 END) as cinema_sessions
            FROM sessions
            WHERE started_at > datetime('now', '-7 days')
        """).fetchone()
        return dict(row) if row else {}
