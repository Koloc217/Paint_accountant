"""Backups: a CSV list of paints, and a full dump to move the catalog to another machine.

CSV — for reading and spreadsheets: one row per paint, sorted by manufacturer and name,
with the color (hex) when it is set. It can't be imported back: storages, tags and
settings don't fit into a flat table.

Dump — a .zip with the whole database (paints, storages, tags, manufacturers, label
keywords, settings including the sign-in password). Restoring it replaces the current
catalog; the current one is copied aside first.

    paint-accountant-2026-10-30.zip
    ├── manifest.json      app version, schema version, date
    └── paints.sqlite3     the database

Dumps made by earlier versions may also hold a photos/ folder; it was never filled and
is skipped on restore.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__
from . import repository as repo
from .config import Settings
from .db import MIGRATIONS, connect, init_db

DB_NAME = "paints.sqlite3"
MANIFEST = "manifest.json"
MAX_DUMP_BYTES = 100 * 1024 * 1024  # a catalog is a few MB

# ======================= CSV =======================

CSV_HEADERS = {
    "ru": ["Производитель", "Название", "Код", "Цвет (hex)", "Банок", "Где лежит", "Теги", "Штрихкод"],
    "en": ["Manufacturer", "Name", "Code", "Color (hex)", "Pots", "Where", "Tags", "Barcode"],
}
NO_STORAGE = {"ru": "без хранилища", "en": "no storage"}


def paints_csv(conn: sqlite3.Connection, lang: str = "ru") -> bytes:
    """Every paint, by manufacturer then name. UTF-8 with BOM so Excel shows Cyrillic;
    ';' for Russian (Excel with a Russian locale expects it), ',' for English."""
    lang = lang if lang in CSV_HEADERS else "ru"
    paints = sorted(repo.list_paints(conn),
                    key=lambda p: (p["manufacturer"].casefold(), p["name"].casefold(), p["code"] or ""))
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";" if lang == "ru" else ",", lineterminator="\r\n")
    writer.writerow(CSV_HEADERS[lang])
    for p in paints:
        where = ", ".join(
            f"{loc['storage']['names'].get(lang) or loc['storage']['name'] if loc['storage'] else NO_STORAGE[lang]} ×{loc['quantity']}"
            for loc in p["locations"] if loc["quantity"]
        )
        writer.writerow([
            p["manufacturer"], p["name"], p["code"] or "", p["hex"] or "", p["quantity"],
            where, ", ".join(tg["names"].get(lang) or tg["name"] for tg in p["tags"]), p["barcode"] or "",
        ])
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ======================= dump =======================

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def dump_filename() -> str:
    return f"paint-accountant-{_today()}.zip"


def make_dump(settings: Settings) -> bytes:
    """The whole catalog as a .zip — a consistent copy even while the app is running."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / DB_NAME
        src = connect(settings.db_path)
        dest = sqlite3.connect(copy)
        try:
            src.backup(dest)  # SQLite's online backup: consistent, WAL included
            dest.execute("PRAGMA journal_mode = DELETE")  # a single self-contained file
            version = dest.execute("PRAGMA user_version").fetchone()[0]
            counts = {t: dest.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                      for t in ("paint", "storage", "manufacturer", "tag")}
        finally:
            dest.close()
            src.close()
        manifest = {
            "app": "Paint Accountant",
            "app_version": __version__,
            "schema_version": version,
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "counts": counts,
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            z.write(copy, DB_NAME)
        return buf.getvalue()


class BadDump(ValueError):
    """The file is not a dump of this app, is damaged, or is from a newer version."""


def _check_db(path: Path) -> dict:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BadDump("the database in the file is damaged")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise BadDump("not a Paint Accountant database") from exc
    if not {"paint", "storage", "tag"} <= tables:
        raise BadDump("not a Paint Accountant database")
    if version > len(MIGRATIONS):
        raise BadDump(f"the dump is from a newer version of the app (schema {version}, "
                      f"this app knows {len(MIGRATIONS)}) — update the app first")
    return {"schema_version": version}


def _unpack(data: bytes, into: Path) -> Path:
    """The database file from a .zip dump or a bare .sqlite3 file. Nothing else is taken out."""
    db = into / DB_NAME
    if data[:16] == b"SQLite format 3\x00":
        db.write_bytes(data)
        return db
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BadDump("expected a .zip dump (or a .sqlite3 database)") from exc
    with z:
        names = z.namelist()
        if DB_NAME not in names:
            raise BadDump(f"no {DB_NAME} inside the archive — not a Paint Accountant dump")
        if z.getinfo(DB_NAME).file_size > MAX_DUMP_BYTES:
            raise BadDump("the archive is too big")
        db.write_bytes(z.read(DB_NAME))
        return db


def restore_dump(settings: Settings, data: bytes) -> dict:
    """Replace the catalog with the one from a dump. The current database is copied to
    paints.before-restore-<date-time>.sqlite3 next to it first."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    with tempfile.TemporaryDirectory() as tmp:
        new_db = _unpack(data, Path(tmp))
        info = _check_db(new_db)
        init_db(new_db)  # an older dump is brought up to this app's schema, in the temp copy

        live = connect(settings.db_path)
        try:
            saved = settings.db_path.with_name(f"{settings.db_path.stem}.before-restore-{stamp}{settings.db_path.suffix}")
            keep = sqlite3.connect(saved)
            try:
                live.backup(keep)
            finally:
                keep.close()
            src = sqlite3.connect(new_db)
            try:
                src.backup(live)  # page by page into the open database: other connections see it at once
            finally:
                src.close()
            counts = {t: live.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                      for t in ("paint", "storage", "manufacturer", "tag")}
        finally:
            live.close()

    return {"restored": True, "from_schema": info["schema_version"], "counts": counts, "previous": saved.name}
