"""
audifonospro.db — persistencia local con SQLite.

Uso típico:
    from audifonospro.db import init_db
    from audifonospro.db.phrases import save_phrase, get_recent_phrases
    from audifonospro.db.sessions import start_session, end_session
    from audifonospro.db.devices import upsert_device, log_battery

La DB se crea automáticamente en:
    ~/.local/share/audifonospro/audifonospro.db
"""
from .database import init_db, DB_PATH

__all__ = ["init_db", "DB_PATH"]
