"""Reglas de routing automático y presets de Cinema."""
from __future__ import annotations

from datetime import datetime

from .database import get_db, now_iso, to_json, from_json


# ── Routing rules ─────────────────────────────────────────────────────────

def get_active_rules() -> list[dict]:
    """Devuelve reglas activas ordenadas por prioridad."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM routing_rules WHERE active=1 ORDER BY priority DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def find_rule_for_app(app_name: str) -> dict | None:
    """
    Busca la regla más prioritaria para una app en este momento.
    Considera el horario si está configurado.
    """
    now = datetime.now()
    day_bit = str(now.weekday())   # 0=lunes, 6=domingo
    time_str = now.strftime("%H:%M")

    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM routing_rules
               WHERE active=1 AND (app_name=? OR app_name='*')
               ORDER BY priority DESC""",
            (app_name,),
        ).fetchall()

    for row in rows:
        rule = dict(row)
        # Verificar día
        days = rule.get("days", "1111111")
        if len(days) > int(day_bit) and days[int(day_bit)] != "1":
            continue
        # Verificar horario
        t_start = rule.get("time_start")
        t_end   = rule.get("time_end")
        if t_start and t_end:
            if t_start <= t_end:
                if not (t_start <= time_str <= t_end):
                    continue
            else:  # cruza medianoche
                if not (time_str >= t_start or time_str <= t_end):
                    continue
        return rule
    return None


def save_rule(app_name: str, sink_name: str, name: str = "",
              time_start: str | None = None, time_end: str | None = None,
              days: str = "1111111", priority: int = 0) -> int:
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO routing_rules
               (name, app_name, sink_name, time_start, time_end, days, priority, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, app_name, sink_name, time_start, time_end, days, priority, now_iso()),
        )
        return cur.lastrowid


def delete_rule(rule_id: int) -> None:
    with get_db() as db:
        db.execute("DELETE FROM routing_rules WHERE id=?", (rule_id,))


# ── Cinema presets ────────────────────────────────────────────────────────

def save_cinema_preset(name: str, assignments: dict,
                       file_path: str | None = None) -> int:
    """Guarda una configuración de Cinema (dispositivo → pista)."""
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO cinema_presets(name, file_path, assignments, created_at)
               VALUES (?, ?, ?, ?)""",
            (name, file_path, to_json(assignments), now_iso()),
        )
        return cur.lastrowid


def get_cinema_presets(file_path: str | None = None) -> list[dict]:
    with get_db() as db:
        if file_path:
            rows = db.execute(
                "SELECT * FROM cinema_presets WHERE file_path=? ORDER BY last_used DESC",
                (file_path,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM cinema_presets ORDER BY used_count DESC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["assignments"] = from_json(d.get("assignments"))
            result.append(d)
        return result


def use_cinema_preset(preset_id: int) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE cinema_presets SET used_count=used_count+1, last_used=? WHERE id=?",
            (now_iso(), preset_id),
        )
