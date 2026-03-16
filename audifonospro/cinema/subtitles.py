"""
Descarga automática de subtítulos vía OpenSubtitles XML-RPC.

Sin dependencias externas — usa solo stdlib (xmlrpc.client, urllib, gzip, struct).

API:
    results = search(video_path, languages=["es", "en"])
    # → [{"SubFileName": ..., "SubDownloadLink": ..., "LanguageName": ...}, ...]

    saved_path = download(result_entry, dest_dir)
    # → "/ruta/al/archivo.es.srt"
"""
from __future__ import annotations

import gzip
import os
import struct
import urllib.request
import xmlrpc.client

# OpenSubtitles XML-RPC endpoint (compatible con la API v1 gratuita)
_API_URL   = "https://api.opensubtitles.org/xml-rpc"
_USERAGENT = "audifonospro v1.0"

# ISO 639-1 → ISO 639-2/B (OpenSubtitles usa códigos de 3 letras)
_LANG_MAP: dict[str, str] = {
    "es": "spa", "en": "eng", "fr": "fre", "de": "ger",
    "it": "ita", "pt": "por", "ru": "rus", "ja": "jpn",
    "ko": "kor", "zh": "chi", "ar": "ara", "nl": "dut",
}


# ── Hash del archivo (algoritmo propio de OpenSubtitles) ──────────────────────

def compute_hash(path: str) -> tuple[str, int]:
    """
    Calcula el hash de archivo según el algoritmo de OpenSubtitles.
    Devuelve (hash_hex_16chars, filesize_bytes).
    """
    fmt      = "<q"   # little-endian signed 64-bit
    blk_size = struct.calcsize(fmt)
    filesize = os.path.getsize(path)
    hash_val = filesize

    with open(path, "rb") as f:
        # Primeros 64 KB
        for _ in range(65536 // blk_size):
            buf = f.read(blk_size)
            if len(buf) < blk_size:
                break
            (val,) = struct.unpack(fmt, buf)
            hash_val = (hash_val + val) & 0xFFFFFFFFFFFFFFFF

        # Últimos 64 KB
        f.seek(max(0, filesize - 65536))
        for _ in range(65536 // blk_size):
            buf = f.read(blk_size)
            if len(buf) < blk_size:
                break
            (val,) = struct.unpack(fmt, buf)
            hash_val = (hash_val + val) & 0xFFFFFFFFFFFFFFFF

    return f"{hash_val:016x}", filesize


# ── Búsqueda ──────────────────────────────────────────────────────────────────

def search(
    video_path: str,
    languages: list[str] | None = None,
) -> list[dict]:
    """
    Busca subtítulos para el video dado.

    Estrategia:
      1. Búsqueda por hash de archivo (más precisa)
      2. Fallback a búsqueda por nombre de archivo si el hash no devuelve nada

    Parámetros:
        video_path: ruta al archivo de video
        languages:  lista de códigos ISO 639-1, p.ej. ["es", "en"]

    Devuelve lista de dicts de OpenSubtitles (cada uno tiene SubFileName,
    SubDownloadLink, LanguageName, SubRating, etc.).
    """
    if languages is None:
        languages = ["es", "en"]

    lang_str = ",".join(_LANG_MAP.get(l, l) for l in languages)
    server   = xmlrpc.client.ServerProxy(_API_URL, allow_none=True)

    login_res = server.LogIn("", "", "es", _USERAGENT)
    if login_res.get("status") != "200 OK":
        raise RuntimeError(f"OpenSubtitles login falló: {login_res.get('status')}")

    token = login_res["token"]
    try:
        # Intento 1: por hash
        file_hash, file_size = compute_hash(video_path)
        params = [{"sublanguageid": lang_str, "moviehash": file_hash,
                   "moviebytesize": str(file_size)}]
        res  = server.SearchSubtitles(token, params)
        subs = res.get("data") or []

        # Intento 2: por nombre si el hash no encontró nada
        if not subs:
            query = os.path.splitext(os.path.basename(video_path))[0]
            params = [{"sublanguageid": lang_str, "query": query}]
            res  = server.SearchSubtitles(token, params)
            subs = res.get("data") or []

        return subs if isinstance(subs, list) else []

    finally:
        try:
            server.LogOut(token)
        except Exception:
            pass


# ── Descarga ──────────────────────────────────────────────────────────────────

def download(sub: dict, dest_dir: str) -> str:
    """
    Descarga un subtítulo y lo guarda en dest_dir.

    Descomprime automáticamente si viene en .gz (OpenSubtitles siempre comprime).
    Devuelve la ruta al archivo .srt/.ass guardado.
    """
    url      = sub.get("SubDownloadLink", "")
    filename = sub.get("SubFileName", "subtitle.srt")

    # Destino final (sin .gz si lo tiene)
    dest = os.path.join(dest_dir, filename)
    if dest.endswith(".gz"):
        dest = dest[:-3]

    req = urllib.request.Request(url, headers={"User-Agent": _USERAGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()

    # Descomprimir si es gzip
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)

    with open(dest, "wb") as f:
        f.write(data)

    return dest


# ── Descarga inteligente (todo en uno) ────────────────────────────────────────

def auto_download(
    video_path: str,
    languages: list[str] | None = None,
    dest_dir: str | None = None,
) -> str:
    """
    Busca y descarga el mejor subtítulo disponible.

    Prioriza: español > inglés, mayor puntuación.

    Devuelve la ruta al archivo descargado, o lanza RuntimeError si no hay nada.
    """
    if dest_dir is None:
        dest_dir = os.path.dirname(video_path)

    subs = search(video_path, languages or ["es", "en"])
    if not subs:
        raise RuntimeError("No se encontraron subtítulos para este archivo")

    # Ordenar: primero por idioma preferido, luego por calificación
    lang_pref = (languages or ["es", "en"])
    def _sort_key(s: dict) -> tuple:
        lang  = s.get("ISO639", "")
        pref  = lang_pref.index(lang) if lang in lang_pref else 99
        score = float(s.get("SubRating", 0) or 0)
        return (pref, -score)

    subs.sort(key=_sort_key)
    return download(subs[0], dest_dir)
