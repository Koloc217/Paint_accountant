from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from paint_accountant import backup, db
from paint_accountant.__main__ import main
from paint_accountant.app import create_app
from paint_accountant.config import load_settings


def _client(path):
    return TestClient(create_app(load_settings(data_dir=path)))


@pytest.fixture()
def client(tmp_path):
    with _client(tmp_path / "a") as c:
        yield c


def _fill(c):
    box = c.post("/api/storages", json={"name": "Коробка 1"}).json()
    wash = next(t for t in c.get("/api/tags").json() if t["name"] == "Проливка")
    c.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "hex": "#1c1c1c",
                                "quantity": 2, "storage_id": box["id"], "tag_ids": [wash["id"]]})
    c.post("/api/paints", json={"name": "Gunmetal Grey", "manufacturer": "Vallejo", "code": "70.863"})
    c.post("/api/paints", json={"name": "Abaddon Black", "manufacturer": "Citadel", "quantity": 0})
    return box


def _rows(data: bytes, delimiter=";"):
    assert data.startswith("﻿".encode())  # BOM: Excel shows Cyrillic correctly
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=delimiter))


def test_csv_sorted_by_manufacturer_with_colors(client):
    _fill(client)
    r = client.get("/api/backup/paints.csv")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    rows = _rows(r.content)
    assert rows[0] == backup.CSV_HEADERS["ru"]
    assert [(x[0], x[1]) for x in rows[1:]] == [
        ("Citadel", "Abaddon Black"), ("Citadel", "Nuln Oil"), ("Vallejo", "Gunmetal Grey")]
    nuln = rows[2]
    assert nuln[3] == "#1c1c1c" and nuln[4] == "2" and nuln[5] == "Коробка 1 ×2" and nuln[6] == "Проливка"
    assert rows[1][3] == "" and rows[1][4] == "0"  # no color, none left
    assert rows[3][2] == "70.863"


def test_csv_in_english_uses_commas(client):
    _fill(client)
    rows = _rows(client.get("/api/backup/paints.csv?lang=en").content, delimiter=",")
    assert rows[0][0] == "Manufacturer" and len(rows) == 4


def test_dump_moves_everything_to_another_machine(client, tmp_path):
    _fill(client)
    client.post("/api/auth/password", json={"password": "secret1"})
    r = client.get("/api/backup/dump")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert set(z.namelist()) >= {"manifest.json", "paints.sqlite3"}
    assert manifest["schema_version"] == len(db.MIGRATIONS) and manifest["counts"]["paint"] == 3

    with _client(tmp_path / "b") as other:
        other.post("/api/paints", json={"name": "Will Be Replaced", "manufacturer": "AK"})
        res = other.post("/api/backup/restore", content=r.content)
        assert res.status_code == 200, res.text
        assert res.json()["counts"]["paint"] == 3
        # the password came along with the catalog
        assert other.get("/api/paints").status_code == 401
        other.post("/api/auth/login", json={"user": "admin", "password": "secret1"})
        names = sorted(p["name"] for p in other.get("/api/paints").json())
        assert names == ["Abaddon Black", "Gunmetal Grey", "Nuln Oil"]
        nuln = other.get("/api/paints?q=nuln").json()[0]
        assert nuln["locations"][0]["storage"]["name"] == "Коробка 1" and nuln["tags"][0]["name"] == "Проливка"
        # the replaced catalog is kept aside
        saved = list((tmp_path / "b").glob("paints.before-restore-*.sqlite3"))
        assert len(saved) == 1
        old = sqlite3.connect(saved[0])
        assert old.execute("SELECT name FROM paint").fetchall() == [("Will Be Replaced",)]
        old.close()


def test_dump_includes_photos(client, tmp_path):
    settings = client.app.state.settings
    (settings.photos_dir / "p1.jpg").write_bytes(b"jpeg")
    data = client.get("/api/backup/dump").content
    with _client(tmp_path / "b") as other:
        assert other.post("/api/backup/restore", content=data).status_code == 200
        assert (other.app.state.settings.photos_dir / "p1.jpg").read_bytes() == b"jpeg"


def test_older_schema_is_upgraded_on_restore(client, tmp_path):
    old = tmp_path / "old.sqlite3"
    conn = db.connect(old)
    conn.execute("PRAGMA foreign_keys = OFF")
    for number, script in enumerate(db.MIGRATIONS[:4], start=1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
    conn.close()
    r = client.post("/api/backup/restore", content=old.read_bytes())  # a bare .sqlite3 works too
    assert r.status_code == 200, r.text
    assert r.json()["from_schema"] == 4
    assert client.get("/api/health").json()["schema_version"] == len(db.MIGRATIONS)


@pytest.mark.parametrize("payload, message", [
    (b"hello", "expected a .zip"),
    (b"", "send the dump"),
])
def test_restore_rejects_junk(client, payload, message):
    _fill(client)
    r = client.post("/api/backup/restore", content=payload)
    assert r.status_code == 422 and message in r.text
    assert len(client.get("/api/paints").json()) == 3  # nothing changed


def _zip(**files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_restore_rejects_foreign_and_newer_databases(client, tmp_path):
    foreign = tmp_path / "f.sqlite3"
    c = sqlite3.connect(foreign)
    c.execute("CREATE TABLE notes (x)")
    c.commit()
    c.close()
    r = client.post("/api/backup/restore", content=_zip(**{"paints.sqlite3": foreign.read_bytes()}))
    assert r.status_code == 422 and "not a Paint Accountant database" in r.text

    newer = client.get("/api/backup/dump").content
    with zipfile.ZipFile(io.BytesIO(newer)) as z:
        dbfile = tmp_path / "n.sqlite3"
        dbfile.write_bytes(z.read("paints.sqlite3"))
    c = sqlite3.connect(dbfile)
    c.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    c.close()
    r = client.post("/api/backup/restore", content=dbfile.read_bytes())
    assert r.status_code == 422 and "newer version" in r.text

    assert client.post("/api/backup/restore", content=_zip(readme="x")).status_code == 422


def test_restore_rejects_paths_outside_photos(client):
    dump = client.get("/api/backup/dump").content
    with zipfile.ZipFile(io.BytesIO(dump)) as z:
        dbdata = z.read("paints.sqlite3")
    bad = _zip(**{"paints.sqlite3": dbdata, "photos/../../evil.txt": "x"})
    r = client.post("/api/backup/restore", content=bad)
    assert r.status_code == 422 and "unsafe" in r.text


def test_backup_needs_sign_in_when_enabled(client):
    client.post("/api/auth/password", json={"password": "secret1"})
    client.post("/api/auth/logout")
    assert client.get("/api/backup/dump").status_code == 401
    assert client.get("/api/backup/paints.csv").status_code == 401
    assert client.post("/api/backup/restore", content=b"x").status_code == 401


def test_command_line_dump_and_restore(client, tmp_path, capsys):
    _fill(client)
    src = client.app.state.settings.data_dir
    target = tmp_path / "copy.zip"
    main(["--data-dir", str(src), "--config", str(tmp_path / "none.ini"), "--dump", str(target)])
    assert zipfile.is_zipfile(target)
    dest = tmp_path / "c"
    main(["--data-dir", str(dest), "--config", str(tmp_path / "none.ini"), "--restore", str(target)])
    assert "Restored: 3 paints" in capsys.readouterr().out
    with _client(dest) as other:
        assert len(other.get("/api/paints").json()) == 3
