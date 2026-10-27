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
    # 2 — manufacturers become their own table; paint.manufacturer (text)
    #     is replaced by paint.manufacturer_id. Existing names are kept.
    """
    CREATE TABLE manufacturer (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX manufacturer_name_uq ON manufacturer (name COLLATE NOCASE);

    INSERT INTO manufacturer (name) VALUES
        ('AK'), ('Citadel'), ('Green Stuff World'), ('Kaleido'),
        ('Kimera'), ('Tamiya'), ('Vallejo');

    -- names already used by paints, one row per name ignoring case
    INSERT INTO manufacturer (name)
        SELECT min(trim(manufacturer)) FROM paint
        WHERE py_lower(trim(manufacturer)) NOT IN (SELECT py_lower(name) FROM manufacturer)
        GROUP BY py_lower(trim(manufacturer));

    CREATE TABLE paint_new (
        id              INTEGER PRIMARY KEY,
        name            TEXT    NOT NULL,
        manufacturer_id INTEGER NOT NULL REFERENCES manufacturer (id) ON DELETE RESTRICT,
        code            TEXT,
        hex             TEXT    CHECK (hex IS NULL OR hex GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
        quantity        INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
        storage_id      INTEGER REFERENCES storage (id) ON DELETE SET NULL,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    INSERT INTO paint_new (id, name, manufacturer_id, code, hex, quantity, storage_id, created_at, updated_at)
        SELECT p.id, p.name, m.id, p.code, p.hex, p.quantity, p.storage_id, p.created_at, p.updated_at
        FROM paint p JOIN manufacturer m ON py_lower(m.name) = py_lower(trim(p.manufacturer));
    DROP TABLE paint;
    ALTER TABLE paint_new RENAME TO paint;

    CREATE UNIQUE INDEX paint_identity_uq ON paint (manufacturer_id, name COLLATE NOCASE, ifnull(code, ''));
    CREATE INDEX paint_storage_ix ON paint (storage_id);
    CREATE INDEX paint_manufacturer_ix ON paint (manufacturer_id);
    """,
    # 3 — barcode on the pot, remembered when a paint is added by photo
    """
    ALTER TABLE paint ADD COLUMN barcode TEXT;
    CREATE UNIQUE INDEX paint_barcode_uq ON paint (barcode) WHERE barcode IS NOT NULL;
    """,
    # 4 — a paint can lie in several storages ("at hand 1, Запас 2").
    #     Pots move from paint.quantity/storage_id to paint_location.
    #     One storage is the default (an "at hand" one), and a storage can send
    #     second and further pots of the same paint to its "copies" storage.
    """
    ALTER TABLE storage ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1));
    ALTER TABLE storage ADD COLUMN copies_storage_id INTEGER REFERENCES storage (id) ON DELETE SET NULL;
    CREATE UNIQUE INDEX storage_default_uq ON storage (is_default) WHERE is_default = 1;

    -- the default "at hand" storage: an existing one if there is, else a new "Под рукой"
    UPDATE storage SET at_hand = 1
        WHERE py_lower(name) = 'под рукой' AND NOT EXISTS (SELECT 1 FROM storage WHERE at_hand = 1);
    INSERT INTO storage (name, at_hand)
        SELECT 'Под рукой', 1 WHERE NOT EXISTS (SELECT 1 FROM storage WHERE at_hand = 1);
    UPDATE storage SET is_default = 1
        WHERE id = (SELECT min(id) FROM storage WHERE at_hand = 1);

    CREATE TABLE paint_location (
        paint_id   INTEGER NOT NULL REFERENCES paint (id) ON DELETE CASCADE,
        storage_id INTEGER REFERENCES storage (id),  -- NULL = without a storage
        quantity   INTEGER NOT NULL CHECK (quantity > 0)
    );
    CREATE UNIQUE INDEX paint_location_uq ON paint_location (paint_id, ifnull(storage_id, 0));
    CREATE INDEX paint_location_storage_ix ON paint_location (storage_id);

    INSERT INTO paint_location (paint_id, storage_id, quantity)
        SELECT id, storage_id, quantity FROM paint WHERE quantity > 0;

    CREATE TABLE paint_new (
        id              INTEGER PRIMARY KEY,
        name            TEXT    NOT NULL,
        manufacturer_id INTEGER NOT NULL REFERENCES manufacturer (id) ON DELETE RESTRICT,
        code            TEXT,
        barcode         TEXT,
        hex             TEXT    CHECK (hex IS NULL OR hex GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    INSERT INTO paint_new (id, name, manufacturer_id, code, barcode, hex, created_at, updated_at)
        SELECT id, name, manufacturer_id, code, barcode, hex, created_at, updated_at FROM paint;
    DROP TABLE paint;
    ALTER TABLE paint_new RENAME TO paint;
    CREATE UNIQUE INDEX paint_identity_uq ON paint (manufacturer_id, name COLLATE NOCASE, ifnull(code, ''));
    CREATE INDEX paint_manufacturer_ix ON paint (manufacturer_id);
    CREATE UNIQUE INDEX paint_barcode_uq ON paint (barcode) WHERE barcode IS NOT NULL;
    """,
    # 5 — tag "Техническая": thinners, mediums, retarders and the like
    """
    INSERT INTO tag (name, color)
        SELECT 'Техническая', '#607d8b'
        WHERE NOT EXISTS (SELECT 1 FROM tag WHERE py_lower(name) = 'техническая');
    INSERT OR IGNORE INTO tag_keyword (keyword, tag_id)
        SELECT column1, (SELECT id FROM tag WHERE py_lower(name) = 'техническая')
        FROM (VALUES ('medium'), ('thinner'), ('thinners'), ('retarder'), ('flow'), ('improver'), ('diluent'));
    """,
    # 6 — more label words: cleaners and putty -> "Техническая"; dipping inks -> "Чернила".
    #     Only for tags that still have these names (a renamed tag is left alone).
    """
    INSERT OR IGNORE INTO tag_keyword (keyword, tag_id)
        SELECT v.column1, t.id
        FROM (VALUES ('cleaner'), ('cleaners'), ('putty'), ('filler'), ('restorer')) v
        JOIN tag t ON py_lower(t.name) = 'техническая';
    INSERT OR IGNORE INTO tag_keyword (keyword, tag_id)
        SELECT v.column1, t.id
        FROM (VALUES ('inks'), ('dipping'), ('dip'), ('quickshade')) v
        JOIN tag t ON py_lower(t.name) = 'чернила';
    """,
    # 7 — weathering pigments -> "Спецэффект"
    """
    INSERT OR IGNORE INTO tag_keyword (keyword, tag_id)
        SELECT v.column1, t.id
        FROM (VALUES ('pigment'), ('pigments')) v
        JOIN tag t ON py_lower(t.name) = 'спецэффект';
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
        if version > len(MIGRATIONS):
            raise RuntimeError(
                f"Database {db_path} is from a newer version of the app "
                f"(schema {version}, this app knows {len(MIGRATIONS)}). Update the app."
            )
        if version == len(MIGRATIONS):
            return
        # Rebuilding a table (DROP + RENAME) with foreign keys on would cascade-delete
        # rows that point to it, so they are off while migrating and checked afterwards.
        conn.execute("PRAGMA foreign_keys = OFF")
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            try:
                conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
            if number == 1:
                _seed_tags(conn)
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"Database migration left broken references: {[tuple(r) for r in problems]}")
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
