"""Historial de frases traducidas."""
from __future__ import annotations

from .database import get_db, now_iso


def save_phrase(
    session_id: int | None,
    original: str,
    translated: str,
    src_lang: str = "",
    dst_lang: str = "",
    stt_ms: int = 0,
    trans_ms: int = 0,
    tts_ms: int = 0,
) -> int:
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO phrases
               (session_id, timestamp, original, translated,
                src_lang, dst_lang, stt_ms, trans_ms, tts_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, now_iso(), original, translated,
             src_lang, dst_lang, stt_ms, trans_ms, tts_ms),
        )
        return cur.lastrowid


def search_phrases(query: str, limit: int = 50) -> list[dict]:
    """Búsqueda full-text en original y traducción."""
    like = f"%{query}%"
    with get_db() as db:
        rows = db.execute(
            """SELECT id, timestamp, original, translated, src_lang, dst_lang,
                      (stt_ms + trans_ms + tts_ms) AS total_ms
               FROM phrases
               WHERE original LIKE ? OR translated LIKE ?
               ORDER BY timestamp DESC LIMIT ?""",
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_phrases(limit: int = 30) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """SELECT id, timestamp, original, translated, src_lang, dst_lang,
                      stt_ms, trans_ms, tts_ms,
                      (stt_ms + trans_ms + tts_ms) AS total_ms
               FROM phrases ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latency_stats() -> dict:
    """Promedios de latencia por etapa."""
    with get_db() as db:
        row = db.execute("""
            SELECT
              COUNT(*) as total_phrases,
              ROUND(AVG(stt_ms))   as avg_stt_ms,
              ROUND(AVG(trans_ms)) as avg_trans_ms,
              ROUND(AVG(tts_ms))   as avg_tts_ms,
              ROUND(AVG(stt_ms + trans_ms + tts_ms)) as avg_total_ms
            FROM phrases
            WHERE timestamp > datetime('now', '-30 days')
        """).fetchone()
        return dict(row) if row else {}


def get_top_languages() -> list[dict]:
    with get_db() as db:
        rows = db.execute("""
            SELECT src_lang, dst_lang, COUNT(*) as count
            FROM phrases
            GROUP BY src_lang, dst_lang
            ORDER BY count DESC LIMIT 5
        """).fetchall()
        return [dict(r) for r in rows]
