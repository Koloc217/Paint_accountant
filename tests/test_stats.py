"""GET /api/stats: where the pots are, repeats, probable duplicate records, tidy-up counts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import load_settings


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(load_settings(data_dir=tmp_path))) as c:
        yield c


def test_empty_catalog(client):
    d = client.get("/api/stats").json()
    assert d["totals"]["paints"] == 0 and d["totals"]["pots"] == 0
    assert d["repeated"]["top"] == [] and d["possible_duplicates"] == [] and d["tags"] == []


def test_busiest_storage_repeats_and_spread(client):
    desk = client.post("/api/storages", json={"name": "Тумба"}).json()
    empty = client.post("/api/storages", json={"name": "Пустая"}).json()
    a = client.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "storage_id": desk["id"],
                                         "quantity": 3}).json()
    client.post(f"/api/paints/{a['id']}/add", json={"count": 1})  # one more, into the default "at hand" storage
    client.post("/api/paints", json={"name": "Black", "manufacturer": "Vallejo", "storage_id": desk["id"]})
    client.post("/api/paints", json={"name": "Loose", "manufacturer": "Vallejo", "storage_id": None})
    client.post("/api/paints", json={"name": "Gone", "manufacturer": "AK", "quantity": 0})

    d = client.get("/api/stats").json()
    assert d["totals"] == {"paints": 4, "pots": 6, "in_stock": 3, "out_of_stock": 1,
                           "storages": 3, "manufacturers": 3}
    assert d["storages"][0]["id"] == desk["id"] and d["storages"][0]["pots"] == 4  # busiest first
    assert d["empty_storages"] == 1 and any(s["id"] == empty["id"] and s["pots"] == 0 for s in d["storages"])
    assert d["without_storage"] == {"paints": 1, "pots": 1}
    assert d["repeated"]["paints"] == 1 and d["repeated"]["extra_pots"] == 3
    assert d["repeated"]["top"][0]["id"] == a["id"] and d["repeated"]["top"][0]["pots"] == 4
    assert d["repeated"]["spread"] == 1  # Nuln Oil lies in two storages
    assert d["manufacturers"][0]["name"] == "Citadel"


def test_possible_duplicates_and_tidy(client):
    tag = client.get("/api/tags").json()[0]
    client.post("/api/paints", json={"name": "Doombull Brown", "manufacturer": "Citadel", "code": "LAYER",
                                     "barcode": "111", "hex": "#704214", "tag_ids": [tag["id"]]})
    b = client.post("/api/paints", json={"name": "  doombull   BROWN ", "manufacturer": "Citadel"}).json()
    client.post("/api/paints", json={"name": "Doombull Brown", "manufacturer": "Kimera"})  # another maker: not a twin
    d = client.get("/api/stats").json()
    assert d["possible_duplicates_total"] == 1
    g = d["possible_duplicates"][0]
    assert g["manufacturer"] == "Citadel" and [p["id"] for p in g["paints"]][1] == b["id"]
    assert d["tidy"] == {"no_tag": 2, "no_color": 2, "no_barcode": 2}
    assert len(d["tags"]) == 1 and d["tags"][0]["paints"] == 1


def test_user_created_manufacturers_in_stats(client):
    # Create paints across 10 different manufacturers (including user-created ones)
    makers = ["AK", "Army Painter", "Citadel", "Green Stuff World", "Kaleido",
              "Kimera", "Mr. Hobby", "Scale75", "Vallejo", "Daler-Rowney", "Pacific88"]
    for i, m in enumerate(makers):
        client.post("/api/paints", json={"name": f"Paint {i}", "manufacturer": m, "quantity": 1})

    d = client.get("/api/stats").json()
    stat_makers = [x["name"] for x in d["manufacturers"]]
    # All 11 manufacturers, including user-created Daler-Rowney and Pacific88, must be present
    for m in makers:
        assert m in stat_makers
    assert len(stat_makers) == len(makers)


def test_color_spectrum_in_stats(client):
    client.post("/api/paints", json={"name": "Khorne Red", "manufacturer": "Citadel", "hex": "#e53935"})
    client.post("/api/paints", json={"name": "Macragge Blue", "manufacturer": "Citadel", "hex": "#1e88e5"})
    client.post("/api/paints", json={"name": "Mournfang Brown", "manufacturer": "Citadel", "hex": "#6d4c41"})
    client.post("/api/paints", json={"name": "No Color Paint", "manufacturer": "Vallejo"})

    d = client.get("/api/stats").json()
    assert "colors" in d
    assert d["colors"]["total_with_color"] == 3
    assert d["colors"]["without_color"] == 1
    group_ids = [g["id"] for g in d["colors"]["groups"]]
    assert "red" in group_ids
    assert "blue" in group_ids
    assert "brown" in group_ids
    red_grp = next(g for g in d["colors"]["groups"] if g["id"] == "red")
    assert red_grp["paints"] == 1
    assert red_grp["samples"][0]["name"] == "Khorne Red"


