"""Backups: a CSV list of paints, and a full dump to move the catalog to another machine.

CSV — for reading and spreadsheets: one row per paint, sorted by manufacturer and name,
with the color (hex) when it is set. It can't be imported back: storages, tags and
settings don't fit into a flat table.

Dump — a .zip with the whole database (paints, storages, tags, manufacturers, label
keywords, settings including the sign-in password) and the photos folder. Restoring it
replaces the current catalog; the current one is copied aside first.

    paint-accountant-2026-10-30.zip
    ├── manifest.json      app version, schema version, date
    ├── paints.sqlite3     the database
    └── photos/…           pictures, if any
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import __version__
from . import repository as repo
from .config import Settings
from .db import MIGRATIONS, connect, init_db

DB_NAME = "paints.sqlite3"
MANIFEST = "manifest.json"
MAX_DUMP_BYTES = 500 * 1024 * 1024  # uncompressed; a catalog is a few MB, photos add more

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
            f"{loc['storage']['name'] if loc['storage'] else NO_STORAGE[lang]} ×{loc['quantity']}"
            for loc in p["locations"] if loc["quantity"]
        )
        writer.writerow([
            p["manufacturer"], p["name"], p["code"] or "", p["hex"] or "", p["quantity"],
            where, ", ".join(tg["name"] for tg in p["tags"]), p["barcode"] or "",
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
            if settings.photos_dir.is_dir():
                for f in sorted(settings.photos_dir.rglob("*")):
                    if f.is_file():
                        z.write(f, "photos/" + f.relative_to(settings.photos_dir).as_posix())
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


def _unpack(data: bytes, into: Path) -> tuple[Path, Path | None]:
    """Database file and photos folder (if any) from a .zip dump or a bare .sqlite3 file."""
    db = into / DB_NAME
    if data[:16] == b"SQLite format 3\x00":
        db.write_bytes(data)
        return db, None
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BadDump("expected a .zip dump (or a .sqlite3 database)") from exc
    with z:
        names = z.namelist()
        if DB_NAME not in names:
            raise BadDump(f"no {DB_NAME} inside the archive — not a Paint Accountant dump")
        if sum(i.file_size for i in z.infolist()) > MAX_DUMP_BYTES:
            raise BadDump("the archive is too big")
        db.write_bytes(z.read(DB_NAME))
        photos = into / "photos"
        for info in z.infolist():
            rel = PurePosixPath(info.filename)
            if info.is_dir() or rel.parts[:1] != ("photos",) or len(rel.parts) < 2:
                continue
            if any(part in ("..", "") for part in rel.parts) or rel.is_absolute():
                raise BadDump("unsafe path inside the archive")
            target = into.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
        return db, (photos if photos.is_dir() else None)


def restore_dump(settings: Settings, data: bytes) -> dict:
    """Replace the catalog with the one from a dump. The current database is copied to
    paints.before-restore-<date-time>.sqlite3 next to it first (photos to photos.before-restore-…)."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    with tempfile.TemporaryDirectory() as tmp:
        new_db, new_photos = _unpack(data, Path(tmp))
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

        if new_photos is not None:
            if settings.photos_dir.is_dir() and any(settings.photos_dir.iterdir()):
                settings.photos_dir.rename(settings.photos_dir.with_name(f"photos.before-restore-{stamp}"))
            elif settings.photos_dir.is_dir():
                settings.photos_dir.rmdir()
            shutil.copytree(new_photos, settings.photos_dir)
        settings.ensure_dirs()

    return {"restored": True, "from_schema": info["schema_version"], "counts": counts, "previous": saved.name}
