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
