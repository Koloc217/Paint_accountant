"""Photo recognition. Label photos are generated here (text + real EAN-13 barcodes),
so the tests need no image files. Skipped if the optional photo packages aren't installed."""

from __future__ import annotations

import glob
import io

import pytest
from fastapi.testclient import TestClient

from paint_accountant import recognition
from paint_accountant.app import create_app
from paint_accountant.config import load_settings
from paint_accountant.recognition import Line, Reading, find_code, suggest

pytest.importorskip("rapidfuzz")


# ---------- pure text logic (no OCR needed) ----------

@pytest.mark.parametrize(
    "text, maker, expected",
    [
        ("Model Color 70.951 White", None, "70.951"),
        ("70,951", None, "70.951"),
        ("Exhaust Manifold 77.723 Metal Color", None, "77.723"),
        ("72.001", None, "72.001"),
        ("SHADE 24-14", None, "24-14"),
        ("AK 11001 Pure White", None, "AK11001"),
        ("ak-11001", None, "AK11001"),
        ("WP1101 Matt Black", None, "WP1101"),
        ("XF-1 Flat Black", None, "XF-1"),
        ("Flat Black", None, None),
    ],
)
def test_find_code(text, maker, expected):
    assert find_code(text, maker) == expected


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(load_settings(data_dir=tmp_path))) as c:
        yield c


@pytest.fixture()
def conn(client):
    from paint_accountant.db import connect

    c = connect(client.app.state.settings.db_path)
    yield c
    c.close()


def _reading(*lines, barcodes=()):
    # (text, height) — bigger height = bigger print on the label
    return Reading(
        lines=[Line(text=t, score=0.95, x=10, y=40 * i, height=h) for i, (t, h) in enumerate(lines)],
        barcodes=[{"text": b, "format": "EAN13"} for b in barcodes],
    )


def _tag_id(client, name):
    return next(t["id"] for t in client.get("/api/tags").json() if t["name"] == name)


def test_suggest_new_citadel_shade(client, conn):
    r = suggest(conn, _reading(("CITADEL", 30), ("NULN OIL", 70), ("SHADE", 30), ("24-14", 25), ("18ml", 15)))
    s = r["suggestion"]
    assert s["manufacturer"]["name"] == "Citadel"
    assert s["name"] == "Nuln Oil"
    assert s["code"] == "24-14"
    assert s["tag_ids"] == [_tag_id(client, "Проливка")]
    assert r["matches"] == []
    kinds = {ln["text"]: ln["kind"] for ln in r["lines"]}
    assert kinds == {"CITADEL": "manufacturer", "NULN OIL": "name", "SHADE": "tag", "24-14": "code", "18ml": "noise"}


def test_suggest_vallejo_and_aliases(client, conn):
    r = suggest(conn, _reading(("acrylicos vallejo", 20), ("Model Air", 25), ("71.057", 20), ("Black", 50)))
    assert r["suggestion"]["manufacturer"]["name"] == "Vallejo"
    assert r["suggestion"]["code"] == "71.057"
    assert r["suggestion"]["name"] == "Black"
    assert _tag_id(client, "Для аэрографа") in r["suggestion"]["tag_ids"]

    r = suggest(conn, _reading(("GAMES WORKSHOP", 20), ("Abaddon Black", 50)))
    assert r["suggestion"]["manufacturer"]["name"] == "Citadel"


def test_short_manufacturer_name_needs_whole_word(conn):
    r = suggest(conn, _reading(("OAK BROWN", 50)))
    assert r["suggestion"]["manufacturer"] is None  # "AK" inside "OAK" is not AK
    r = suggest(conn, _reading(("AK", 20), ("Pure White", 50)))
    assert r["suggestion"]["manufacturer"]["name"] == "AK"


def test_matches_existing_paint_even_with_ocr_typos(client, conn):
    p = client.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "code": "24-14"}).json()
    other = client.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Kimera"}).json()
    r = suggest(conn, _reading(("CITADEL", 30), ("NULN 0IL", 70)))
    assert r["matches"][0]["paint"]["id"] == p["id"]
    assert r["matches"][0]["by"] == "text"
    # same name from another manufacturer is ranked lower or dropped
    rest = [m for m in r["matches"] if m["paint"]["id"] == other["id"]]
    assert not rest or rest[0]["score"] < r["matches"][0]["score"]

    # without a manufacturer on the photo both are offered
    r = suggest(conn, _reading(("NULN OIL", 70)))
    assert {m["paint"]["id"] for m in r["matches"]} == {p["id"], other["id"]}


def test_barcode_finds_known_paint_and_offers_new_one(client, conn):
    p = client.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "barcode": "5011921027835"}).json()
    r = suggest(conn, _reading(("something", 20), barcodes=["5011921027835"]))
    assert r["matches"][0] == {"paint": r["matches"][0]["paint"], "score": 100, "by": "barcode"}
    assert r["matches"][0]["paint"]["id"] == p["id"]
    assert r["suggestion"]["barcode"] is None  # already known

    r = suggest(conn, _reading(barcodes=["4006381333931"]))
    assert r["suggestion"]["barcode"] == "4006381333931"  # new: will be saved with the paint


def test_barcode_is_unique(client):
    client.post("/api/paints", json={"name": "A", "manufacturer": "AK", "barcode": "123"})
    r = client.post("/api/paints", json={"name": "B", "manufacturer": "AK", "barcode": "123"})
    assert r.status_code == 409


# ---------- real images: OCR + barcode reading ----------

def _font(size):
    from PIL import ImageFont

    for path in glob.glob("/usr/share/fonts/**/DejaVuSans-Bold.ttf", recursive=True) + glob.glob(
        "C:/Windows/Fonts/arialbd.ttf"
    ):
        return ImageFont.truetype(path, size)
    pytest.skip("no TrueType font to draw a test label")


def _label(lines, barcode=None, rotate=0):
    """A paint label photo: text lines and optionally a real EAN-13 barcode."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1000, 900), (238, 236, 230))
    d = ImageDraw.Draw(img)
    y = 50
    for text, size in lines:
        d.text((70, y), text, font=_font(size), fill=(25, 25, 25))
        y += int(size * 1.6)
    if barcode:
        import zxingcpp

        code = zxingcpp.create_barcode(barcode, zxingcpp.BarcodeFormat.EAN13)
        bc = Image.fromarray(__import__("numpy").asarray(zxingcpp.write_barcode_to_image(code, scale=4)))
        img.paste(bc.convert("RGB").resize((bc.width, 160)), (70, y + 10))
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor=(238, 236, 230))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


needs_ocr = pytest.mark.skipif(not recognition.availability()["ocr"], reason="OCR packages not installed")
needs_barcode = pytest.mark.skipif(not recognition.availability()["barcode"], reason="zxing-cpp not installed")


@needs_ocr
@needs_barcode
def test_photo_endpoint_end_to_end(client):
    photo = _label([("CITADEL", 40), ("NULN OIL", 90), ("SHADE  24-14", 40)], barcode="5011921027835")
    r = client.post("/api/recognize", content=photo, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200, r.text
    data = r.json()
    s = data["suggestion"]
    assert s["manufacturer"]["name"] == "Citadel"
    assert s["name"] == "Nuln Oil"
    assert s["code"] == "24-14"
    assert s["barcode"] == "5011921027835"
    assert _tag_id(client, "Проливка") in s["tag_ids"]

    # save it with the barcode, then the same photo finds it straight away
    body = {"name": s["name"], "manufacturer_id": s["manufacturer"]["id"], "code": s["code"], "barcode": s["barcode"]}
    paint = client.post("/api/paints", json=body).json()
    again = client.post("/api/recognize", content=photo, headers={"Content-Type": "image/jpeg"}).json()
    assert again["matches"][0]["paint"]["id"] == paint["id"]
    assert again["matches"][0]["by"] == "barcode"


@needs_ocr
def test_photo_rotated_by_exif_like_a_phone(client):
    from PIL import Image

    raw = _label([("TAMIYA", 40), ("FLAT BLACK", 80), ("XF-1", 50)])
    img = Image.open(io.BytesIO(raw)).rotate(90, expand=True)  # stored sideways...
    exif = Image.Exif()
    exif[0x0112] = 8  # ...with "rotate 90° back" in EXIF, as phones do
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    s = client.post("/api/recognize", content=buf.getvalue(), headers={"Content-Type": "image/jpeg"}).json()["suggestion"]
    assert s["manufacturer"]["name"] == "Tamiya"
    assert s["code"] == "XF-1"
    assert s["name"] == "Flat Black"


def test_recognize_rejects_bad_input(client):
    if not recognition.availability()["available"]:
        assert client.post("/api/recognize", content=b"x").status_code == 503
        return
    assert client.post("/api/recognize", content=b"").status_code == 422
    assert client.post("/api/recognize", content=b"not an image", headers={"Content-Type": "image/jpeg"}).status_code == 422


def test_status_endpoint(client):
    data = client.get("/api/recognize").json()
    assert set(data) >= {"available", "ocr", "barcode", "missing", "install"}


def test_name_on_two_lines_is_joined(conn):
    r = suggest(conn, Reading(lines=[
        Line(text="TAMIYA", score=0.99, x=10, y=10, height=30),
        Line(text="FLAT", score=0.99, x=10, y=60, height=80),
        Line(text="BLACK", score=0.99, x=10, y=150, height=80),
        Line(text="XF-1", score=0.99, x=10, y=300, height=40),
    ]))
    assert r["suggestion"]["name"] == "Flat Black"
    assert r["suggestion"]["code"] == "XF-1"


def test_similar_but_different_paint_is_not_offered(client, conn):
    client.post("/api/paints", json={"name": "Matt Black", "manufacturer": "AK"})
    r = suggest(conn, _reading(("FLAT BLACK", 70)))
    assert r["matches"] == []


@needs_barcode
def test_barcode_only_mode_for_live_frames(client):
    photo = _label([("CITADEL", 40), ("NULN OIL", 90)], barcode="5011921027835")
    data = client.post("/api/recognize?barcode_only=true", content=photo, headers={"Content-Type": "image/jpeg"}).json()
    assert data["lines"] == []  # no text reading
    assert data["barcodes"][0]["text"] == "5011921027835"


def test_find_paint_by_barcode_in_list(client):
    p = client.post("/api/paints", json={"name": "Nuln Oil", "manufacturer": "Citadel", "barcode": "5011921027835"}).json()
    client.post("/api/paints", json={"name": "Other", "manufacturer": "Citadel"})
    found = client.get("/api/paints", params={"barcode": "5011921027835"}).json()
    assert [x["id"] for x in found] == [p["id"]]
    assert client.get("/api/paints", params={"barcode": "000"}).json() == []
