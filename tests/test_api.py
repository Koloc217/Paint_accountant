from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import load_settings


@pytest.fixture()
def client(tmp_path):
    app = create_app(load_settings(data_dir=tmp_path))
    with TestClient(app) as c:
        yield c


def _paint(client, **kw):
    body = {"name": "Mephiston Red", "manufacturer": "Citadel"} | kw
    r = client.post("/api/paints", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_health_and_starter_tags(client):
    assert client.get("/api/health").json() == {"status": "ok", "schema_version": 2}
    tags = client.get("/api/tags").json()
    names = [t["name"] for t in tags]
    assert "Проливка" in names and "Контраст" in names
    wash = next(t for t in tags if t["name"] == "Проливка")
    assert set(wash["keywords"]) == {"shade", "wash"}


def test_docs_still_available(client):
    assert client.get("/docs").status_code == 200


def test_paint_crud_and_hex_normalization(client):
    p = _paint(client, hex="C01", code=" 21-03 ")
    assert p["hex"] == "#cc0011"
    assert p["code"] == "21-03"
    assert p["quantity"] == 1
    assert p["storage"] is None and p["tags"] == []

    r = client.patch(f"/api/paints/{p['id']}", json={"quantity": 0, "hex": None})
    assert r.status_code == 200
    assert r.json()["quantity"] == 0 and r.json()["hex"] is None
    assert r.json()["code"] == "21-03"  # untouched field stays

    assert client.delete(f"/api/paints/{p['id']}").status_code == 204
    assert client.get(f"/api/paints/{p['id']}").status_code == 404


def test_invalid_input(client):
    assert client.post("/api/paints", json={"name": "X", "manufacturer": "Y", "hex": "zzz"}).status_code == 422
    assert client.post("/api/paints", json={"name": " ", "manufacturer": "Y"}).status_code == 422
    assert client.post("/api/paints", json={"name": "X", "manufacturer": "Y", "quantity": -1}).status_code == 422
    assert client.post("/api/paints", json={"name": "X", "manufacturer": "Y", "storage_id": 999}).status_code == 422
    assert client.post("/api/paints", json={"name": "X", "manufacturer": "Y", "tag_ids": [999]}).status_code == 422
    p = _paint(client)
    assert client.patch(f"/api/paints/{p['id']}", json={"name": None}).status_code == 422


def test_duplicate_paint_is_case_insensitive_including_cyrillic(client):
    _paint(client, name="Кровь дракона", manufacturer="Кимера")
    r = client.post("/api/paints", json={"name": "кровь ДРАКОНА", "manufacturer": "кимера"})
    assert r.status_code == 409
    # same name, different code is a different paint
    _paint(client, name="Кровь дракона", manufacturer="Кимера", code="K-01")


def test_search_and_filters(client):
    box = client.post("/api/storages", json={"name": "Коробка 1"}).json()
    desk = client.post("/api/storages", json={"name": "Ящик у стола", "at_hand": True}).json()
    tags = {t["name"]: t["id"] for t in client.get("/api/tags").json()}

    a = _paint(client, name="Nuln Oil", storage_id=box["id"], tag_ids=[tags["Проливка"]])
    b = _paint(client, name="Black Templar", storage_id=desk["id"], tag_ids=[tags["Контраст"]], quantity=0)
    c = _paint(client, name="Зелёная тень", manufacturer="Kimera")

    ids = lambda r: {p["id"] for p in r.json()}  # noqa: E731
    assert ids(client.get("/api/paints", params={"q": "nuln"})) == {a["id"]}
    assert ids(client.get("/api/paints", params={"q": "ЗЕЛЁН"})) == {c["id"]}
    assert ids(client.get("/api/paints", params={"q": "citadel"})) == {a["id"], b["id"]}
    assert ids(client.get("/api/paints", params={"storage_id": box["id"]})) == {a["id"]}
    assert ids(client.get("/api/paints", params={"no_storage": True})) == {c["id"]}
    assert ids(client.get("/api/paints", params={"at_hand": True})) == {b["id"]}
    assert ids(client.get("/api/paints", params={"in_stock": False})) == {b["id"]}
    assert ids(client.get("/api/paints", params={"tag_id": tags["Проливка"]})) == {a["id"]}
    assert ids(client.get("/api/paints", params=[("tag_id", tags["Проливка"]), ("tag_id", tags["Контраст"])])) == set()
    assert ids(client.get("/api/paints", params={"q": "%"})) == set()  # LIKE wildcards are escaped

    makers = {m["name"]: m["paint_count"] for m in client.get("/api/manufacturers").json()}
    assert makers["Citadel"] == 2 and makers["Kimera"] == 1 and makers["Tamiya"] == 0
    assert [m["name"] for m in client.get("/api/manufacturers", params={"q": "kim"}).json()] == ["Kimera"]
    kimera = next(m["id"] for m in client.get("/api/manufacturers").json() if m["name"] == "Kimera")
    assert ids(client.get("/api/paints", params={"manufacturer_id": kimera})) == {c["id"]}
    assert ids(client.get("/api/paints", params={"manufacturer": "citadel"})) == {a["id"], b["id"]}


def test_storages(client):
    s = client.post("/api/storages", json={"name": "Коробка 2", "nfc_tag": "04:A2:B3"}).json()
    assert s["at_hand"] is False and s["paint_count"] == 0
    assert client.post("/api/storages", json={"name": "коробка 2"}).status_code == 409

    p = _paint(client, storage_id=s["id"], quantity=3)
    s2 = client.get(f"/api/storages/{s['id']}").json()
    assert s2["paint_count"] == 1 and s2["pot_count"] == 3

    r = client.patch(f"/api/storages/{s['id']}", json={"at_hand": True, "name": "Органайзер"})
    assert r.json()["at_hand"] is True and r.json()["name"] == "Органайзер"
    assert client.get(f"/api/paints/{p['id']}").json()["storage"]["at_hand"] is True

    # deleting a storage keeps its paints, they just lose the storage
    assert client.delete(f"/api/storages/{s['id']}").status_code == 204
    assert client.get(f"/api/paints/{p['id']}").json()["storage"] is None


def test_tags_crud_and_paint_tags(client):
    t = client.post("/api/tags", json={"name": "Флуоресцент", "color": "0f0"}).json()
    assert t["color"] == "#00ff00"
    assert client.post("/api/tags", json={"name": "флуоресцент"}).status_code == 409

    p = _paint(client, tag_ids=[t["id"]])
    assert [x["name"] for x in p["tags"]] == ["Флуоресцент"]
    p = client.patch(f"/api/paints/{p['id']}", json={"tag_ids": []}).json()
    assert p["tags"] == []

    client.patch(f"/api/paints/{p['id']}", json={"tag_ids": [t["id"]]})
    assert client.delete(f"/api/tags/{t['id']}").status_code == 204
    assert client.get(f"/api/paints/{p['id']}").json()["tags"] == []


def test_reopen_keeps_data(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as c:
        _paint(c)
    with TestClient(create_app(settings)) as c:
        assert len(c.get("/api/paints").json()) == 1
        assert len(c.get("/api/tags").json()) == 10  # starter tags not duplicated


def test_starter_manufacturers(client):
    names = [m["name"] for m in client.get("/api/manufacturers").json()]
    assert names == ["AK", "Citadel", "Green Stuff World", "Kaleido", "Kimera", "Tamiya", "Vallejo"]


def test_paint_by_manufacturer_id_or_name(client):
    vallejo = next(m for m in client.get("/api/manufacturers").json() if m["name"] == "Vallejo")
    p = _paint(client, manufacturer=None, manufacturer_id=vallejo["id"], name="Dead White")
    assert p["manufacturer"] == "Vallejo" and p["manufacturer_id"] == vallejo["id"]

    # name matches an existing manufacturer ignoring case — no duplicate created
    p2 = _paint(client, manufacturer="vallejo", name="Black")
    assert p2["manufacturer_id"] == vallejo["id"]

    # a new name is added to the list
    p3 = _paint(client, manufacturer="Scale75", name="Black Rock")
    assert "Scale75" in [m["name"] for m in client.get("/api/manufacturers").json()]

    # moving a paint to another manufacturer
    ak = next(m for m in client.get("/api/manufacturers").json() if m["name"] == "AK")
    moved = client.patch(f"/api/paints/{p3['id']}", json={"manufacturer_id": ak["id"]}).json()
    assert moved["manufacturer"] == "AK"

    assert client.post("/api/paints", json={"name": "X"}).status_code == 422
    assert client.post("/api/paints", json={"name": "X", "manufacturer_id": 999}).status_code == 422


def test_manufacturer_crud(client):
    m = client.post("/api/manufacturers", json={"name": " Army Painter "}).json()
    assert m["name"] == "Army Painter" and m["paint_count"] == 0
    assert client.post("/api/manufacturers", json={"name": "army painter"}).status_code == 409
    assert client.post("/api/manufacturers", json={"name": "  "}).status_code == 422

    r = client.patch(f"/api/manufacturers/{m['id']}", json={"name": "The Army Painter"})
    assert r.json()["name"] == "The Army Painter"
    assert client.patch(f"/api/manufacturers/{m['id']}", json={"name": "citadel"}).status_code == 409

    p = _paint(client, manufacturer=None, manufacturer_id=m["id"], name="Matt Black")
    assert client.delete(f"/api/manufacturers/{m['id']}").status_code == 409  # still has paints
    client.delete(f"/api/paints/{p['id']}")
    assert client.delete(f"/api/manufacturers/{m['id']}").status_code == 204
    assert client.delete(f"/api/manufacturers/{m['id']}").status_code == 404
