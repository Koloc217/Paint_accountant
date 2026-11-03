"""Tags and storages: one id with a name in every interface language."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant import db
from paint_accountant.app import create_app
from paint_accountant.config import load_settings


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(load_settings(data_dir=tmp_path))) as c:
        yield c


def tag(client, ru_name):
    return next(t for t in client.get("/api/tags").json() if t["names"]["ru"] == ru_name)


def test_starter_tags_have_english_names(client):
    assert tag(client, "Проливка")["names"] == {"ru": "Проливка", "en": "Wash"}
    assert tag(client, "Техническая")["names"]["en"] == "Technical"
    assert tag(client, "Грунт")["name"] == "Грунт"  # "name" is the Russian one


def test_new_tag_gets_the_same_name_in_every_language(client):
    t = client.post("/api/tags", json={"name": "Мой тег"}).json()
    assert t["names"] == {"ru": "Мой тег", "en": "Мой тег"}
    t = client.post("/api/tags", json={"name": "Глянец", "names": {"en": "Gloss"}}).json()
    assert t["names"] == {"ru": "Глянец", "en": "Gloss"}


def test_rename_in_one_language_keeps_the_other(client):
    t = client.post("/api/tags", json={"name": "Мой тег"}).json()
    r = client.patch(f"/api/tags/{t['id']}", json={"name": "My tag", "lang": "en"})
    assert r.status_code == 200 and r.json()["names"] == {"ru": "Мой тег", "en": "My tag"}
    r = client.patch(f"/api/tags/{t['id']}", json={"name": "Общий"})  # no language: everywhere
    assert r.json()["names"] == {"ru": "Общий", "en": "Общий"}


def test_names_are_unique_per_language(client):
    t = client.post("/api/tags", json={"name": "Мой тег"}).json()
    assert client.patch(f"/api/tags/{t['id']}", json={"name": "Wash", "lang": "en"}).status_code == 409
    assert client.patch(f"/api/tags/{t['id']}", json={"name": "Wash", "lang": "ru"}).status_code == 200
    assert client.post("/api/tags", json={"name": "проливка"}).status_code == 409


def test_paint_carries_tag_names_in_every_language(client):
    wash = tag(client, "Проливка")
    p = client.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "tag_ids": [wash["id"]]}).json()
    assert p["tags"][0]["names"] == {"ru": "Проливка", "en": "Wash"}


def test_migration_keeps_ids_and_does_not_steal_a_users_name(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = db.connect(path)
    for n, script in enumerate(db.MIGRATIONS[:13], start=1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {n}; COMMIT;")
        if n == 1:
            db._seed_tags(conn)
    with conn:
        conn.execute("INSERT INTO tag (name, color) VALUES ('Primer', '#123456')")  # the user's own
        before = {r[1]: r[0] for r in conn.execute("SELECT id, name FROM tag")}
        conn.execute("INSERT INTO paint (name, manufacturer_id) VALUES ('X', 1)")
        conn.execute("INSERT INTO paint_tag (paint_id, tag_id) VALUES (1, ?)", (before["Проливка"],))
    conn.close()

    db.init_db(path)

    conn = db.connect(path)
    names = {}
    for r in conn.execute("SELECT tag_id, lang, name FROM tag_name"):
        names.setdefault(r[0], {})[r[1]] = r[2]
    assert names[before["Проливка"]] == {"ru": "Проливка", "en": "Wash"}
    assert names[before["Primer"]] == {"ru": "Primer", "en": "Primer"}
    assert names[before["Грунт"]] == {"ru": "Грунт", "en": "Грунт"}  # "Primer" was taken
    assert conn.execute("SELECT tag_id FROM paint_tag").fetchone()[0] == before["Проливка"]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tag)")]
    assert cols == ["id", "color"]
    conn.close()


# ---------------- storages work the same way ----------------

def storage(client, ru_name):
    return next(s for s in client.get("/api/storages").json() if s["names"]["ru"] == ru_name)


def test_default_storage_has_an_english_name(client):
    assert storage(client, "Под рукой")["names"] == {"ru": "Под рукой", "en": "At hand"}


def test_storage_rename_in_one_language(client):
    box = client.post("/api/storages", json={"name": "Коробка 1"}).json()
    assert box["names"] == {"ru": "Коробка 1", "en": "Коробка 1"}
    r = client.patch(f"/api/storages/{box['id']}", json={"name": "Box 1", "lang": "en"})
    assert r.json()["names"] == {"ru": "Коробка 1", "en": "Box 1"}
    assert client.patch(f"/api/storages/{box['id']}", json={"name": "At hand", "lang": "en"}).status_code == 409
    assert client.post("/api/storages", json={"name": "коробка 1"}).status_code == 409


def test_paint_locations_and_csv_use_storage_names(client):
    box = client.post("/api/storages", json={"name": "Коробка 1"}).json()
    client.patch(f"/api/storages/{box['id']}", json={"name": "Box 1", "lang": "en"})
    p = client.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "storage_id": box["id"]}).json()
    assert p["locations"][0]["storage"]["names"] == {"ru": "Коробка 1", "en": "Box 1"}
    csv_en = client.get("/api/backup/paints.csv?lang=en").content.decode("utf-8-sig")
    assert "Box 1 ×1" in csv_en
    csv_ru = client.get("/api/backup/paints.csv?lang=ru").content.decode("utf-8-sig")
    assert "Коробка 1 ×1" in csv_ru


def test_storage_migration_keeps_ids(tmp_path):
    path = tmp_path / "paints.sqlite3"
    conn = db.connect(path)
    for n, script in enumerate(db.MIGRATIONS[:14], start=1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {n}; COMMIT;")
        if n == 1:
            db._seed_tags(conn)
    with conn:
        box = conn.execute("INSERT INTO storage (name) VALUES ('Коробка 1')").lastrowid
        hand = conn.execute("SELECT id FROM storage WHERE name = 'Под рукой'").fetchone()[0]
    conn.close()

    db.init_db(path)

    conn = db.connect(path)
    names = {}
    for r in conn.execute("SELECT storage_id, lang, name FROM storage_name"):
        names.setdefault(r[0], {})[r[1]] = r[2]
    assert names[hand] == {"ru": "Под рукой", "en": "At hand"}
    assert names[box] == {"ru": "Коробка 1", "en": "Коробка 1"}
    assert "name" not in [r[1] for r in conn.execute("PRAGMA table_info(storage)")]
    conn.close()
