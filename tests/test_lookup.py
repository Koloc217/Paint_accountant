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
    # the piece that names the most wins, so a shop's shorthand doesn't become the name
    ("MW: Wash: Light Grey (35 ml.)", ["Vallejo"], None, "Light Grey"),
    ("(76515) - Av Model Wash 35ml - Light Grey Wash", ["Vallejo"], "76.515", "Light Grey"),
    # the range is named with the kinds, the colour is the piece without them
    ("77104 18 ml True Metallic Metal Light Paint, Ancient Copper",
     ["Vallejo"], "77.104", "Ancient Copper"),
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


# a real answer where UPCitemdb's own title is a listing for the whole range and the
# colour's name is only in the shops' titles under "offers"
BALTHASAR = {
    "ean": "5011921118816",
    "title": "Citadel Paint Pot - Base, Layer, Contrast & Shade - Games Workshop - Warhammer",
    "brand": "Games Workshop",
    "offers": [{"title": "GAW9918995013006 Balthasar Gold Base Paint", "price": 4.2},
               {"title": "Base - Balthasar Gold Citadel Paints", "price": 4.5}],
}


def test_the_name_is_taken_from_a_shops_title(client, monkeypatch):
    _answer(monkeypatch, data={"items": [BALTHASAR]})
    d = client.get("/api/lookup/upcitemdb", params={"barcode": "5011921118816"}).json()
    s = d["suggestion"]
    assert s["name"] == "Balthasar Gold" and s["manufacturer"]["name"] == "Citadel"
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert [tags[i] for i in s["tag_ids"]] == ["Основа"]  # not every kind the range listing names
    assert s["range_only"] is False
    assert d["item"]["title"] == "GAW9918995013006 Balthasar Gold Base Paint"  # the listing used


def test_a_range_listing_alone_fills_nothing(client, monkeypatch):
    """Base, Layer, Contrast & Shade in one title: no colour of its own, so no name and no tags."""
    _answer(monkeypatch, data={"items": [{k: v for k, v in BALTHASAR.items() if k != "offers"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "5011921118816"}).json()["suggestion"]
    assert s["name"] is None and s["tag_ids"] == [] and s["range_only"] is True
    assert s["manufacturer"]["name"] == "Citadel"  # the brand is still worth having


def test_only_what_we_read_is_cached(client, monkeypatch):
    heavy = dict(BALTHASAR, description="x" * 5000,
                 offers=[dict(o, link="http://shop/" + "y" * 500) for o in BALTHASAR["offers"]])
    _answer(monkeypatch, data={"items": [heavy]})
    client.get("/api/lookup/upcitemdb", params={"barcode": "5011921118816"})
    cached = lookup._cache["5011921118816"][1]["items"][0]
    assert "description" not in cached and cached["offers"] == [
        {"title": "GAW9918995013006 Balthasar Gold Base Paint"},
        {"title": "Base - Balthasar Gold Citadel Paints"}]


# a Citadel Technical pot: the main title sells the range together with brushes, and one
# shop's title says what the pot goes over ("For Base") rather than what it is
IRONCRUST = {
    "ean": "5011921121243",
    "title": "Citadel Paint - Technical, Dry and Paint Brushes - Games Workshop - Warhammer",
    "brand": "Games Workshop",
    "offers": [{"title": "GAW9918995604106 27-25 Martian Ironcrust Technical Paint"},
               {"title": "Citadel Technical Martian Ironcrust 24 Ml Pot Paint For Base Games Workshop"}],
}


def _suggest(client, item, barcode="5011921121243"):
    return client.get("/api/lookup/upcitemdb", params={"barcode": barcode}).json()["suggestion"]


def test_technical_pot_keeps_only_its_own_kind(client, monkeypatch):
    _answer(monkeypatch, data={"items": [IRONCRUST]})
    s = _suggest(client, IRONCRUST)
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert (s["name"], s["code"]) == ("Martian Ironcrust", "27-25")
    assert [tags[i] for i in s["tag_ids"]] == ["Спецэффект"]


def test_a_kind_the_paint_goes_over_is_not_its_tag(client, monkeypatch):
    """"…Technical Martian Ironcrust … Paint For Base": Base is what it covers, not what it is."""
    _answer(monkeypatch, data={"items": [{"title": IRONCRUST["offers"][1]["title"], "brand": "Games Workshop"}]})
    s = _suggest(client, None)
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert s["name"] == "Martian Ironcrust" and [tags[i] for i in s["tag_ids"]] == ["Спецэффект"]


def test_a_title_selling_brushes_with_the_range_names_nothing(client, monkeypatch):
    """"Technical, Dry and Paint Brushes" is a shelf, not a pot: no name, no tags."""
    _answer(monkeypatch, data={"items": [{k: v for k, v in IRONCRUST.items() if k != "offers"}]})
    s = _suggest(client, None)
    assert s["name"] is None and s["tag_ids"] == [] and s["range_only"] is True


def test_two_kinds_that_are_both_the_paints_own_are_kept(client, monkeypatch):
    """Not every second kind is a cross-reference: an airbrush metallic really is both."""
    _answer(monkeypatch, data={"items": [{"title": "Game Air Metallic Gold 17ml", "brand": "Vallejo"}]})
    s = _suggest(client, None, "8429551727747")
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert sorted(tags[i] for i in s["tag_ids"]) == ["Для аэрографа", "Металлик"]


# a Vallejo wash: its own title is all shorthand, and the shops write the code without
# the dot Vallejo prints ("76515", "val76515", "VJP76515") — the same paint as 76.515
LIGHT_GREY = {
    "ean": "8429551765152",
    "title": "MW: Wash: Light Grey (35 ml.)",
    "brand": "Acrylicos Vallejo",
    "offers": [{"title": "(val76515) - Av Model Wash 35ml - Light Grey Wash"},
               {"title": "Vallejo Light Grey Wash, 35ml"},
               {"title": "76515 Model Wash Light Grey, 35 ml"},
               {"title": "Vallejo Light Grey Wash, 35ml VJP76515"}],
}


def test_vallejo_wash_and_its_dotless_code(client, monkeypatch):
    _answer(monkeypatch, data={"items": [LIGHT_GREY]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "8429551765152"}).json()["suggestion"]
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert s["name"] == "Light Grey" and s["manufacturer"]["name"] == "Vallejo"
    assert s["code"] == "76.515"  # written as Vallejo prints it, so the label matches
    assert [tags[i] for i in s["tag_ids"]] == ["Проливка"]


def test_a_bare_five_digit_number_is_a_code_only_for_vallejo(client, monkeypatch):
    """Vallejo's dotless code is read because the brand says Vallejo; for others it proves nothing."""
    _answer(monkeypatch, data={"items": [{"title": "Pro Acryl 76515 Bold Titanium White 22ml",
                                          "brand": "Monument Hobbies"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "0850025932011"}).json()["suggestion"]
    assert s["code"] is None and s["manufacturer"] is None
    assert s["new_manufacturer"] == "Monument Hobbies"


def test_entities_are_decoded_and_the_colour_beats_the_range(client, monkeypatch):
    """A shop writes the comma as "&#44;": undecoded it neither shows nor separates."""
    _answer(monkeypatch, data={"items": [{
        "title": "VJP77104 18 ml True Metallic Metal Light Paint&#44; Ancient Copper",
        "brand": "Vallejo"}]})
    d = client.get("/api/lookup/upcitemdb", params={"barcode": "8429551771047"}).json()
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert "&#44;" not in d["item"]["title"] and "Paint, Ancient Copper" in d["item"]["title"]
    s = d["suggestion"]
    assert (s["name"], s["code"]) == ("Ancient Copper", "77.104")
    assert [tags[i] for i in s["tag_ids"]] == ["Металлик"]


@pytest.mark.parametrize("title, brand, name, code, tag", [
    ("Mr Hobby C137 Aqueous Hobby Color Tire Black 10ml", "GSI Creos", "Tire Black", "C137", None),
    # "Mr. Color" spelled without the dot still has to go, and lacquer means alcohol-thinned
    ("Mr. Color C1 White Gloss Lacquer Paint 10ml", "Mr. Hobby", "White Gloss", "C1", "Спиртовая"),
    ("GSI Creos Mr Metal Color MC211 Chrome Silver", "GSI Creos", "Chrome Silver", "MC211", "Металлик"),
    ("Mr.Hobby H12 Flat Black Aqueous 10ml", "Mr Hobby", "Flat Black", "H12", None),
    # another maker's lacquer paint is tagged too, and keeps its own kind of code
    ("Tamiya LP-5 Semi Gloss Black Lacquer Paint 10ml", "Tamiya", "Semi Gloss Black", "LP-5", "Спиртовая"),
])
def test_mr_hobby_and_alcohol_paints(client, monkeypatch, title, brand, name, code, tag):
    _answer(monkeypatch, data={"items": [{"title": title, "brand": brand}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "4973028737301"}).json()["suggestion"]
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert (s["name"], s["code"]) == (name, code)
    assert s["manufacturer"]["name"] == ("Tamiya" if brand == "Tamiya" else "Mr. Hobby")
    assert [tags[i] for i in s["tag_ids"]] == ([tag] if tag else [])


def test_a_letter_and_digits_are_a_code_only_for_mr_hobby(client, monkeypatch):
    """"C12" on its own says nothing: it is read only once the paint is known to be theirs."""
    _answer(monkeypatch, data={"items": [{"title": "Pro Acryl C12 Bold Titanium White", "brand": "Monument Hobbies"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "0850025932011"}).json()["suggestion"]
    assert s["code"] is None and s["manufacturer"] is None


# AK's own title sells the whole General Series ("236 colour singles"), and the shop's
# title has the pot: its code, its colour, and the shop's own spelling of it
BRITISH_KHAKI = {
    "ean": "8435568303607",
    "title": "AK Interactive: GENERAL Series Paints ? Acrylic Color Model Paint - 17ml Singles",
    "brand": "AK Interactive",
    "offers": [{"title": "Ak Interactive Ak11127 British Kkaki [3rd Gen] (17ml Bottle) Acrylic Paint Brown"}],
}


def test_ak_pot_from_the_shops_title(client, monkeypatch):
    _answer(monkeypatch, data={"items": [BRITISH_KHAKI]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "8435568303607"}).json()["suggestion"]
    assert (s["name"], s["code"]) == ("British Kkaki", "AK11127")  # the shop's typo is theirs to keep
    assert s["manufacturer"]["name"] == "AK" and s["range_only"] is False


def test_a_listing_of_a_whole_series_names_nothing(client, monkeypatch):
    """"GENERAL Series … Singles" is a shelf: the words for one say so even with no kinds named."""
    _answer(monkeypatch, data={"items": [{k: v for k, v in BRITISH_KHAKI.items() if k != "offers"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "8435568303607"}).json()["suggestion"]
    assert s["name"] is None and s["range_only"] is True
    assert s["manufacturer"]["name"] == "AK"


def test_the_same_barcode_under_another_listing_of_the_range(client, monkeypatch):
    """UPCitemdb changes its mind about a title: the same EAN, now "3Gen … Full Range"."""
    _answer(monkeypatch, data={"items": [{
        "title": "AK Interactive 3Gen General Series Full Range Acrylic Paint 17ml",
        "brand": "AK Interactive"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "8435568303607"}).json()["suggestion"]
    assert s["name"] is None and s["range_only"] is True and s["manufacturer"]["name"] == "AK"


@pytest.mark.parametrize("title, name, code", [
    ("AK Interactive Real Colors RC004 Olive Drab 10ml", "Olive Drab", "RC004"),
    # however a shop writes the generation, it is not part of the colour's name
    ("AK Interactive AK11021 Satin Black 3rd Generation Acrylic 17ml", "Satin Black", "AK11021"),
    ("AK 3rd Gen Acrylic AK11127 British Khaki", "British Khaki", "AK11127"),
    ("Real Colors RC-026 Dark Green 10ml", "Dark Green", "RC026"),
    ("AK Interactive Xtreme Metal AK479 Aluminium 30ml", "Aluminium", "AK479"),
])
def test_ak_lines_and_their_codes(client, monkeypatch, title, name, code):
    _answer(monkeypatch, data={"items": [{"title": title, "brand": "AK Interactive"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "8435568303607"}).json()["suggestion"]
    assert (s["name"], s["code"], s["manufacturer"]["name"]) == (name, code, "AK")


def test_the_raw_answer_is_kept_for_looking_at(client, monkeypatch):
    """A pot that comes out wrong: what UPCitemdb really sent, without spending a request."""
    assert client.get("/api/lookup/upcitemdb/raw").status_code == 404
    answer = {"code": "OK", "items": [dict(BRITISH_KHAKI, description="as it came")]}
    _answer(monkeypatch, data=answer)
    client.get("/api/lookup/upcitemdb", params={"barcode": "8435568303607"})
    raw = client.get("/api/lookup/upcitemdb/raw", params={"barcode": "8435568303607"}).json()
    assert raw["barcode"] == "8435568303607" and raw["status"] == 200
    assert raw["answer"] == answer  # whole and unread, offers and all

    # another barcode's answer doesn't take its place: pressing the button again is
    # answered from the day's memory, and must still show this pot's answer
    _answer(monkeypatch, data={"code": "OK", "items": [MEPHISTON]})
    client.get("/api/lookup/upcitemdb", params={"barcode": "5011921026203"})
    client.get("/api/lookup/upcitemdb", params={"barcode": "8435568303607"})  # from memory, no request
    again = client.get("/api/lookup/upcitemdb/raw", params={"barcode": "8435568303607"}).json()
    assert again == raw
    assert client.get("/api/lookup/upcitemdb/raw").json()["barcode"] == "5011921026203"  # the newest
    assert client.get("/api/lookup/upcitemdb/raw", params={"barcode": "4006381333931"}).status_code == 404


def test_xpress_color_is_a_contrast_paint(client, monkeypatch):
    """Vallejo's Xpress Color is the same kind of paint as Citadel's Contrast."""
    _answer(monkeypatch, data={"items": [
        {"title": "VJP72406 18 ml Game Color Xpress Color Plasma Red Paint", "brand": "Vallejo"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "8429551724067"}).json()["suggestion"]
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert (s["name"], s["code"]) == ("Plasma Red", "72.406")
    assert [tags[i] for i in s["tag_ids"]] == ["Контраст"]


def test_a_brand_not_in_the_catalog_is_still_cleaned_off_the_name(client, monkeypatch):
    """"The Army Painter" is written "Army Painter" as often as not, and isn't a maker here yet."""
    _answer(monkeypatch, data={"items": [
        {"title": "Army Painter Speedpaint 2.0 Zealot Yellow", "brand": "The Army Painter"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "5713799110403"}).json()["suggestion"]
    tags = {t["id"]: t["name"] for t in client.get("/api/tags").json()}
    assert s["name"] == "Zealot Yellow" and [tags[i] for i in s["tag_ids"]] == ["Контраст"]
    # offered under the name we know them by, so the next pot of theirs finds this one
    assert s["manufacturer"] is None and s["new_manufacturer"] == "Army Painter"


def test_a_code_is_kept_when_the_brand_and_the_pattern_agree(client, monkeypatch):
    _answer(monkeypatch, data={"items": [
        {"title": "The Army Painter Warpaints Fanatic WP3001 Matt Black", "brand": "The Army Painter"}]})
    s = client.get("/api/lookup/upcitemdb", params={"barcode": "5713799110403"}).json()["suggestion"]
    assert s["code"] == "WP3001" and s["new_manufacturer"] == "Army Painter"


# an answer of the free plan, word for word: one item, no offers, and brand, model and
# colour all blank, though the site shows three titles and a brand for this barcode
THIN_ANSWER = {
    "code": "OK", "total": 1, "offset": 0,
    "items": [{"ean": "8435568303607",
               "title": "AK Interactive 3Gen General Series Full Range Acrylic Paint 17ml",
               "description": "", "elid": "188561759753", "brand": "", "model": "", "color": "",
               "size": "", "dimension": "", "weight": "",
               "category": "Toys & Games > Toys > Play Vehicle Accessories",
               "lowest_recorded_price": 3.99, "highest_recorded_price": 8.52,
               "images": [], "offers": []}],
}


def test_an_answer_with_nothing_but_a_range_title(client, monkeypatch):
    """Nothing can be made of it but the manufacturer — which the barcode itself gives."""
    _answer(monkeypatch, data=THIN_ANSWER)
    d = client.get("/api/lookup/upcitemdb", params={"barcode": "8435568303607"}).json()
    assert d["found"] is True
    s = d["suggestion"]
    assert s["name"] is None and s["code"] is None and s["tag_ids"] == [] and s["range_only"] is True
    assert s["manufacturer"]["name"] == "AK"  # from 8435568, since the brand came blank
    assert client.get("/api/lookup/upcitemdb/raw", params={"barcode": "8435568303607"}).json()["answer"] == THIN_ANSWER


def test_several_listings_the_one_with_a_name_wins(client, monkeypatch):
    nothing = {"title": "Citadel Base Paint Pot - Games Workshop - All Colors!", "brand": "Citadel Miniatures"}
    _answer(monkeypatch, data={"items": [nothing, MEPHISTON]})
    d = client.get("/api/lookup/upcitemdb", params={"barcode": "5011921026203"}).json()
    assert d["suggestion"]["name"] == "Mephiston Red"
    assert d["item"]["title"] == MEPHISTON["title"]  # the listing that was used is the one shown
    assert d["suggestion"]["range_only"] is False


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
