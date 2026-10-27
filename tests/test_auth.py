"""Optional sign-in with user admin: off by default, guards the API and docs when on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paint_accountant import auth
from paint_accountant.__main__ import main
from paint_accountant.app import create_app
from paint_accountant.config import load_settings


@pytest.fixture()
def settings(tmp_path):
    return load_settings(data_dir=tmp_path)


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def test_off_by_default(client):
    assert client.get("/api/auth").json() == {"enabled": False, "user": "admin", "signed_in": True}
    assert client.get("/api/paints").status_code == 200
    assert client.get("/docs").status_code == 200


def test_turning_on_guards_api_but_not_the_page(app, client):
    r = client.post("/api/auth/password", json={"password": "kraska"})
    assert r.status_code == 200 and auth.COOKIE in r.cookies
    assert client.get("/api/paints").status_code == 200  # this device stays signed in

    with TestClient(app) as other:  # another phone
        assert other.get("/api/auth").json() == {"enabled": True, "user": "admin", "signed_in": False}
        r = other.get("/api/paints")
        assert r.status_code == 401 and r.json()["detail"]["code"] == "auth_required"
        assert other.post("/api/storages", json={"name": "X"}).status_code == 401
        assert other.post("/api/auth/password", json={"password": "hack"}).status_code == 401
        assert other.post("/api/auth/disable").status_code == 401
        # the app itself, static files, health and storage links still load, so the login screen can show
        assert other.get("/").status_code == 200
        assert other.get("/static/app.js").status_code == 200
        assert other.get("/api/health").status_code == 200
        r = other.get("/storage/1", follow_redirects=False)
        assert r.status_code in (302, 307)
        r = other.get("/docs", follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/#/login"
        assert other.get("/openapi.json", follow_redirects=False).status_code in (302, 307)


def test_login_logout(app, client):
    client.post("/api/auth/password", json={"password": "kraska"})
    with TestClient(app) as phone:
        r = phone.post("/api/auth/login", json={"password": "wrong"})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "wrong_password"
        r = phone.post("/api/auth/login", json={"password": "kraska"})
        assert r.status_code == 200
        assert phone.get("/api/paints").status_code == 200
        assert phone.get("/api/auth").json()["signed_in"] is True
        phone.post("/api/auth/logout")
        assert phone.get("/api/paints").status_code == 401


def test_new_password_signs_out_other_devices(app, client):
    client.post("/api/auth/password", json={"password": "first"})
    with TestClient(app) as phone:
        phone.post("/api/auth/login", json={"password": "first"})
        assert phone.get("/api/paints").status_code == 200
        r = client.post("/api/auth/password", json={"password": "second"})
        assert r.status_code == 200
        assert client.get("/api/paints").status_code == 200   # the device that changed it stays in
        assert phone.get("/api/paints").status_code == 401    # others sign in again
        assert phone.post("/api/auth/login", json={"password": "first"}).status_code == 401
        assert phone.post("/api/auth/login", json={"password": "second"}).status_code == 200


def test_short_password_refused(client):
    assert client.post("/api/auth/password", json={"password": "abc"}).status_code == 422
    assert client.get("/api/auth").json()["enabled"] is False


def test_turning_off(app, client):
    client.post("/api/auth/password", json={"password": "kraska"})
    assert client.post("/api/auth/disable").status_code == 200
    with TestClient(app) as phone:
        assert phone.get("/api/paints").status_code == 200
    # turning on again: an old cookie from before doesn't work
    client.post("/api/auth/password", json={"password": "kraska"})
    with TestClient(app) as phone:
        assert phone.get("/api/paints").status_code == 401


def test_too_many_wrong_passwords(app, client):
    client.post("/api/auth/password", json={"password": "kraska"})
    with TestClient(app) as phone:
        for _ in range(auth.MAX_FAILS):
            assert phone.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        r = phone.post("/api/auth/login", json={"password": "kraska"})
        assert r.status_code == 429 and r.json()["detail"]["wait"] > 0


def test_forged_cookie_refused(app, client):
    client.post("/api/auth/password", json={"password": "kraska"})
    good = client.cookies.get(auth.COOKIE)
    with TestClient(app) as phone:
        for bad in ["", "admin.1.123.abc", good[:-1] + ("0" if good[-1] != "0" else "1"), "x.y", good + ".1"]:
            phone.cookies.set(auth.COOKIE, bad)
            assert phone.get("/api/paints").status_code == 401, bad


def test_password_is_stored_hashed(settings, client):
    client.post("/api/auth/password", json={"password": "kraska"})
    raw = settings.db_path.read_bytes()
    assert b"kraska" not in raw


def test_reset_from_command_line(settings, app, client, capsys):
    client.post("/api/auth/password", json={"password": "forgotten"})
    main(["--data-dir", str(settings.data_dir), "--reset-auth"])
    assert "Sign-in is off" in capsys.readouterr().out
    with TestClient(app) as phone:
        assert phone.get("/api/paints").status_code == 200
