"""Out-of-range numbers and null flags must not crash the server."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import load_settings

BIG = 10**20


@pytest.fixture()
def client(tmp_path):
    app = create_app(load_settings(data_dir=tmp_path))
    with TestClient(app) as c:
        yield c


def test_huge_ids_are_422(client):
    for path in (f"/api/paints/{BIG}", f"/api/storages/{BIG}"):
        assert client.get(path).status_code == 422
    assert client.get("/api/paints", params={"storage_id": BIG}).status_code == 422
    for extra in ({"tag_ids": [BIG]}, {"storage_id": BIG}, {"manufacturer_id": BIG}):
        r = client.post("/api/paints", json={"name": "A", "manufacturer": "X", **extra})
        assert r.status_code == 422, extra


def test_quantity_has_a_limit(client):
    r = client.post("/api/paints", json={"name": "A", "manufacturer": "X", "quantity": 2**62, "storage_id": None})
    assert r.status_code == 422


def test_null_flags_leave_storage_as_is(client):
    default = next(s for s in client.get("/api/storages").json() if s["is_default"])
    for body in ({"is_default": None}, {"is_default": False}, {"at_hand": None}):
        r = client.patch(f"/api/storages/{default['id']}", json=body)
        assert r.status_code == 200
        assert r.json()["is_default"] is True
        assert r.json()["at_hand"] == default["at_hand"]
