"""SQLite database: connection, schema migrations and starter data.

Uses only the standard-library sqlite3 module, so it works the same on
Linux, Windows and Raspberry Pi. The schema version is kept in
`PRAGMA user_version`; each entry in MIGRATIONS moves it up by one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .repository import register_functions

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE storage (
        id        INTEGER PRIMARY KEY,
        name      TEXT    NOT NULL,
        nfc_tag   TEXT,                       -- optional tag/label identifier
        at_hand   INTEGER NOT NULL DEFAULT 0 CHECK (at_hand IN (0, 1)),
        created_at TEXT   NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT   NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX storage_name_uq ON storage (name COLLATE NOCASE);

    CREATE TABLE paint (
        id           INTEGER PRIMARY KEY,
        name         TEXT    NOT NULL,
        manufacturer TEXT    NOT NULL,
        code         TEXT,
        hex          TEXT    CHECK (hex IS NULL OR hex GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
        quantity     INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
        storage_id   INTEGER REFERENCES storage (id) ON DELETE SET NULL,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX paint_identity_uq
        ON paint (manufacturer COLLATE NOCASE, name COLLATE NOCASE, ifnull(code, ''));
    CREATE INDEX paint_storage_ix ON paint (storage_id);

    CREATE TABLE tag (
        id    INTEGER PRIMARY KEY,
        name  TEXT NOT NULL,
        color TEXT NOT NULL DEFAULT '#888888'
              CHECK (color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]')
    );
    CREATE UNIQUE INDEX tag_name_uq ON tag (name COLLATE NOCASE);

    CREATE TABLE paint_tag (
        paint_id INTEGER NOT NULL REFERENCES paint (id) ON DELETE CASCADE,
        tag_id   INTEGER NOT NULL REFERENCES tag (id)   ON DELETE CASCADE,
        PRIMARY KEY (paint_id, tag_id)
    );
    CREATE INDEX paint_tag_tag_ix ON paint_tag (tag_id);

    -- words on a label that suggest a tag (used by photo recognition later)
    CREATE TABLE tag_keyword (
        keyword TEXT    NOT NULL,
        tag_id  INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
        PRIMARY KEY (keyword, tag_id)
    );
    """,
]

# Starter tags: (name, plaque color, label keywords)
STARTER_TAGS: list[tuple[str, str, list[str]]] = [
    ("Грунт", "#9e9e9e", ["primer"]),
    ("Основа", "#5c6bc0", ["base"]),
    ("Слой", "#42a5f5", ["layer"]),
    ("Контраст", "#ab47bc", ["contrast", "speedpaint"]),
    ("Проливка", "#8d6e63", ["shade", "wash"]),
    ("Спецэффект", "#26a69a", ["technical", "effect"]),
    ("Металлик", "#bdbdbd", ["metal", "metallic"]),
    ("Чернила", "#3949ab", ["ink"]),
    ("Лак", "#fdd835", ["varnish"]),
    ("Для аэрографа", "#ef6c00", ["air"]),
]


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    register_functions(conn)
    return conn


def init_db(db_path: Path) -> None:
    """Create the data folder and database if needed, apply pending migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            with conn:
                conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
            if number == 1:
                _seed_tags(conn)
    finally:
        conn.close()


def _seed_tags(conn: sqlite3.Connection) -> None:
    with conn:
        for name, color, keywords in STARTER_TAGS:
            tag_id = conn.execute(
                "INSERT INTO tag (name, color) VALUES (?, ?)", (name, color)
            ).lastrowid
            conn.executemany(
                "INSERT INTO tag_keyword (keyword, tag_id) VALUES (?, ?)",
                [(kw, tag_id) for kw in keywords],
            )
