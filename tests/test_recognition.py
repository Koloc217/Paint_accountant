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
from paint_accountant.recognition import Line, Reading, clean_name, find_code, suggest

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


def test_thinner_label_suggests_technical_tag(client, conn):
    r = suggest(conn, _reading(("Vallejo", 20), ("Airbrush Thinner", 50), ("71.161", 20)))
    assert _tag_id(client, "Техническая") in r["suggestion"]["tag_ids"]


@pytest.mark.parametrize(
    "label, tag",
    [
        ("Airbrush Cleaner", "Техническая"),
        ("Brush Cleaner", "Техническая"),
        ("Plastic Putty", "Техническая"),
        ("Dipping Ink", "Чернила"),
        ("Quickshade Dip", "Чернила"),
        ("Inks", "Чернила"),
        ("Weathering Pigments", "Спецэффект"),
        ("Rust Pigment", "Спецэффект"),
        ("Astrogranite Texture", "Спецэффект"),
    ],
)
def test_label_words_suggest_tags(client, conn, label, tag):
    r = suggest(conn, _reading((label, 50)))
    assert _tag_id(client, tag) in r["suggestion"]["tag_ids"]


def test_scale75_on_label(conn):
    r = suggest(conn, _reading(("SCALE 75", 20), ("Ink Black", 50)))
    assert r["suggestion"]["manufacturer"]["name"] == "Scale75"


def _real(lines):
    """Lines as OCR read them from a real photo: (text, y, height) or (text, x, y, height)."""
    return Reading(lines=[Line(text=t[0], score=0.95, x=50 if len(t) == 3 else t[1], y=t[-2], height=t[-1])
                          for t in lines])


def test_green_stuff_world_stacked_brand_and_two_line_name(client, conn):
    # read from a real photo of GSW "Paint Revitalizer"; note "STUF" — an OCR slip
    r = suggest(conn, _real([
        ("GREEN", 341, 118), ("STUF", 415, 108), ("WORLD", 489, 93),
        ("PAINT", 667, 94), ("REVITALIZER", 754, 96), ("60ml (2.02 ffl.oz.)", 1016, 79),
    ]))
    s = r["suggestion"]
    assert s["manufacturer"]["name"] == "Green Stuff World"
    assert s["name"] == "Paint Revitalizer"
    assert _tag_id(client, "Техническая") in s["tag_ids"]
    kinds = {ln["text"]: ln["kind"] for ln in r["lines"]}
    assert kinds["GREEN"] == kinds["STUF"] == kinds["WORLD"] == "manufacturer"
    assert kinds["60ml (2.02 ffl.oz.)"] == "noise"


def test_gsw_reference_code(conn):
    r = suggest(conn, _real([("GREEN STUFF WORLD", 10, 40), ("Paint Revitalizer", 80, 90),
                             ("1750 - PAINT REVITALIZER", 200, 20)]))
    assert r["suggestion"]["code"] == "1750"
    assert r["suggestion"]["name"] == "Paint Revitalizer"


def test_brand_and_name_on_neighbouring_lines_stay_apart(conn):
    r = suggest(conn, _real([("CITADEL", 10, 30), ("NULN OIL", 45, 70)]))
    assert r["suggestion"]["manufacturer"]["name"] == "Citadel"
    assert r["suggestion"]["name"] == "Nuln Oil"


# read from a real photo of Vallejo Model Color 70.863 Gunmetal Grey: the logo font of
# "vallejo" comes out as "vaeo", the AV logo as "NV", the name is cut by the bottle's curve
VALLEJO_MODEL_COLOR = [
    ("100 70.863", 297, 420, 67), ("GUNMETA", 456, 425, 55), ("MODEL", 306, 486, 87),
    ("COLOR", 309, 555, 78), ("acrylic-colors", 313, 617, 48), ("NV", 370, 697, 93),
    ("vaeo", 371, 775, 46),
]


def test_vallejo_known_by_product_line_and_code(conn):
    r = suggest(conn, _real(VALLEJO_MODEL_COLOR))
    s = r["suggestion"]
    assert s["manufacturer"]["name"] == "Vallejo"
    assert s["code"] == "70.863"
    assert s["name"] == "Gunmeta"  # the name next to the code, not the big logo
    kinds = {ln["text"]: ln["kind"] for ln in r["lines"]}
    assert kinds["MODEL"] == kinds["COLOR"] == "manufacturer"
    assert kinds["NV"] == "noise"


def test_vallejo_by_code_alone(conn):
    r = suggest(conn, _real([("70.951", 10, 400, 40), ("WHITE", 200, 400, 40), ("vaeo", 50, 600, 30)]))
    assert r["suggestion"]["manufacturer"]["name"] == "Vallejo"
    assert r["suggestion"]["name"] == "White"


def test_weak_code_does_not_pick_manufacturer(conn):
    # "24-14" looks like a Citadel code but is too common to decide the brand on its own
    r = suggest(conn, _real([("NULN OIL", 10, 70), ("24-14", 100, 20)]))
    assert r["suggestion"]["manufacturer"] is None


def test_known_paint_found_from_cut_name(client, conn):
    client.post("/api/paints", json={"name": "Gunmetal Grey", "manufacturer": "Vallejo", "code": "70.863"})
    r = suggest(conn, _real(VALLEJO_MODEL_COLOR))
    assert r["matches"] and r["matches"][0]["paint"]["name"] == "Gunmetal Grey"


# read from a real photo of a Green Stuff World chrome paint: OCR lost "GREEN"
GSW_CHROME = [("STUFF", 100, 40), ("WORLD", 145, 40), ("CHROME", 300, 60), ("METAL", 365, 60),
              ("COPPER", 430, 60), ("CHROME METAL", 520, 25), ("17mle(0.57f1.oz.)", 600, 20)]
GSW_BARCODE = {"text": "8435646510545", "format": "EAN13"}


def _with_barcode(lines, *codes):
    r = _real(lines)
    r.barcodes = list(codes)
    return r


def _add(client, **paint):
    r = client.post("/api/paints", json={"quantity": 1, "allow_duplicate": True} | paint)
    assert r.status_code == 201, r.text
    return r.json()


def test_two_words_of_a_long_brand_are_enough(conn):
    r = suggest(conn, _real(GSW_CHROME))
    assert r["suggestion"]["manufacturer"]["name"] == "Green Stuff World"
    assert r["suggestion"]["name"] != "Stuff"
    kinds = {ln["text"]: ln["kind"] for ln in r["lines"]}
    assert kinds["STUFF"] == kinds["WORLD"] == "manufacturer"
    assert kinds["17mle(0.57f1.oz.)"] == "noise"  # volume, even misread


def test_other_brand_with_the_same_word_is_not_a_match(client, conn):
    # the screenshot case: Vallejo "Copper" offered at 100% for a GSW pot with a new barcode
    _add(client, name="Copper", manufacturer="Vallejo", code="77.710")
    r = suggest(conn, _with_barcode(GSW_CHROME, GSW_BARCODE))
    assert r["suggestion"]["manufacturer"]["name"] == "Green Stuff World"
    assert r["matches"] == []


def test_brand_from_barcode_when_the_text_has_none(client, conn):
    r = suggest(conn, _with_barcode([("COPPER", 100, 60)], GSW_BARCODE))
    assert r["suggestion"]["manufacturer"]["name"] == "Green Stuff World"
    r = suggest(conn, _with_barcode([("NULN OIL", 100, 60)], {"text": "5011921026845", "format": "EAN13"}))
    assert r["suggestion"]["manufacturer"]["name"] == "Citadel"


def test_brand_from_barcodes_already_in_the_catalog(client, conn):
    # a brand without a built-in prefix: learned from a saved paint
    _add(client, name="Rosso", manufacturer="Kimera", barcode="8051234000017")
    r = suggest(conn, _with_barcode([("BLU", 100, 60)], {"text": "8051234000024", "format": "EAN13"}))
    assert r["suggestion"]["manufacturer"]["name"] == "Kimera"


def test_paint_with_its_own_different_barcode_is_another_paint(client, conn):
    _add(client, name="Chrome Copper", manufacturer="Green Stuff World", barcode="8435646510999")
    r = suggest(conn, _with_barcode(GSW_CHROME, GSW_BARCODE))
    assert r["matches"] == []
    # without a barcode on the photo it's still a fair guess
    r = suggest(conn, _real(GSW_CHROME))
    assert [m["paint"]["name"] for m in r["matches"]] == ["Chrome Copper"]


def test_same_brand_paint_without_barcode_still_matches(client, conn):
    _add(client, name="Chrome Copper", manufacturer="Green Stuff World")
    r = suggest(conn, _with_barcode(GSW_CHROME, GSW_BARCODE))
    assert [m["paint"]["name"] for m in r["matches"]] == ["Chrome Copper"]


def test_one_common_word_of_unknown_brand_is_only_a_hint(client, conn):
    _add(client, name="Copper", manufacturer="Vallejo")
    r = suggest(conn, _real([("COPPER", 100, 60), ("acrylic", 200, 20)]))
    assert r["suggestion"]["manufacturer"] is None
    assert r["matches"] and r["matches"][0]["score"] < 90


def test_storage_qr_in_the_frame_is_not_a_barcode(conn):
    r = suggest(conn, _with_barcode([("NULN OIL", 100, 60)], {"text": "http://192.168.1.94:8642/storage/2", "format": "QRCode"}))
    assert r["suggestion"]["barcode"] is None and r["barcodes"] == []


# Tamiya Panel Line Accent Color, as RapidOCR read a real photo: the bold title loses its
# spaces, the brand is only the star logo, the number is printed as "Item 87131".
TAMIYA_PANEL_LINE = [
    ("PANELLINE", 140, 395, 78), ("ACCENTCOLOR", 132, 480, 80), ("(BLACK)", 290, 565, 41),
    ("DANGER: EXTREMELY FLAMMABLE LIQUID AND VAPOR", 150, 610, 22),
    ("Read carefully additional warings on sach side panal.", 170, 640, 17),
    ("Net 40ml Item 87131", 260, 665, 20),
]


def test_tamiya_by_item_number_with_glued_title(conn):
    s = suggest(conn, _real(TAMIYA_PANEL_LINE))["suggestion"]
    assert s["manufacturer"]["name"] == "Tamiya"
    assert s["code"] == "87131"
    assert s["name"] == "Panel Line Accent Color (Black)"
    wash = conn.execute("SELECT id FROM tag WHERE name = 'Проливка'").fetchone()[0]
    assert wash in s["tag_ids"]


def test_tamiya_title_read_with_spaces(conn):
    lines = [("PANEL LINE", 140, 395, 78), ("ACCENT COLOR", 132, 480, 80), ("(BLACK)", 290, 565, 41),
             ("Net 40ml Item 87131", 260, 665, 20)]
    assert suggest(conn, _real(lines))["suggestion"]["name"] == "Panel Line Accent Color (Black)"


@pytest.mark.parametrize("read, expected", [
    ('NULN OIL"', "Nuln Oil"),          # ™ read as a quote
    ("Nuln Oil™", "Nuln Oil"),
    ("AGRAX EARTHSHADE TM", "Agrax Earthshade"),
    ("MEPHISTON RED'", "Mephiston Red"),
    ("Ork's Flesh", "Ork's Flesh"),     # an apostrophe inside a word stays
    ("NIGHTSHADE", "Nightshade"),       # a real one-word name is not split
    ("REVITALIZER", "Revitalizer"),
])
def test_clean_name(read, expected):
    assert clean_name(read) == expected


def test_citadel_trademark_quote_is_dropped(conn):
    r = suggest(conn, _real([("CITADEL", 100, 40, 30), ('NULN OIL"', 60, 120, 70)]))
    assert r["suggestion"]["manufacturer"]["name"] == "Citadel"
    assert r["suggestion"]["name"] == "Nuln Oil"
