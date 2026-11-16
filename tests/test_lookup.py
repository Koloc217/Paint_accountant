"""Online barcode lookup (UPCitemdb): cleaning the answer, the API, proxies. No real network here."""

from __future__ import annotations

import socket
import struct
import threading

import pytest
from pathlib import Path

from fastapi.testclient import TestClient

from paint_accountant import config, lookup
from paint_accountant.app import create_app
from paint_accountant.config import load_settings

QUICKSILVER = {
    "ean": "8436574502206",
    "title": "Metallic Paint Quicksilver 17ml - Acrylic Brush Airbrush Modelling Hobby",
    "brand": "Green Stuff World",
    "model": "",
    "category": "Toys & Games > Toys > Pretend Play > Toy Tools",
    "offers": [],
}


# a real answer for a Citadel pot: the brand carries a word of its own, the code sits in "model",
# and the shop's title is padded with "Warhammer", "Plastic Model Paint" and the volume
MEPHISTON = {
    "ean": "5011921026203",
    "title": "Mephiston Red Citadel Base Plastic Model Paint (0.4 fl oz)",
    "brand": "Citadel Miniatures",
    "model": "21-03",
    "color": "Red",
    "category": "Toys & Games > Puzzles > Jigsaw Puzzles",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    lookup.clear_cache()
    for name in ("PAINT_LOOKUP", "PAINT_LOOKUP_PROXY", "PAINT_LOOKUP_INTERFACE",
                 "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[server]\nport = 8642\n", encoding="utf-8")
    app = create_app(load_settings(data_dir=tmp_path / "data", config_file=ini))
    with TestClient(app) as c:
        yield c


def _answer(monkeypatch, status=200, data=None, headers=None):
    calls = []

    def fake(barcode, how):
        calls.append((barcode, how))
        return status, headers or {"x-ratelimit-remaining": "97"}, data

    monkeypatch.setattr(lookup, "fetch", fake)
    return calls


# ---------------- cleaning the title ----------------

TAG_WORDS = {"metallic", "metal", "shade", "wash", "primer", "contrast", "ink", "air", "base", "layer"}


@pytest.mark.parametrize("title, names, code, expected", [
    (QUICKSILVER["title"], ["Green Stuff World"], None, "Quicksilver"),
    ("Green Stuff World Metallic Paint Quicksilver 17ml", ["Green Stuff World", "gsw"], None, "Quicksilver"),
    ("Vallejo Model Color 70.950 Black 17ml Acrylic Paint", ["Vallejo", "model color"], "70.950", "Black"),
    ("Citadel Shade: Nuln Oil (18ml)", ["Citadel"], None, "Nuln Oil"),
    ("Games Workshop Citadel Contrast Black Templar 18ml Pot", ["Games Workshop", "Citadel"], None, "Black Templar"),
    ("The Army Painter Warpaints - Matt Black WP1101", ["The Army Painter", "Army Painter"], "WP1101", "Matt Black"),
    ("Black Primer 60 ml", ["Vallejo"], None, "Black"),
    ("Primer", [], None, "Primer"),  # nothing but the kind of paint: keep it
    ("TAMIYA 81301 Acrylic Mini X-1 Black - 10ml Bottle", ["Tamiya"], "X-1", "Black"),
    ("NULN OIL 颜料 18ML", ["Citadel"], None, "Nuln Oil"),
    ("", [], None, ""),
    # the same pot listed by different shops — every title must come down to the same name
    (MEPHISTON["title"], ["Citadel Miniatures", "Citadel"], "21-03", "Mephiston Red"),
    ("Citadel Mephiston Red Warhammer Wargaming In Stock Model Paint 12ml 21-03",
     ["Citadel"], "21-03", "Mephiston Red"),
    ("Warhammer Paint: Mephiston Red", ["Citadel"], None, "Mephiston Red"),
    ("Citadel 21-03 Games Workshop Base Mephiston Red Paints",
     ["Citadel", "games workshop"], "21-03", "Mephiston Red"),
    # a shop's wholesale article is not a name, so the name comes from the piece after the dash
    ("Games Workshop GAW9918995020406 21-55 Base - Runelord Brass Paint",
     ["Games Workshop", "Citadel"], "21-55", "Runelord Brass"),
    ("Runelord Brass Base Paint 12ml", ["Citadel"], None, "Runelord Brass"),
    ("Citadel Base: Runelord Brass", ["Citadel"], None, "Runelord Brass"),
])
def test_clean_title(title, names, code, expected):
    assert lookup.clean_title(title, names, code, TAG_WORDS) == expected


# ---------------- the API ----------------

def test_found_fills_the_form(client, monkeypatch):
    calls = _answer(monkeypatch, data={"code": "OK", "total": 1, "items": [QUICKSILVER]})
    r = client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["found"] is True and d["cached"] is False and d["remaining"] == 97
    assert d["item"]["title"] == QUICKSILVER["title"] and d["item"]["brand"] == "Green Stuff World"
    s = d["suggestion"]
    assert s["name"] == "Quicksilver"
    assert s["manufacturer"]["name"] == "Green Stuff World" and s["new_manufacturer"] is None
    assert s["code"] is None and s["barcode"] == "8436574502206"
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert [tags[i] for i in s["tag_ids"]] == ["Металлик"]
    assert calls == [("8436574502206", lookup.Route())]

    # the second press doesn't spend a request
    assert client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"}).json()["cached"] is True
    assert len(calls) == 1


def test_citadel_pot_with_the_code_in_model(client, monkeypatch):
    _answer(monkeypatch, data={"items": [MEPHISTON]})
    d = client.get("/api/lookup/upcitemdb", params={"barcode": "5011921026203"}).json()
    s = d["suggestion"]
    assert s["name"] == "Mephiston Red"
    assert s["manufacturer"]["name"] == "Citadel"  # "Citadel Miniatures" is our Citadel
    assert s["code"] == "21-03"  # from the item's "model" field
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert [tags[i] for i in s["tag_ids"]] == ["Основа"]
    assert d["item"]["color"] == "Red" and d["item"]["model"] == "21-03"


def test_shop_article_glued_to_the_code(client, monkeypatch):
    _answer(monkeypatch, data={"items": [{"title": "GAW21-03 Mephiston Red Base Paint", "brand": ""}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "5011921026203"}).json()["suggestion"]
    assert (s["name"], s["code"]) == ("Mephiston Red", "21-03")


def test_wholesale_article_never_becomes_the_name(client, monkeypatch):
    """A GW pot as a shop lists it: GAW… is their own article, 21-55 is Citadel's code."""
    _answer(monkeypatch, data={"items": [
        {"title": "Games Workshop GAW9918995020406 21-55 Base - Runelord Brass Paint",
         "brand": "Games Workshop", "model": ""}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "5011921150014"}).json()["suggestion"]
    assert (s["name"], s["code"]) == ("Runelord Brass", "21-55")
    assert s["manufacturer"]["name"] == "Citadel"


def test_several_listings_the_one_with_a_name_wins(client, monkeypatch):
    nothing = {"title": "Citadel Base Paint Pot - Games Workshop - All Colors!", "brand": "Citadel Miniatures"}
    _answer(monkeypatch, data={"items": [nothing, MEPHISTON]})
    d = client.get("/api/lookup/upcitemdb", params={"barcode": "5011921026203"}).json()
    assert d["suggestion"]["name"] == "Mephiston Red"
    assert d["item"]["title"] == MEPHISTON["title"]  # the listing that was used is the one shown


def test_code_and_manufacturer_from_the_title(client, monkeypatch):
    _answer(monkeypatch, data={"items": [{"title": "Model Color 70.950 Black 17ml", "brand": "Acrylicos Vallejo"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "8429551709507"}).json()["suggestion"]
    assert (s["manufacturer"]["name"], s["code"], s["name"]) == ("Vallejo", "70.950", "Black")


def test_unknown_brand_is_offered_as_new(client, monkeypatch):
    _answer(monkeypatch, data={"items": [{"title": "Pro Acryl Bold Titanium White 22ml", "brand": "MONUMENT HOBBIES"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "0850025932011"}).json()["suggestion"]
    assert s["manufacturer"] is None and s["new_manufacturer"] == "Monument Hobbies"
    assert s["code"] is None  # numbers of an unknown brand are not taken for a code
    assert s["name"] == "Pro Acryl Bold Titanium White"


def test_manufacturer_by_barcode_prefix_when_no_brand(client, monkeypatch):
    _answer(monkeypatch, data={"items": [{"title": "Metallic Paint Quicksilver 17ml", "brand": ""}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"}).json()["suggestion"]
    assert s["manufacturer"]["name"] == "Green Stuff World"


def test_not_found(client, monkeypatch):
    _answer(monkeypatch, status=404, data={"code": "NOT_FOUND", "message": "no match"})
    d = client.get("/api/lookup/upcitemdb", params={"barcode": "4006381333931"}).json()
    assert d["found"] is False and "suggestion" not in d
    _answer(monkeypatch, data={"code": "OK", "total": 0, "items": []})
    assert client.get("/api/lookup/upcitemdb", params={"barcode": "12345670"}).json()["found"] is False


def test_limits_and_errors(client, monkeypatch):
    _answer(monkeypatch, status=429, data={"code": "EXCEED_LIMIT"}, headers={"x-ratelimit-reset": "1800000000"})
    r = client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "lookup_limit" and r.json()["detail"]["reset"] == 1800000000
    _answer(monkeypatch, status=429, data={"code": "TOO_FAST"})
    assert client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"}).json()["detail"]["code"] == "lookup_too_fast"
    _answer(monkeypatch, status=500, data={"code": "SERVER_ERR", "message": "oops"})
    r = client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"})
    assert r.status_code == 502 and "oops" in r.json()["detail"]["message"]

    def down(barcode, how):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(lookup, "fetch", down)
    r = client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "lookup_unreachable"
    assert "api.upcitemdb.com" in r.json()["detail"]["message"]  # direct: the site is named


def test_a_dead_proxy_is_named_without_its_password(client, monkeypatch, tmp_path):
    """The usual failure: nothing is listening where the proxy is said to be."""
    ini = client.app.state.settings.config_file
    ini.write_text("[lookup]\nproxy = socks5h://me:secret@192.168.1.60:1080\n", encoding="utf-8")
    client.app.state.settings = load_settings(data_dir=tmp_path / "data", config_file=ini)
    assert client.get("/api/info").json()["lookup"]["route"] == "socks5h://192.168.1.60:1080"

    def refused(barcode, how):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(lookup, "fetch", refused)
    message = client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"}).json()["detail"]["message"]
    assert "socks5h://192.168.1.60:1080 doesn't answer" in message and "secret" not in message


def test_a_proxy_from_the_environment_says_so(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    how = lookup.route(load_settings(config_file=Path("/nonexistent/config.ini")))
    assert how.from_env is True and how.label == "http://127.0.0.1:8080 (from HTTPS_PROXY)"


def test_bad_barcode_never_leaves(client, monkeypatch):
    calls = _answer(monkeypatch, data={"items": []})
    for bad in ("ABC-12345", "1234567890", "http://x/storage/1"):
        r = client.get("/api/lookup/upcitemdb", params={"barcode": bad})
        assert r.status_code == 422, bad
    assert not calls


def test_setting_turns_it_off_and_is_saved(client, monkeypatch):
    calls = _answer(monkeypatch, data={"items": [QUICKSILVER]})
    assert client.get("/api/info").json()["lookup"] == {"upcitemdb": True, "via": "direct", "route": "no proxy"}
    r = client.put("/api/settings/lookup", json={"enabled": False})
    assert r.status_code == 200 and r.json()["upcitemdb"] is False
    assert client.get("/api/info").json()["lookup"]["upcitemdb"] is False
    r = client.get("/api/lookup/upcitemdb", params={"barcode": "8436574502206"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "lookup_disabled"
    assert not calls

    ini = client.app.state.settings.config_file
    text = ini.read_text(encoding="utf-8")
    assert "[lookup]" in text and "upcitemdb = off" in text and "port = 8642" in text
    assert load_settings(config_file=ini).lookup_enabled is False
    client.put("/api/settings/lookup", json={"enabled": True})
    assert ini.read_text(encoding="utf-8").count("upcitemdb =") == 1
    assert load_settings(config_file=ini).lookup_enabled is True


# ---------------- settings and the route ----------------

def test_config_and_route(monkeypatch, tmp_path):
    ini = tmp_path / "config.ini"
    assert load_settings(config_file=ini).lookup_enabled is True  # on unless turned off
    ini.write_text("[lookup]\nupcitemdb = on\nproxy = socks5h://127.0.0.1:1080\ninterface = awg0\n", encoding="utf-8")
    s = load_settings(config_file=ini)
    assert (s.lookup_proxy, s.lookup_interface) == ("socks5h://127.0.0.1:1080", "awg0")
    assert lookup.route(s) == lookup.Route(proxy="socks5h://127.0.0.1:1080")  # the proxy wins

    ini.write_text("[lookup]\ninterface = awg0\n", encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    assert lookup.route(load_settings(config_file=ini)) == lookup.Route(interface="awg0")

    ini.write_text("", encoding="utf-8")
    assert lookup.route(load_settings(config_file=ini)) == lookup.Route(proxy="http://10.0.0.1:3128", from_env=True)
    monkeypatch.setenv("PAINT_LOOKUP_PROXY", "socks5://10.0.0.2:1080")
    monkeypatch.setenv("PAINT_LOOKUP", "off")
    s = load_settings(config_file=ini)
    assert lookup.route(s).proxy == "socks5://10.0.0.2:1080" and s.lookup_enabled is False


def test_example_config_documents_lookup():
    text = config.CONFIG_EXAMPLE.read_text(encoding="utf-8")
    assert "[lookup]" in text and "upcitemdb = on" in text


# ---------------- proxies, against tiny local ones ----------------

def _serve(handler):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    seen: dict = {}

    def run():
        conn, _ = server.accept()
        with conn:
            handler(conn, seen)
            conn.sendall(b"hello")
        server.close()

    threading.Thread(target=run, daemon=True).start()
    return server.getsockname()[1], seen


def _recv(conn, n):
    data = b""
    while len(data) < n:
        data += conn.recv(n - len(data))
    return data


def test_socks5_with_remote_dns_and_password():
    def handler(conn, seen):
        assert _recv(conn, 4) == b"\x05\x02\x00\x02"
        conn.sendall(b"\x05\x02")
        _ver, ulen = _recv(conn, 2)
        user = _recv(conn, ulen)
        seen["auth"] = (user, _recv(conn, _recv(conn, 1)[0]))
        conn.sendall(b"\x01\x00")
        assert _recv(conn, 4) == b"\x05\x01\x00\x03"
        seen["host"] = _recv(conn, _recv(conn, 1)[0])
        seen["port"] = struct.unpack(">H", _recv(conn, 2))[0]
        conn.sendall(b"\x05\x00\x00\x01" + bytes(4) + bytes(2))

    port, seen = _serve(handler)
    sock = lookup.open_tunnel("api.upcitemdb.com", 443, lookup.Route(proxy=f"socks5h://me:p%40ss@127.0.0.1:{port}"))
    assert sock.recv(5) == b"hello"
    sock.close()
    assert seen == {"auth": (b"me", b"p@ss"), "host": b"api.upcitemdb.com", "port": 443}


def test_socks5_refusal_is_explained():
    def handler(conn, seen):
        _recv(conn, 3)
        conn.sendall(b"\x05\x00")
        _recv(conn, 4)
        _recv(conn, _recv(conn, 1)[0] + 2)
        conn.sendall(b"\x05\x05\x00\x01" + bytes(6))

    port, _ = _serve(handler)
    with pytest.raises(OSError, match="connection refused"):
        lookup.open_tunnel("api.upcitemdb.com", 443, lookup.Route(proxy=f"socks5h://127.0.0.1:{port}"))


def test_http_proxy_connect():
    def handler(conn, seen):
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(1)
        seen["head"] = head.decode()
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

    port, seen = _serve(handler)
    sock = lookup.open_tunnel("api.upcitemdb.com", 443, lookup.Route(proxy=f"http://127.0.0.1:{port}"))
    assert sock.recv(5) == b"hello"
    sock.close()
    assert seen["head"].startswith("CONNECT api.upcitemdb.com:443 HTTP/1.1\r\n")


def test_unsupported_proxy_and_interface_by_address():
    with pytest.raises(lookup.LookupFailed, match="isn't supported"):
        lookup.open_tunnel("api.upcitemdb.com", 443, lookup.Route(proxy="https://127.0.0.1:1"))

    def handler(conn, seen):
        seen["ok"] = True

    port, seen = _serve(handler)
    sock = lookup.open_tunnel("127.0.0.1", port, lookup.Route(interface="127.0.0.1"))  # send from this address
    assert sock.recv(5) == b"hello"
    sock.close()
