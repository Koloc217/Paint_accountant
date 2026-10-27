"""tools/storage_id.py: renumbering a storage keeps its pots and settings."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import load_settings

spec = importlib.util.spec_from_file_location("storage_id", Path(__file__).parent.parent / "tools" / "storage_id.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def _setup(tmp_path):
    s = load_settings(data_dir=tmp_path)
    with TestClient(create_app(s)) as c:
        hand = next(x for x in c.get("/api/storages").json() if x["name"] == "Под рукой")
        box = c.post("/api/storages", json={"name": "Коробка"}).json()
        c.patch(f"/api/storages/{hand['id']}", json={"copies_storage_id": box["id"]})
        p = c.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "storage_id": hand["id"]}).json()
        c.post(f"/api/paints/{p['id']}/add", json={"storage_id": box["id"], "count": 2})
    return s, hand["id"], box["id"]


def _state(s):
    with TestClient(create_app(s)) as c:
        return {x["name"]: (x["id"], x["pot_count"], (x["copies_storage"] or {}).get("name"))
                for x in c.get("/api/storages").json()}


def test_swap_with_taken_number(tmp_path):
    s, hand, box = _setup(tmp_path)
    conn = sqlite3.connect(s.db_path)
    assert tool.renumber(conn, hand, box) is True
    conn.close()
    st = _state(s)
    assert st["Под рукой"] == (box, 1, "Коробка")
    assert st["Коробка"] == (hand, 2, None)


def test_move_to_free_number(tmp_path):
    s, hand, box = _setup(tmp_path)
    conn = sqlite3.connect(s.db_path)
    assert tool.renumber(conn, hand, 50) is False
    conn.close()
    assert _state(s)["Под рукой"] == (50, 1, "Коробка")
