"""Upgrading a database made by an older version keeps the user's data."""

from __future__ import annotations

import sqlite3

import pytest

from paint_accountant import db


def _make_v1(path):
    conn = db.connect(path)
    conn.executescript(f"BEGIN; {db.MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")
    db._seed_tags(conn)
    with conn:
        conn.execute("INSERT INTO storage (id, name) VALUES (1, 'Коробка 1')")
        conn.executemany(
            "INSERT INTO paint (id, name, manufacturer, code, hex, quantity, storage_id) VALUES (?,?,?,?,?,?,?)",
            [
                (1, "Nuln Oil", "Citadel", "24-14", "#1d1d1d", 2, 1),
                (2, "Mephiston Red", "citadel ", None, None, 1, None),  # other spelling of an existing one
                (3, "Изумрудный", "Кимера", None, None, 0, 1),            # name not in the starter list
                (4, "Black", "кимера", None, None, 1, None),
            ],
        )
        conn.execute("INSERT INTO paint_tag (paint_id, tag_id) VALUES (1, 5)")
    conn.close()


def test_v1_to_v2_keeps_paints_tags_and_storages(tmp_path):
    path = tmp_path / "paints.sqlite3"
    _make_v1(path)

    db.init_db(path)

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    rows = conn.execute(
        "SELECT p.id, p.name, m.name AS maker, coalesce(sum(l.quantity), 0), max(l.storage_id) FROM paint p "
        "JOIN manufacturer m ON m.id = p.manufacturer_id LEFT JOIN paint_location l ON l.paint_id = p.id "
        "GROUP BY p.id ORDER BY p.id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        (1, "Nuln Oil", "Citadel", 2, 1),
        (2, "Mephiston Red", "Citadel", 1, None),
        (3, "Изумрудный", "Кимера", 0, None),  # used up: no pots, so no place either
        (4, "Black", "Кимера", 1, None),
    ]
    # tag link survived the table rebuild
    assert conn.execute("SELECT count(*) FROM paint_tag").fetchone()[0] == 1
    makers = [r[0] for r in conn.execute("SELECT name FROM manufacturer ORDER BY id")]
    assert makers[:7] == ["AK", "Citadel", "Green Stuff World", "Kaleido", "Kimera", "Tamiya", "Vallejo"]
    assert makers.count("Кимера") == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()

    db.init_db(path)  # second start: nothing changes, nothing duplicated
    conn = db.connect(path)
    assert conn.execute("SELECT count(*) FROM manufacturer").fetchone()[0] == len(makers)
    conn.close()


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer version"):
        db.init_db(path)


def test_v3_to_v4_moves_pots_into_locations_and_picks_default(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = db.connect(path)
    for n, script in enumerate(db.MIGRATIONS[:3], start=1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {n}; COMMIT;")
    with conn:
        conn.execute("INSERT INTO storage (id, name) VALUES (1, 'Коробка 1')")
        conn.execute("INSERT INTO storage (id, name, at_hand) VALUES (2, 'Ящик у стола', 1)")
        citadel = conn.execute("SELECT id FROM manufacturer WHERE name = 'Citadel'").fetchone()[0]
        conn.executemany(
            "INSERT INTO paint (id, name, manufacturer_id, quantity, storage_id, barcode) VALUES (?,?,?,?,?,?)",
            [(1, "Nuln Oil", citadel, 3, 1, "501"), (2, "Leadbelcher", citadel, 1, 2, None),
             (3, "Abaddon Black", citadel, 0, 1, None), (4, "Retributor", citadel, 2, None, None)],
        )
    conn.close()

    db.init_db(path)

    conn = db.connect(path)
    locs = {tuple(r) for r in conn.execute("SELECT paint_id, storage_id, quantity FROM paint_location")}
    assert locs == {(1, 1, 3), (2, 2, 1), (4, None, 2)}  # used-up paint has no pots anywhere
    # the existing "at hand" storage became the default, no extra one was created
    storages = [tuple(r) for r in conn.execute("SELECT id, name, at_hand, is_default FROM storage ORDER BY id")]
    assert storages == [(1, "Коробка 1", 0, 0), (2, "Ящик у стола", 1, 1)]
    assert conn.execute("SELECT barcode FROM paint WHERE id = 1").fetchone()[0] == "501"
    conn.close()


def test_fresh_database_has_default_at_hand_storage(tmp_path):
    path = tmp_path / "paints.sqlite3"
    db.init_db(path)
    conn = db.connect(path)
    assert [tuple(r) for r in conn.execute("SELECT name, at_hand, is_default FROM storage")] == [("Под рукой", 1, 1)]
    conn.close()


def test_technical_tag_added_once_with_keywords(tmp_path):
    path = tmp_path / "paints.sqlite3"
    db.init_db(path)
    db.init_db(path)
    conn = db.connect(path)
    rows = conn.execute("SELECT id FROM tag WHERE name = 'Техническая'").fetchall()
    assert len(rows) == 1
    kws = {r[0] for r in conn.execute("SELECT keyword FROM tag_keyword WHERE tag_id = ?", (rows[0][0],))}
    assert {"medium", "thinner", "retarder"} <= kws
    conn.close()


def test_technical_tag_not_duplicated_if_user_made_one(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = db.connect(path)
    for n, script in enumerate(db.MIGRATIONS[:4], start=1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {n}; COMMIT;")
        if n == 1:
            db._seed_tags(conn)
    with conn:
        conn.execute("INSERT INTO tag (name, color) VALUES ('техническая', '#123456')")
    conn.close()
    db.init_db(path)
    conn = db.connect(path)
    assert conn.execute("SELECT count(*) FROM tag WHERE lower(name) LIKE '%ическая'").fetchone()[0] == 1
    conn.close()


_UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def _make_v10_with_paints(path):
    conn = db.connect(path)
    for n, script in enumerate(db.MIGRATIONS[:10], start=1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {n}; COMMIT;")
        if n == 1:
            db._seed_tags(conn)
    with conn:
        citadel = conn.execute("SELECT id FROM manufacturer WHERE name = 'Citadel'").fetchone()[0]
        box = conn.execute("INSERT INTO storage (name) VALUES ('Коробка 1')").lastrowid
        conn.executemany(
            "INSERT INTO paint (id, name, manufacturer_id, code, barcode, hex) VALUES (?,?,?,?,?,?)",
            [(1, "Nuln Oil", citadel, "24-14", "501", "#1d1d1d"), (2, "Leadbelcher", citadel, None, None, None),
             (7, "Retributor", citadel, None, None, None)],  # ids with a gap stay as they are
        )
        conn.executemany("INSERT INTO paint_location (paint_id, storage_id, quantity) VALUES (?,?,?)",
                         [(1, box, 2), (7, None, 1)])
        conn.execute("INSERT INTO paint_tag (paint_id, tag_id) VALUES (1, 5)")
    conn.close()


def test_v10_to_v11_gives_every_paint_a_uuid_and_keeps_the_rest(tmp_path):
    import re

    path = tmp_path / "paints.sqlite3"
    _make_v10_with_paints(path)

    db.init_db(path)

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    rows = conn.execute("SELECT id, uuid, name, code, barcode, hex FROM paint ORDER BY id").fetchall()
    assert [(r["id"], r["name"], r["code"], r["barcode"], r["hex"]) for r in rows] == [
        (1, "Nuln Oil", "24-14", "501", "#1d1d1d"), (2, "Leadbelcher", None, None, None),
        (7, "Retributor", None, None, None),
    ]
    uuids = [r["uuid"] for r in rows]
    assert all(re.match(_UUID_RE, u) for u in uuids), uuids
    assert len(set(uuids)) == 3
    # pots and tags still point to the right paints
    assert {tuple(r) for r in conn.execute("SELECT paint_id, quantity FROM paint_location")} == {(1, 2), (7, 1)}
    assert conn.execute("SELECT paint_id FROM paint_tag").fetchall()[0][0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

    # a copy of the old database was left next to it, untouched
    backup = tmp_path / "paints.before-v10.sqlite3"
    assert backup.exists()
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 10
    assert old.execute("SELECT count(*) FROM paint").fetchone()[0] == 3
    old.close()

    db.init_db(path)  # second start: uuids don't change
    conn = db.connect(path)
    assert [r[0] for r in conn.execute("SELECT uuid FROM paint ORDER BY id")] == uuids
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
