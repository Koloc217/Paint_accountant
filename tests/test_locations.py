"""Where pots lie: the default storage, the copies rule, explicit storages, moving."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import load_settings


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(load_settings(data_dir=tmp_path))) as c:
        yield c


@pytest.fixture()
def st(client):
    """Storages: the default 'Под рукой' (copies go to 'Запас') and 'Коробка 1'."""
    storages = {s["name"]: s for s in client.get("/api/storages").json()}
    hand = storages["Под рукой"]
    reserve = client.post("/api/storages", json={"name": "Запас"}).json()
    box = client.post("/api/storages", json={"name": "Коробка 1"}).json()
    r = client.patch(f"/api/storages/{hand['id']}", json={"copies_storage_id": reserve["id"]})
    assert r.json()["copies_storage"] == {"id": reserve["id"], "name": "Запас"}
    return {"hand": hand["id"], "reserve": reserve["id"], "box": box["id"]}


def where(paint):
    return {(loc["storage"]["name"] if loc["storage"] else None): loc["quantity"] for loc in paint["locations"]}


def new_paint(client, **kw):
    r = client.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel"} | kw)
    assert r.status_code == 201, r.text
    return r.json()


def test_first_pot_goes_at_hand_extra_pots_to_copies(client, st):
    p = new_paint(client, quantity=3)
    assert where(p) == {"Под рукой": 1, "Запас": 2}
    assert {(x["storage"]["name"], x["count"]) for x in p["notice"]["placed"]} == {("Под рукой", 1), ("Запас", 2)}

    r = client.post(f"/api/paints/{p['id']}/add").json()
    assert where(r["paint"]) == {"Под рукой": 1, "Запас": 3}
    assert r["notice"]["placed"] == [{"storage": {"id": st["reserve"], "name": "Запас", "at_hand": False}, "count": 1}]


def test_explicit_storage_takes_everything(client, st):
    p = new_paint(client, quantity=2, storage_id=st["box"])
    assert where(p) == {"Коробка 1": 2}
    r = client.post(f"/api/paints/{p['id']}/add", json={"storage_id": st["box"], "count": 2}).json()
    assert where(r["paint"]) == {"Коробка 1": 4}


def test_warns_when_paint_is_elsewhere_but_not_at_hand(client, st):
    p = new_paint(client, storage_id=st["box"])
    r = client.post(f"/api/paints/{p['id']}/add").json()
    assert where(r["paint"]) == {"Под рукой": 1, "Коробка 1": 1}
    assert [(x["storage"]["name"], x["quantity"]) for x in r["notice"]["already_in"]] == [("Коробка 1", 1)]
    # already at hand now: no warning, the next pot is a copy
    r = client.post(f"/api/paints/{p['id']}/add").json()
    assert r["notice"]["already_in"] == []
    assert where(r["paint"]) == {"Под рукой": 1, "Коробка 1": 1, "Запас": 1}


def test_changing_copies_storage_only_affects_new_pots(client, st):
    p = new_paint(client, quantity=2)
    client.patch(f"/api/storages/{st['hand']}", json={"copies_storage_id": st["box"]})
    r = client.post(f"/api/paints/{p['id']}/add").json()
    assert where(r["paint"]) == {"Под рукой": 1, "Запас": 1, "Коробка 1": 1}
    # copies rule off
    client.patch(f"/api/storages/{st['hand']}", json={"copies_storage_id": None})
    r = client.post(f"/api/paints/{p['id']}/add").json()
    assert where(r["paint"])["Под рукой"] == 2


def test_remove_takes_from_hand_first_and_offers_refill(client, st):
    p = new_paint(client, quantity=3)  # hand 1, reserve 2
    r = client.post(f"/api/paints/{p['id']}/remove").json()
    assert where(r["paint"]) == {"Запас": 2}
    assert [s["name"] for s in r["notice"]["emptied"]] == ["Под рукой"]
    assert [loc["storage"]["name"] for loc in r["notice"]["refill_from"]] == ["Запас"]

    r = client.post(f"/api/paints/{p['id']}/move", json={"from_storage_id": st["reserve"], "to_storage_id": st["hand"]}).json()
    assert where(r["paint"]) == {"Под рукой": 1, "Запас": 1}

    r = client.post(f"/api/paints/{p['id']}/remove", json={"storage_id": st["reserve"]}).json()
    assert where(r["paint"]) == {"Под рукой": 1}
    assert r["notice"]["refill_from"] == []

    r = client.post(f"/api/paints/{p['id']}/remove").json()
    assert r["paint"]["quantity"] == 0 and r["paint"]["locations"] == []
    assert client.post(f"/api/paints/{p['id']}/remove").status_code == 409  # nothing left


def test_move_ignores_copies_rule_and_checks_amounts(client, st):
    p = new_paint(client, quantity=1, storage_id=st["box"])
    r = client.post(f"/api/paints/{p['id']}/add").json()  # hand 1
    r = client.post(f"/api/paints/{p['id']}/move", json={"from_storage_id": st["box"], "to_storage_id": st["hand"]}).json()
    assert where(r["paint"]) == {"Под рукой": 2}  # moved on purpose, so not redirected to copies
    bad = client.post(f"/api/paints/{p['id']}/move", json={"from_storage_id": st["box"], "to_storage_id": st["hand"]})
    assert bad.status_code == 409
    assert client.post(f"/api/paints/{p['id']}/move", json={"from_storage_id": st["hand"], "to_storage_id": 999}).status_code == 422


def test_default_storage_rules(client, st):
    # only one default at a time
    r = client.patch(f"/api/storages/{st['box']}", json={"is_default": True})
    assert r.json()["is_default"] is True
    defaults = [s["name"] for s in client.get("/api/storages").json() if s["is_default"]]
    assert defaults == ["Коробка 1"]
    p = new_paint(client)
    assert where(p) == {"Коробка 1": 1}
    # the default can't be deleted
    assert client.delete(f"/api/storages/{st['box']}").status_code == 409
    # a storage can't send copies to itself
    assert client.patch(f"/api/storages/{st['box']}", json={"copies_storage_id": st["box"]}).status_code == 422
    # deleting the copies storage turns the rule off
    assert client.delete(f"/api/storages/{st['reserve']}").status_code == 204
    hand = client.get(f"/api/storages/{st['hand']}").json()
    assert hand["copies_storage"] is None


def test_deleting_storage_merges_pots_into_no_storage(client, st):
    p = new_paint(client, quantity=2, storage_id=None)
    client.post(f"/api/paints/{p['id']}/add", json={"storage_id": st["box"], "count": 3})
    assert client.delete(f"/api/storages/{st['box']}").status_code == 204
    assert where(client.get(f"/api/paints/{p['id']}").json()) == {None: 5}


def test_storage_counts_and_filters(client, st):
    a = new_paint(client, quantity=3)               # hand 1, reserve 2
    b = new_paint(client, name="Leadbelcher", storage_id=st["box"])
    storages = {s["name"]: s for s in client.get("/api/storages").json()}
    assert (storages["Запас"]["paint_count"], storages["Запас"]["pot_count"]) == (1, 2)
    ids = lambda **params: {p["id"] for p in client.get("/api/paints", params=params).json()}  # noqa: E731
    assert ids(storage_id=st["reserve"]) == {a["id"]}
    assert ids(at_hand=True) == {a["id"]}
    assert ids(at_hand=False) == {b["id"]}
