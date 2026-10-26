from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import load_settings


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(load_settings(data_dir=tmp_path))) as c:
        yield c


def test_index_serves_ui_with_versioned_assets(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    assert "__V__" not in r.text
    assert "/static/app.js?v=" in r.text


@pytest.mark.parametrize("path", ["/static/app.js", "/static/style.css", "/static/icon.svg"])
def test_static_files(client, path):
    assert client.get(path).status_code == 200


def test_storage_short_link_for_nfc(client):
    r = client.get("/storage/3", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/#/storage/3"


def test_info(client):
    data = client.get("/api/info").json()
    assert data["version"]
    assert data["lan_url"] is None or data["lan_url"].endswith(":8642")
