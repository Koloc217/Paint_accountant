"""SQLite database: connection, the schema, starter data and migrations.

Uses only the standard-library sqlite3 module, so it works the same on
Linux, Windows and Raspberry Pi. The schema version is kept in
`PRAGMA user_version`. A new database is created at BASELINE_VERSION by
BASELINE (the schema as it was after the first 15 migrations, now folded into
one script) and the starter data; each entry in MIGRATIONS then moves it up by one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from .repository import register_functions

# A random UUID (version 4, lower case) in plain SQL, so a paint added by any
# means — the app, the sqlite3 console, a script — gets one from the column default.
UUID4_SQL = (
    "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || "
    "substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || "
    "substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))"
)

_HEX = "GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'"

# The schema a new database starts with. Databases made by the app before it had this
# script were brought here by migrations 1–15; those are gone, and such a database
# (user_version below BASELINE_VERSION) has to be upgraded by an older copy of the app first.
BASELINE_VERSION = 15
BASELINE = f"""
    CREATE TABLE manufacturer (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX manufacturer_name_uq ON manufacturer (name COLLATE NOCASE);

    -- a box, a drawer, a shelf. Its names per interface language are in storage_name.
    CREATE TABLE storage (
        id                INTEGER PRIMARY KEY,
        nfc_tag           TEXT,                       -- a note about its label, for the owner
        at_hand           INTEGER NOT NULL DEFAULT 0 CHECK (at_hand IN (0, 1)),
        created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        is_default        INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),  -- new pots go here
        -- where a second pot of a paint is suggested to go instead of this "at hand" storage
        copies_storage_id INTEGER REFERENCES storage (id) ON DELETE SET NULL
    );
    CREATE UNIQUE INDEX storage_default_uq ON storage (is_default) WHERE is_default = 1;

    CREATE TABLE storage_name (
        storage_id INTEGER NOT NULL REFERENCES storage (id) ON DELETE CASCADE,
        lang       TEXT    NOT NULL,
        name       TEXT    NOT NULL,
        PRIMARY KEY (storage_id, lang)
    );
    CREATE UNIQUE INDEX storage_name_lang_uq ON storage_name (lang, name COLLATE NOCASE);

    -- uuid: a permanent id for links from other apps (Project Hub and the like). Unlike `id`
    -- it never changes and does not repeat after a database is rebuilt or merged.
    CREATE TABLE paint (
        id              INTEGER PRIMARY KEY,
        uuid            TEXT    NOT NULL DEFAULT ({UUID4_SQL}) CHECK (length(uuid) = 36 AND uuid = lower(uuid)),
        name            TEXT    NOT NULL,
        manufacturer_id INTEGER NOT NULL REFERENCES manufacturer (id) ON DELETE RESTRICT,
        code            TEXT,
        barcode         TEXT,                         -- remembered when a paint is added by photo
        hex             TEXT    CHECK (hex IS NULL OR hex {_HEX}),
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX paint_uuid_uq ON paint (uuid);
    CREATE UNIQUE INDEX paint_identity_uq ON paint (manufacturer_id, name COLLATE NOCASE, ifnull(code, ''));
    CREATE INDEX paint_manufacturer_ix ON paint (manufacturer_id);
    CREATE UNIQUE INDEX paint_barcode_uq ON paint (barcode) WHERE barcode IS NOT NULL;

    -- links from other apps rely on it, so it can't be changed once given
    CREATE TRIGGER paint_uuid_fixed BEFORE UPDATE OF uuid ON paint
        WHEN NEW.uuid IS NOT OLD.uuid
        BEGIN SELECT RAISE(ABORT, 'paint uuid cannot be changed'); END;

    -- where the pots lie: a paint can be in several storages ("at hand 1, Запас 2")
    CREATE TABLE paint_location (
        paint_id   INTEGER NOT NULL REFERENCES paint (id) ON DELETE CASCADE,
        storage_id INTEGER REFERENCES storage (id),  -- NULL = without a storage
        quantity   INTEGER NOT NULL CHECK (quantity > 0)
    );
    CREATE UNIQUE INDEX paint_location_uq ON paint_location (paint_id, ifnull(storage_id, 0));
    CREATE INDEX paint_location_storage_ix ON paint_location (storage_id);

    -- a tag's names per interface language are in tag_name
    CREATE TABLE tag (
        id    INTEGER PRIMARY KEY,
        color TEXT NOT NULL DEFAULT '#888888' CHECK (color {_HEX})
    );

    CREATE TABLE tag_name (
        tag_id INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
        lang   TEXT    NOT NULL,
        name   TEXT    NOT NULL,
        PRIMARY KEY (tag_id, lang)
    );
    CREATE UNIQUE INDEX tag_name_lang_uq ON tag_name (lang, name COLLATE NOCASE);

    CREATE TABLE paint_tag (
        paint_id INTEGER NOT NULL REFERENCES paint (id) ON DELETE CASCADE,
        tag_id   INTEGER NOT NULL REFERENCES tag (id)   ON DELETE CASCADE,
        PRIMARY KEY (paint_id, tag_id)
    );
    CREATE INDEX paint_tag_tag_ix ON paint_tag (tag_id);

    -- words on a label that suggest a tag (used by photo recognition)
    CREATE TABLE tag_keyword (
        keyword TEXT    NOT NULL,
        tag_id  INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
        PRIMARY KEY (keyword, tag_id)
    );

    -- app-wide settings (sign-in with a password and the like)
    CREATE TABLE setting (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
"""

# ---- what a new database starts with (all of it can be renamed or deleted in the app)

STARTER_MANUFACTURERS = ["AK", "Army Painter", "Citadel", "Green Stuff World", "Kaleido", "Kimera",
                         "Mr. Hobby", "Tamiya", "Vallejo", "Scale75"]

# (Russian name, English name, plaque color, words on a label that suggest the tag)
STARTER_TAGS: list[tuple[str, str, str, list[str]]] = [
    ("Грунт", "Primer", "#9e9e9e", ["primer"]),
    ("Основа", "Base", "#5c6bc0", ["base"]),
    ("Слой", "Layer", "#42a5f5", ["layer"]),
    # Army Painter's Speedpaint and Vallejo's Xpress Color are the same kind of paint
    ("Контраст", "Contrast", "#ab47bc", ["contrast", "speedpaint", "xpress"]),
    # "accent": Tamiya "Panel Line Accent Color" is a wash
    ("Проливка", "Wash", "#8d6e63", ["shade", "wash", "accent"]),
    # also weathering pigments and texture pastes
    ("Спецэффект", "Special effect", "#26a69a", ["technical", "effect", "pigment", "pigments", "texture", "textures"]),
    ("Металлик", "Metallic", "#bdbdbd", ["metal", "metallic"]),
    ("Чернила", "Ink", "#3949ab", ["ink", "inks", "dipping", "dip", "quickshade"]),
    ("Лак", "Varnish", "#fdd835", ["varnish"]),
    ("Для аэрографа", "Airbrush", "#ef6c00", ["air"]),
    # thinned and cleaned with alcohol or a lacquer thinner, not with water
    ("Спиртовая", "Alcohol-based", "#7cb342", ["alcohol", "lacquer"]),
    # artist oils and oil washes
    ("Масляные", "Oil", "#ff8f00", ["oil", "oils", "oilbrusher"]),
    # thinners, mediums, retarders, cleaners, putty, paint revitalizers
    ("Техническая", "Technical", "#607d8b",
     ["medium", "thinner", "thinners", "retarder", "flow", "improver", "diluent", "cleaner", "cleaners",
      "putty", "filler", "restorer", "revitalizer", "revitalizador"]),
]

# the one storage of a new database: "at hand" and the default for new pots
STARTER_STORAGE = {"ru": "Под рукой", "en": "At hand"}

def _add_mr_hobby_and_the_alcohol_tag(conn: sqlite3.Connection) -> None:
    """Give a database made before these were starter data a chance to catch up.

    Both only if they are missing: a user who added Mr. Hobby himself, or who already
    sorted his alcohol paints under a tag of his own, keeps exactly what he has — and
    running this twice changes nothing, since the second run finds them there.
    """
    conn.execute("""INSERT INTO manufacturer (name) SELECT 'Mr. Hobby'
                    WHERE NOT EXISTS (SELECT 1 FROM manufacturer WHERE py_lower(name) = py_lower('Mr. Hobby'))""")

    ru, en, color, keywords = next(t for t in STARTER_TAGS if t[:2] == ("Спиртовая", "Alcohol-based"))
    taken = conn.execute(
        """SELECT EXISTS (SELECT 1 FROM tag_keyword WHERE keyword = ?)
                  OR EXISTS (SELECT 1 FROM tag_name WHERE lang = 'ru' AND py_lower(name) = py_lower(?))
                  OR EXISTS (SELECT 1 FROM tag_name WHERE lang = 'en' AND py_lower(name) = py_lower(?))""",
        (keywords[0], ru, en)).fetchone()[0]
    if taken:
        return
    tag_id = conn.execute("INSERT INTO tag (color) VALUES (?)", (color,)).lastrowid
    conn.executemany("INSERT INTO tag_name (tag_id, lang, name) VALUES (?, ?, ?)",
                     [(tag_id, "ru", ru), (tag_id, "en", en)])
    # a keyword already pointing at another tag stays with it
    conn.executemany("INSERT OR IGNORE INTO tag_keyword (keyword, tag_id) VALUES (?, ?)",
                     [(kw, tag_id) for kw in keywords])


def _add_oil_tag(conn: sqlite3.Connection) -> None:
    """Give a database made before the oil tag was starter data a chance to catch up.

    Only if missing: a user who already sorted oil paints under a tag of his own keeps
    what he has, and running this twice changes nothing.
    """
    ru, en, color, keywords = next(t for t in STARTER_TAGS if t[:2] == ("Масляные", "Oil"))
    taken = conn.execute(
        """SELECT EXISTS (SELECT 1 FROM tag_keyword WHERE keyword = ?)
                  OR EXISTS (SELECT 1 FROM tag_name WHERE lang = 'ru' AND py_lower(name) = py_lower(?))
                  OR EXISTS (SELECT 1 FROM tag_name WHERE lang = 'en' AND py_lower(name) = py_lower(?))""",
        (keywords[0], ru, en)).fetchone()[0]
    if taken:
        return
    tag_id = conn.execute("INSERT INTO tag (color) VALUES (?)", (color,)).lastrowid
    conn.executemany("INSERT INTO tag_name (tag_id, lang, name) VALUES (?, ?, ?)",
                     [(tag_id, "ru", ru), (tag_id, "en", en)])
    conn.executemany("INSERT OR IGNORE INTO tag_keyword (keyword, tag_id) VALUES (?, ?)",
                     [(kw, tag_id) for kw in keywords])


# Changes after the baseline, as SQL or as a function taking the open connection; each runs
# in its own transaction. Entry 0 takes the schema to BASELINE_VERSION + 1.
MIGRATIONS: list[str | Callable[[sqlite3.Connection], None]] = [
    # 16 — names saved with spaces around them (" Nuln Oil"), doubled spaces or invisible
    #      characters are tidied, as new ones are on saving. A name that would then repeat
    #      another one is left as it is (merge such paints by hand).
    """
    UPDATE OR IGNORE manufacturer SET name = py_clean(name)
        WHERE name != py_clean(name) AND py_clean(name) != ''
          AND NOT EXISTS (SELECT 1 FROM manufacturer x
                          WHERE x.id != manufacturer.id AND py_lower(x.name) = py_lower(py_clean(manufacturer.name)));
    UPDATE OR IGNORE paint SET code = nullif(py_clean(code), '')
        WHERE code IS NOT NULL AND code != py_clean(code)
          AND NOT EXISTS (SELECT 1 FROM paint x
                          WHERE x.id != paint.id AND x.manufacturer_id = paint.manufacturer_id
                            AND py_lower(x.name) = py_lower(paint.name)
                            AND py_lower(ifnull(x.code, '')) = py_lower(py_clean(paint.code)));
    UPDATE OR IGNORE paint SET name = py_clean(name)
        WHERE name != py_clean(name) AND py_clean(name) != ''
          AND NOT EXISTS (SELECT 1 FROM paint x
                          WHERE x.id != paint.id AND x.manufacturer_id = paint.manufacturer_id
                            AND py_lower(x.name) = py_lower(py_clean(paint.name))
                            AND py_lower(ifnull(x.code, '')) = py_lower(ifnull(paint.code, '')));
    UPDATE OR IGNORE paint SET barcode = nullif(py_clean(barcode), '')
        WHERE barcode IS NOT NULL AND barcode != py_clean(barcode);
    UPDATE OR IGNORE tag_name SET name = py_clean(name)
        WHERE name != py_clean(name) AND py_clean(name) != ''
          AND NOT EXISTS (SELECT 1 FROM tag_name x WHERE x.tag_id != tag_name.tag_id AND x.lang = tag_name.lang
                            AND py_lower(x.name) = py_lower(py_clean(tag_name.name)));
    UPDATE OR IGNORE storage_name SET name = py_clean(name)
        WHERE name != py_clean(name) AND py_clean(name) != ''
          AND NOT EXISTS (SELECT 1 FROM storage_name x WHERE x.storage_id != storage_name.storage_id
                            AND x.lang = storage_name.lang
                            AND py_lower(x.name) = py_lower(py_clean(storage_name.name)));
    UPDATE storage SET nfc_tag = nullif(py_clean(nfc_tag), '')
        WHERE nfc_tag IS NOT NULL AND nfc_tag != py_clean(nfc_tag);
    """,
    # 17 — Mr. Hobby joins the manufacturers a new database starts with, and paints thinned
    #      with alcohol or a lacquer thinner get a tag of their own. Both only if missing:
    #      a database where the user already added them keeps what he has.
    """
    INSERT INTO manufacturer (name) SELECT 'Mr. Hobby'
        WHERE NOT EXISTS (SELECT 1 FROM manufacturer WHERE py_lower(name) = py_lower('Mr. Hobby'));
    """,
    # 17 — Mr. Hobby among the manufacturers a new database starts with, and a tag for the
    #      paints thinned with alcohol or a lacquer thinner.
    _add_mr_hobby_and_the_alcohol_tag,
    # 18 — Vallejo's Xpress Color is a contrast paint, so the word now suggests that tag.
    #      It follows whichever tag "contrast" belongs to, which is the user's to move.
    """
    INSERT OR IGNORE INTO tag_keyword (keyword, tag_id)
        SELECT 'xpress', tag_id FROM tag_keyword WHERE keyword = 'contrast';
    """,
    # 19 — Army Painter among the manufacturers a new database starts with. Not if one is
    #      there already, under either spelling of the name.
    """
    INSERT INTO manufacturer (name) SELECT 'Army Painter'
        WHERE NOT EXISTS (SELECT 1 FROM manufacturer
                          WHERE py_lower(name) IN (py_lower('Army Painter'), py_lower('The Army Painter')));
    """,
    # 20 — a tag for oil paints and oil washes
    _add_oil_tag,
]

SCHEMA_VERSION = BASELINE_VERSION + len(MIGRATIONS)

# the last version of the app that could upgrade a database older than the baseline
LAST_WITH_OLD_MIGRATIONS = "0ee8e28"

# ALTER TABLE … DROP COLUMN, kept as the floor the app has been tested on (SQLite 3.35, 2021)
MIN_SQLITE = (3, 35, 0)


class OldSQLite(RuntimeError):
    """The SQLite library this Python uses is too old for the app's database."""


class OldDatabase(RuntimeError):
    """The database was made by a version of the app from before the baseline schema."""


def too_old(version: int) -> str:
    return (f"it was made by an old version of the app (schema {version}; this app upgrades from "
            f"{BASELINE_VERSION}). Start the app once as it was at commit {LAST_WITH_OLD_MIGRATIONS} "
            f"(git checkout {LAST_WITH_OLD_MIGRATIONS}), then update again.")


def check_sqlite() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        need = ".".join(map(str, MIN_SQLITE[:2]))
        raise OldSQLite(
            f"This Python comes with SQLite {sqlite3.sqlite_version}, the app needs {need} or newer. "
            "Use a newer Python (python.org builds for Windows carry their own SQLite) or update the system."
        )


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    register_functions(conn)
    return conn


def init_db(db_path: Path) -> None:
    """Create the data folder and database if needed, apply pending migrations."""
    check_sqlite()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database {db_path} is from a newer version of the app "
                f"(schema {version}, this app knows {SCHEMA_VERSION}). Update the app."
            )
        if version == SCHEMA_VERSION:
            return
        if version == 0:
            if conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                raise RuntimeError(f"{db_path} is not a Paint Accountant database")
            _create(conn)
            version = BASELINE_VERSION
        elif version < BASELINE_VERSION:
            raise OldDatabase(f"Database {db_path} can't be opened: {too_old(version)}")
        else:
            backup_before_migration(conn, db_path, version)
        # A migration that rebuilds a table (DROP + RENAME) with foreign keys on would
        # cascade-delete rows that point to it, so they are off while migrating and checked afterwards.
        conn.execute("PRAGMA foreign_keys = OFF")
        for number, script in enumerate(MIGRATIONS[version - BASELINE_VERSION:], start=version + 1):
            try:
                if callable(script):
                    conn.execute("BEGIN")
                    script(conn)
                    conn.execute(f"PRAGMA user_version = {number}")  # not a value: no placeholder
                    conn.commit()
                else:
                    conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"Database migration left broken references: {[tuple(r) for r in problems]}")
    finally:
        conn.close()


def _create(conn: sqlite3.Connection) -> None:
    """A new database: the baseline schema and the starter data, in one transaction."""
    try:
        conn.executescript(f"BEGIN; {BASELINE}")
        conn.executemany("INSERT INTO manufacturer (name) VALUES (?)", [(name,) for name in STARTER_MANUFACTURERS])
        for ru, en, color, keywords in STARTER_TAGS:
            tag_id = conn.execute("INSERT INTO tag (color) VALUES (?)", (color,)).lastrowid
            conn.executemany("INSERT INTO tag_name (tag_id, lang, name) VALUES (?, ?, ?)",
                             [(tag_id, "ru", ru), (tag_id, "en", en)])
            conn.executemany("INSERT INTO tag_keyword (keyword, tag_id) VALUES (?, ?)",
                             [(kw, tag_id) for kw in keywords])
        storage_id = conn.execute("INSERT INTO storage (at_hand, is_default) VALUES (1, 1)").lastrowid
        conn.executemany("INSERT INTO storage_name (storage_id, lang, name) VALUES (?, ?, ?)",
                         [(storage_id, lang, name) for lang, name in STARTER_STORAGE.items()])
        conn.execute(f"PRAGMA user_version = {BASELINE_VERSION}")
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def backup_before_migration(conn: sqlite3.Connection, db_path: Path, version: int) -> Path:
    """Copy the database next to it before the schema changes: paints.sqlite3 ->
    paints.before-v15.sqlite3. An existing copy for the same version is kept as is."""
    target = db_path.with_name(f"{db_path.stem}.before-v{version}{db_path.suffix}")
    if not target.exists():
        dest = sqlite3.connect(target)
        try:
            conn.backup(dest)  # consistent copy even in WAL mode
        finally:
            dest.close()
    return target
