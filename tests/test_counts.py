"""Counters on manufacturers, tags and storages count different paints, not entries or pots."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import load_settings


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(load_settings(data_dir=tmp_path))) as c:
        yield c


def add(client, name, code=None, maker="Vallejo", qty=1, tags=()):
    r = client.post("/api/paints", json={"name": name, "manufacturer": maker, "code": code,
                                         "quantity": qty, "tag_ids": list(tags),
                                         "allow_duplicate": True})
    assert r.status_code == 201, r.text
    return r.json()


def count(client, kind, name):
    return next(x["paint_count"] for x in client.get(f"/api/{kind}").json() if x["name"] == name)


def test_same_paint_entered_twice_counts_once(client):
    metal = next(t["id"] for t in client.get("/api/tags").json() if t["name"] == "Металлик")
    add(client, "Gunmetal Grey", "70.863", tags=[metal])
    add(client, "gunmetal  grey", None, tags=[metal])  # entered again by hand, without the code
    add(client, "Black", "70.950", qty=3)               # several pots are still one paint
    assert count(client, "manufacturers", "Vallejo") == 2
    assert count(client, "tags", "Металлик") == 1
    assert count(client, "storages", "Под рукой") == 2
    m = next(x for x in client.get("/api/manufacturers").json() if x["name"] == "Vallejo")
    assert client.patch(f"/api/manufacturers/{m['id']}", json={"name": "Vallejo"}).json()["paint_count"] == 2


def test_same_name_with_different_codes_are_different_paints(client):
    add(client, "Black", "70.950")  # Model Color
    add(client, "Black", "72.051")  # Game Color
    add(client, "Black", None)
    add(client, "Black", maker="Citadel")
    assert count(client, "manufacturers", "Vallejo") == 2
    assert count(client, "manufacturers", "Citadel") == 1
    assert count(client, "storages", "Под рукой") == 3


def test_empty_manufacturer_still_deletable(client):
    m = client.post("/api/manufacturers", json={"name": "Foo"}).json()
    assert m["paint_count"] == 0
    assert client.delete(f"/api/manufacturers/{m['id']}").status_code == 204
