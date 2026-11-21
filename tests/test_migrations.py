"""The database: a new one, one from the version before, and ones this app can't open."""

from __future__ import annotations

import sqlite3

import pytest

from paint_accountant import db
from paint_accountant.__main__ import main

_UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def _baseline(path) -> sqlite3.Connection:
    """A database as the app made it before the migrations that follow the baseline."""
    conn = db.connect(path)
    db._create(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.BASELINE_VERSION
    return conn


def test_versions_add_up():
    assert db.BASELINE_VERSION == 15  # the number is in users' files: it can't be moved
    assert db.SCHEMA_VERSION == db.BASELINE_VERSION + len(db.MIGRATIONS)


def test_fresh_database_starts_with_the_starter_data(tmp_path):
    path = tmp_path / "paints.sqlite3"
    db.init_db(path)
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert [r[0] for r in conn.execute("SELECT name FROM manufacturer ORDER BY id")] == db.STARTER_MANUFACTURERS
    tags = conn.execute("""SELECT t.id, ru.name, en.name, t.color FROM tag t
                           JOIN tag_name ru ON ru.tag_id = t.id AND ru.lang = 'ru'
                           JOIN tag_name en ON en.tag_id = t.id AND en.lang = 'en' ORDER BY t.id""").fetchall()
    assert [tuple(r) for r in tags] == [(i, ru, en, color) for i, (ru, en, color, _) in enumerate(db.STARTER_TAGS, 1)]
    assert tuple(tags[0]) == (1, "Грунт", "Primer", "#9e9e9e")
    assert [t[1] for t in tags[-3:]] == ["Спиртовая", "Масляные", "Техническая"]
    words = {(r[0], r[1]) for r in conn.execute("SELECT keyword, tag_id FROM tag_keyword")}
    assert {("primer", 1), ("xpress", 4), ("accent", 5), ("pigment", 6), ("quickshade", 8),
            ("alcohol", 11), ("oil", 12), ("revitalizer", 13)} <= words
    assert len(words) == sum(len(kws) for *_, kws in db.STARTER_TAGS) == 43
    assert [tuple(r) for r in conn.execute("SELECT lang, name FROM storage_name ORDER BY lang")] == \
        [("en", "At hand"), ("ru", "Под рукой")]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT count(*) FROM paint").fetchone()[0] == 0
    conn.close()


def test_database_of_the_previous_version_is_upgraded_and_copied_first(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = _baseline(path)
    with conn:
        conn.execute("INSERT INTO paint (id, name, manufacturer_id, code) VALUES (1, ' Nuln  Oil', 2, '24-14')")
        conn.execute("INSERT INTO paint_location (paint_id, storage_id, quantity) VALUES (1, 1, 2)")
        conn.execute("INSERT INTO paint_tag (paint_id, tag_id) VALUES (1, 5)")
    uuid = conn.execute("SELECT uuid FROM paint").fetchone()[0]
    conn.close()

    db.init_db(path)
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert tuple(conn.execute("SELECT name, code, uuid FROM paint").fetchone()) == ("Nuln Oil", "24-14", uuid)
    assert conn.execute("SELECT quantity FROM paint_location WHERE paint_id = 1").fetchone()[0] == 2
    assert conn.execute("SELECT tag_id FROM paint_tag WHERE paint_id = 1").fetchone()[0] == 5
    conn.close()
    # the file as it was is kept next to it
    backup = tmp_path / f"paints.before-v{db.BASELINE_VERSION}.sqlite3"
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == db.BASELINE_VERSION
    assert old.execute("SELECT name FROM paint").fetchone()[0] == " Nuln  Oil"
    old.close()
    db.init_db(path)  # nothing more to do the second time
    assert len(list(tmp_path.glob("*.before-*"))) == 1


@pytest.mark.parametrize("version", [1, 4, 10, 14])
def test_database_from_before_the_baseline_is_refused_untouched(tmp_path, version):
    path = tmp_path / "paints.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(f"CREATE TABLE paint (id INTEGER PRIMARY KEY, name TEXT); INSERT INTO paint VALUES (1, 'Old');"
                       f"PRAGMA user_version = {version};")
    conn.close()
    before = path.read_bytes()
    with pytest.raises(db.OldDatabase, match=rf"schema {version}; this app upgrades from 15.*{db.LAST_WITH_OLD_MIGRATIONS}"):
        db.init_db(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == version
    assert conn.execute("SELECT name FROM paint").fetchall() == [("Old",)]
    conn.close()
    assert not list(tmp_path.glob("*.before-*"))
    # from the command line: one plain line, no traceback
    with pytest.raises(SystemExit) as exit_:
        main(["--data-dir", str(tmp_path), "--config", str(tmp_path / "none.ini")])
    assert "Can't start" in str(exit_.value) and "old version of the app" in str(exit_.value)
    assert before[:16] == path.read_bytes()[:16]


def test_some_other_sqlite_file_is_refused(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY)")  # user_version 0, but not empty
    conn.close()
    with pytest.raises(RuntimeError, match="not a Paint Accountant database"):
        db.init_db(path)
    conn = sqlite3.connect(path)
    assert [r[0] for r in conn.execute("SELECT name FROM sqlite_master")] == ["notes"]
    conn.close()


def test_failed_creation_leaves_no_half_made_database(tmp_path, monkeypatch):
    path = tmp_path / "paints.sqlite3"
    monkeypatch.setattr(db, "STARTER_MANUFACTURERS", ["AK", "ak"])  # breaks the unique index midway
    with pytest.raises(sqlite3.IntegrityError):
        db.init_db(path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer version"):
        db.init_db(path)


def test_fresh_database_has_default_at_hand_storage(tmp_path):
    path = tmp_path / "paints.sqlite3"
    db.init_db(path)
    conn = db.connect(path)
    assert [tuple(r) for r in conn.execute("SELECT (SELECT name FROM storage_name WHERE storage_id = storage.id AND lang = 'ru'), at_hand, is_default FROM storage")] == [("Под рукой", 1, 1)]
    conn.close()


def test_technical_tag_added_once_with_keywords(tmp_path):
    path = tmp_path / "paints.sqlite3"
    db.init_db(path)
    db.init_db(path)
    conn = db.connect(path)
    rows = conn.execute("SELECT tag_id FROM tag_name WHERE lang = 'ru' AND name = 'Техническая'").fetchall()
    assert len(rows) == 1
    kws = {r[0] for r in conn.execute("SELECT keyword FROM tag_keyword WHERE tag_id = ?", (rows[0][0],))}
    assert {"medium", "thinner", "retarder"} <= kws
    conn.close()


def test_uuid_is_given_on_insert_and_cannot_change(tmp_path):
    import re

    path = tmp_path / "paints.sqlite3"
    db.init_db(path)
    assert not list(tmp_path.glob("*.before-*"))  # fresh database: nothing to back up
    conn = db.connect(path)
    with conn:
        maker = conn.execute("SELECT id FROM manufacturer LIMIT 1").fetchone()[0]
        conn.executemany("INSERT INTO paint (name, manufacturer_id) VALUES (?, ?)",
                         [(f"Paint {i}", maker) for i in range(200)])
    uuids = [r[0] for r in conn.execute("SELECT uuid FROM paint ORDER BY id")]
    assert len(set(uuids)) == 200 and all(re.match(_UUID_RE, u) for u in uuids)

    with pytest.raises(sqlite3.IntegrityError, match="cannot be changed"):
        with conn:
            conn.execute("UPDATE paint SET uuid = '00000000-0000-4000-8000-000000000000' WHERE id = 1")
    with conn:  # other columns still change freely
        conn.execute("UPDATE paint SET name = 'Renamed', uuid = uuid WHERE id = 1")
    assert conn.execute("SELECT uuid FROM paint WHERE id = 1").fetchone()[0] == uuids[0]

    # an import may bring its own uuid, but not one that is taken
    with conn:
        conn.execute("INSERT INTO paint (name, manufacturer_id, uuid) VALUES ('Imported', ?, ?)",
                     (maker, "11111111-2222-4333-8444-555555555555"))
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("INSERT INTO paint (name, manufacturer_id, uuid) VALUES ('Again', ?, ?)",
                         (maker, uuids[1]))
    conn.close()


def test_migration_16_tidies_saved_names(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = _baseline(path)
    with conn:
        conn.execute("INSERT INTO manufacturer (id, name) VALUES (50, ' Two  Thin Coats ')")
        conn.executemany("INSERT INTO paint (id, name, manufacturer_id, code, barcode) VALUES (?, ?, 50, ?, ?)", [
            (1, "  Nuln   Oil ", " 24-14 ", " 111 "),
            (2, "Black", None, None),
            (3, " black", None, None),          # tidied, it would repeat paint 2: left alone
            (4, "\u200bЗелёный ", "  ", None),   # an invisible character; a blank code becomes none
        ])
        conn.execute("INSERT INTO storage (id, nfc_tag, at_hand) VALUES (9, ' 04:A2 ', 0)")
        conn.executemany("INSERT INTO storage_name (storage_id, lang, name) VALUES (9, ?, ?)",
                         [("ru", "  Коробка  9"), ("en", " Box 9 ")])
        conn.execute("INSERT INTO tag (id, color) VALUES (90, '#112233')")
        conn.executemany("INSERT INTO tag_name (tag_id, lang, name) VALUES (90, ?, ?)",
                         [("ru", " Глейз "), ("en", "Glaze")])
    conn.close()

    db.init_db(path)
    conn = db.connect(path)
    assert conn.execute("SELECT name FROM manufacturer WHERE id = 50").fetchone()[0] == "Two Thin Coats"
    rows = {r["id"]: (r["name"], r["code"], r["barcode"]) for r in conn.execute("SELECT * FROM paint")}
    assert rows == {1: ("Nuln Oil", "24-14", "111"), 2: ("Black", None, None),
                    3: (" black", None, None), 4: ("Зелёный", None, None)}
    assert [r[0] for r in conn.execute("SELECT name FROM storage_name WHERE storage_id = 9 ORDER BY lang")] == \
        ["Box 9", "Коробка 9"]
    assert conn.execute("SELECT name FROM tag_name WHERE tag_id = 90 AND lang = 'ru'").fetchone()[0] == "Глейз"
    assert conn.execute("SELECT nfc_tag FROM storage WHERE id = 9").fetchone()[0] == "04:A2"
    conn.close()


def test_old_sqlite_is_explained(monkeypatch, tmp_path):
    monkeypatch.setattr(db.sqlite3, "sqlite_version_info", (3, 31, 1))
    monkeypatch.setattr(db.sqlite3, "sqlite_version", "3.31.1")
    with pytest.raises(db.OldSQLite, match="SQLite 3.31.1, the app needs 3.35"):
        db.init_db(tmp_path / "paints.sqlite3")


def _as_before(conn: sqlite3.Connection) -> None:
    """A database as it was before Mr. Hobby and the alcohol tag were starter data."""
    with conn:
        conn.execute("DELETE FROM manufacturer WHERE name = 'Mr. Hobby'")
        conn.execute("DELETE FROM tag WHERE id IN (SELECT tag_id FROM tag_keyword WHERE keyword = 'alcohol')")


def _alcohol(conn: sqlite3.Connection) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        """SELECT ru.name, en.name, t.color, (SELECT group_concat(keyword, ',') FROM tag_keyword k
                                              WHERE k.tag_id = t.id ORDER BY keyword)
           FROM tag t JOIN tag_name ru ON ru.tag_id = t.id AND ru.lang = 'ru'
                      JOIN tag_name en ON en.tag_id = t.id AND en.lang = 'en'
           WHERE ru.name = 'Спиртовая' OR en.name = 'Alcohol-based'""")]


def test_mr_hobby_and_the_alcohol_tag_reach_an_older_database(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = _baseline(path)
    _as_before(conn)
    with conn:  # a paint of the user's own, to be left alone
        conn.execute("INSERT INTO paint (id, name, manufacturer_id) VALUES (1, 'Nuln Oil', 2)")
    conn.close()

    for _ in range(2):  # the second run must change nothing
        db.init_db(path)
        conn = db.connect(path)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert [r[0] for r in conn.execute("SELECT name FROM manufacturer WHERE name = 'Mr. Hobby'")] == ["Mr. Hobby"]
        assert _alcohol(conn) == [("Спиртовая", "Alcohol-based", "#7cb342", "alcohol,lacquer")]
        assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == len(db.STARTER_TAGS)
        assert conn.execute("SELECT count(*) FROM paint").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.close()


@pytest.mark.parametrize("already, expected", [
    (None, ["Army Painter"]),                      # not there: added
    ("The Army Painter", ["The Army Painter"]),    # the user's own spelling is kept, alone
    ("army painter", ["army painter"]),
])
def test_army_painter_is_added_unless_one_is_there(tmp_path, already, expected):
    path = tmp_path / "paints.sqlite3"
    conn = _baseline(path)
    with conn:
        conn.execute("DELETE FROM manufacturer WHERE py_lower(name) = py_lower('Army Painter')")
        if already:
            conn.execute("INSERT INTO manufacturer (name) VALUES (?)", (already,))
    conn.close()

    for _ in range(2):  # and the second run adds no second one
        db.init_db(path)
        conn = db.connect(path)
        assert [r[0] for r in conn.execute(
            "SELECT name FROM manufacturer WHERE py_lower(name) LIKE '%army painter%'")] == expected
        conn.close()


def test_xpress_joins_whichever_tag_contrast_belongs_to(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = _baseline(path)
    with conn:  # as it was before, with the keyword on a tag the user has renamed
        conn.execute("DELETE FROM tag_keyword WHERE keyword = 'xpress'")
        conn.execute("UPDATE tag_name SET name = 'Контрастные' WHERE lang = 'ru' AND name = 'Контраст'")
    mine = conn.execute("SELECT tag_id FROM tag_keyword WHERE keyword = 'contrast'").fetchone()[0]
    conn.close()

    db.init_db(path)
    conn = db.connect(path)
    assert [r[0] for r in conn.execute("SELECT tag_id FROM tag_keyword WHERE keyword = 'xpress'")] == [mine]
    assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == len(db.STARTER_TAGS)
    conn.close()


def test_a_tag_of_your_own_by_that_name_is_left_alone(tmp_path):
    """Someone who already sorted his alcohol paints keeps his tag: no second one appears."""
    path = tmp_path / "paints.sqlite3"
    conn = _baseline(path)
    _as_before(conn)
    with conn:
        mine = conn.execute("INSERT INTO tag (color) VALUES ('#111111')").lastrowid
        conn.executemany("INSERT INTO tag_name (tag_id, lang, name) VALUES (?, ?, ?)",
                         [(mine, "ru", "Спиртовая"), (mine, "en", "Alcohol")])
    conn.close()

    db.init_db(path)
    conn = db.connect(path)
    assert _alcohol(conn) == [("Спиртовая", "Alcohol", "#111111", None)]  # his colour, his names, no keywords
    assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == len(db.STARTER_TAGS)
    conn.close()


def _as_before_oil(conn: sqlite3.Connection) -> None:
    """A database as it was before the oil tag was starter data."""
    with conn:
        conn.execute("DELETE FROM tag WHERE id IN (SELECT tag_id FROM tag_name WHERE name = 'Масляные' AND lang = 'ru')")


def _oil(conn: sqlite3.Connection) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        """SELECT ru.name, en.name, t.color, (SELECT group_concat(keyword, ',') FROM tag_keyword k
                                              WHERE k.tag_id = t.id ORDER BY keyword)
           FROM tag t JOIN tag_name ru ON ru.tag_id = t.id AND ru.lang = 'ru'
                      JOIN tag_name en ON en.tag_id = t.id AND en.lang = 'en'
           WHERE ru.name = 'Масляные' OR en.name = 'Oil'""")]


def test_oil_tag_reaches_an_older_database(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = _baseline(path)
    _as_before_oil(conn)
    with conn:  # a paint of the user's own, to be left alone
        conn.execute("INSERT INTO paint (id, name, manufacturer_id) VALUES (1, 'Abteilung Sepia', 2)")
    conn.close()

    for _ in range(2):  # the second run must change nothing
        db.init_db(path)
        conn = db.connect(path)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert _oil(conn) == [("Масляные", "Oil", "#ff8f00", "oil,oils,oilbrusher")]
        assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == len(db.STARTER_TAGS)
        assert conn.execute("SELECT count(*) FROM paint").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.close()


def test_a_tag_of_your_own_for_oil_is_left_alone(tmp_path):
    """Someone who already sorted his oil paints keeps his tag: no second one appears."""
    path = tmp_path / "paints.sqlite3"
    conn = _baseline(path)
    _as_before_oil(conn)
    with conn:
        mine = conn.execute("INSERT INTO tag (color) VALUES ('#222222')").lastrowid
        conn.executemany("INSERT INTO tag_name (tag_id, lang, name) VALUES (?, ?, ?)",
                         [(mine, "ru", "Масляные"), (mine, "en", "Oils")])
    conn.close()

    db.init_db(path)
    conn = db.connect(path)
    assert _oil(conn) == [("Масляные", "Oils", "#222222", None)]  # his colour, his names, no keywords
    assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == len(db.STARTER_TAGS)
    conn.close()

