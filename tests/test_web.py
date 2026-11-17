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


@pytest.mark.parametrize("path", ["/static/app.js", "/static/i18n.js", "/static/js/core.js", "/static/style.css",
                                  "/static/icon.svg"])
def test_static_files(client, path):
    r = client.get(path)
    assert r.status_code == 200
    # scripts import each other by plain names, so the browser must ask the server every time
    assert r.headers["cache-control"] == "no-cache"
    again = client.get(path, headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == "no-cache"


def test_page_starts_the_app_as_a_module(client):
    html = client.get("/").text
    assert '<script type="module" src="/static/app.js?v=' in html
    assert html.count("<script") == 1


def test_scripts_import_only_what_is_exported():
    """The page has no build step to catch a mistyped import: a missing file or name
    would only show as a blank page in the browser. Check them here."""
    import re
    from pathlib import Path

    web = Path(__file__).resolve().parent.parent / "paint_accountant" / "web"
    files = [web / "app.js", web / "i18n.js", *sorted((web / "js").glob("*.js"))]
    exports = {f: set(re.findall(r"^export (?:async )?(?:function|const|let|class) (\w+)", f.read_text(encoding="utf-8"), re.M))
               for f in files}
    seen = 0
    for f in files:
        for names, target in re.findall(r"^import \{([^}]*)\} from '([^']+)';", f.read_text(encoding="utf-8"), re.M):
            other = (f.parent / target).resolve()
            assert other in exports, f"{f.name} imports from {target}, which is not there"
            missing = {n.strip() for n in names.split(",") if n.strip()} - exports[other]
            assert not missing, f"{f.name} imports {sorted(missing)} that {other.name} does not export"
            seen += 1
    assert seen > 20
    assert "import" not in (web / "i18n.js").read_text(encoding="utf-8").split("const EN")[0]  # loads first, alone


def test_storage_short_link_for_nfc(client):
    r = client.get("/storage/3", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/#/storage/3"


def test_info(client):
    data = client.get("/api/info").json()
    assert data["version"]
    assert data["lan_url"] is None or data["lan_url"].endswith(":8642")


def test_qr_code_svg(client):
    r = client.get("/api/qr.svg", params={"data": "http://192.168.1.5:8642/storage/1"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.text.startswith("<svg") and "viewBox" in r.text
    assert client.get("/api/qr.svg").status_code == 422
