from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import load_settings


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(load_settings(data_dir=tmp_path))) as c:
        yield c


def _fill(c):
    box = c.post("/api/storages", json={"name": "Коробка 1"}).json()
    hand = next(s for s in c.get("/api/storages").json() if s["at_hand"])
    tags = {t["name"]: t["id"] for t in c.get("/api/tags").json()}
    c.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "hex": "#1c1c1c",
                                "quantity": 2, "storage_id": box["id"], "tag_ids": [tags["Проливка"]]})
    c.post("/api/paints", json={"name": "Gunmetal Grey", "manufacturer": "Vallejo", "code": "70.863",
                                "storage_id": hand["id"], "tag_ids": [tags["Металлик"]],
                                "barcode": "8429551708630"})
    c.post("/api/paints", json={"name": "Abaddon Black", "manufacturer": "Citadel", "quantity": 0})
    return box, hand, tags


def _get(c, **params):
    r = c.get("/api/export/paints.md", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    return r.text


def test_short_lists_only_paints_in_stock_by_manufacturer(client):
    _fill(client)
    text = _get(client)
    assert text.startswith("# Мои краски для миниатюр")
    assert "в наличии: 2" in text
    assert "- Nuln Oil — #1c1c1c — Проливка" in text
    assert "- Gunmetal Grey (70.863) — Металлик" in text
    assert "Abaddon" not in text
    assert text.index("## Citadel") < text.index("## Vallejo")
    # no storages, pots or barcodes in the short one
    assert "Коробка" not in text and "8429551708630" not in text and "×" not in text
    assert "часть каталога" not in text


def test_full_has_storages_pots_barcodes_and_out_of_stock(client):
    _fill(client)
    text = _get(client, level="full")
    assert "Красок: 3, в наличии 2, банок 3." in text
    assert "## Хранилища" in text and "- Коробка 1 — красок 1, банок 2" in text
    assert "- Nuln Oil — #1c1c1c — Проливка — Коробка 1 ×2" in text
    assert "Gunmetal Grey (70.863) — Металлик — " in text and "×1 ✋ — штрихкод 8429551708630" in text
    out = text.index("## Нет в наличии")
    assert text.index("- Citadel Abaddon Black", out) > out


def test_filters_apply_and_are_mentioned(client):
    _, _, tags = _fill(client)
    text = _get(client, tag_id=tags["Металлик"])
    assert "Gunmetal" in text and "Nuln" not in text
    assert "Это часть каталога, выбранная фильтром." in text
    out = _get(client, in_stock="false")
    assert "закончились: 1" in out and "Abaddon Black" in out and "Nuln" not in out


def test_english_download_and_empty(client):
    _fill(client)
    r = client.get("/api/export/paints.md", params={"level": "full", "lang": "en", "download": "true"})
    assert "attachment" in r.headers["content-disposition"] and "paints-full-" in r.headers["content-disposition"]
    assert r.text.startswith("# Paint catalog") and "## Out of stock" in r.text
    assert "Подходящих красок нет." in _get(client, q="nothing like this")


def test_bad_level_is_rejected(client):
    assert client.get("/api/export/paints.md", params={"level": "huge"}).status_code == 422
