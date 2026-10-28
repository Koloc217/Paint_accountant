"""Photo recognition: barcodes and label text, turned into suggestions for the paint form.

Optional module. It needs the packages from requirements-photo.txt
(rapidocr + onnxruntime for text, zxing-cpp for barcodes, rapidfuzz, Pillow).
Without them everything else in the app works and /api/recognize reports
what is missing. Everything runs locally; the OCR models ship inside the
rapidocr package, nothing is downloaded at runtime.

The text model reads Latin letters and digits well; Cyrillic is not recognized.
"""

from __future__ import annotations

import io
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import repository as repo

MAX_SIDE = 1600  # px; phone photos are scaled down to this before reading
MATCH_THRESHOLD = 82  # below this, "Matt Black" vs "Flat Black" would count as the same paint


# ======================= availability =======================

def availability() -> dict[str, Any]:
    missing = []
    for module, package in (
        ("PIL", "pillow"),
        ("rapidfuzz", "rapidfuzz"),
        ("rapidocr", "rapidocr"),
        ("onnxruntime", "onnxruntime"),
        ("zxingcpp", "zxing-cpp"),
    ):
        try:
            __import__(module)
        except Exception:  # ImportError, or a broken native library
            missing.append(package)
    base_ok = "pillow" not in missing and "rapidfuzz" not in missing
    ocr = base_ok and "rapidocr" not in missing and "onnxruntime" not in missing
    barcode = base_ok and "zxing-cpp" not in missing
    return {
        "available": ocr or barcode,
        "ocr": ocr,
        "barcode": barcode,
        "missing": missing,
        "install": "pip install -r requirements-photo.txt" if missing else None,
    }


class NotAvailable(Exception):
    pass


# ======================= reading =======================

@dataclass
class Line:
    text: str
    score: float
    x: float
    y: float
    height: float
    kind: str = "other"  # manufacturer | code | tag | name | other | noise


@dataclass
class Reading:
    lines: list[Line] = field(default_factory=list)
    barcodes: list[dict] = field(default_factory=list)
    width: int = 0
    height: int = 0
    seconds: float = 0.0


_engine = None
_engine_lock = threading.Lock()


def _ocr_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            from rapidocr import RapidOCR

            _engine = RapidOCR()
        return _engine


def warm_up() -> None:
    """Load the OCR model in the background so the first photo isn't slow."""
    if availability()["ocr"]:
        threading.Thread(target=lambda: _safe(_ocr_engine), daemon=True).start()


def _safe(fn):
    try:
        fn()
    except Exception:  # warm-up is best effort
        logging.getLogger(__name__).exception("OCR warm-up failed")


def load_image(data: bytes):
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # phone photos carry rotation in EXIF
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img


def read_image(data: bytes, ocr: bool = True) -> Reading:
    """ocr=False reads barcodes only — fast enough for frames of a live camera."""
    info = availability()
    if not info["available"]:
        raise NotAvailable(info["install"])
    started = time.perf_counter()
    try:
        img = load_image(data)
    except Exception as exc:
        raise ValueError("not an image or unsupported format") from exc

    reading = Reading(width=img.width, height=img.height)
    if info["barcode"]:
        reading.barcodes = _read_barcodes(img)
    if ocr and info["ocr"]:
        reading.lines = _read_text(img)
    reading.seconds = round(time.perf_counter() - started, 2)
    return reading


def _read_barcodes(img) -> list[dict]:
    import zxingcpp

    found = []
    seen = set()
    for b in zxingcpp.read_barcodes(img):
        text = (b.text or "").strip()
        if text and text not in seen:
            seen.add(text)
            found.append({"text": text, "format": str(b.format).split(".")[-1]})
    return found


def _read_text(img) -> list[Line]:
    import numpy as np

    result = _ocr_engine()(np.asarray(img))
    if result is None or result.txts is None:
        return []
    lines = []
    for text, score, box in zip(result.txts, result.scores, result.boxes):
        text = " ".join(str(text).split())
        if not text:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append(Line(text=text, score=float(score), x=min(xs), y=min(ys), height=max(ys) - min(ys)))
    lines.sort(key=lambda ln: (round(ln.y / 12), ln.x))  # reading order: top to bottom, left to right
    return lines


# ======================= understanding the text =======================

def norm(text: str) -> str:
    """Lowercase, letters and digits only, single spaces."""
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


# "60ml (2.02 fl.oz.)", also with OCR slips: "17mle(0.57f1.oz.)"
VOLUME = re.compile(r"\b\d+([.,]\d+)?\s?(ml|fl\.?\s?oz)|f[l1]\.?\s?oz\b", re.I)

# words printed on labels that are not part of a paint name (English and Spanish — GSW, Vallejo)
STOPWORDS = {
    "de", "pintura", "para", "con", "y",
    "ml", "acrylic", "acrylics", "paint", "paints", "colour", "color", "colours", "colors",
    "water", "based", "non", "toxic", "made", "in", "spain", "uk", "china", "for", "use",
    "only", "the", "and", "of", "new", "formula", "warning", "game", "model", "fl", "oz",
}

# other ways manufacturers appear on labels -> canonical name in the starter list
ALIASES = {
    "games workshop": "Citadel",
    "citadel colour": "Citadel",
    "ak interactive": "AK",
    "akinteractive": "AK",
    "kimera kolors": "Kimera",
    "gsw": "Green Stuff World",
    "green stuf world": "Green Stuff World",
    "greenstuffworld": "Green Stuff World",
    "the army painter": "Army Painter",
    "army painter": "Army Painter",
    "acrylicos vallejo": "Vallejo",
    # Vallejo product lines are printed bigger than the brand (its logo font often reads as "vaeo")
    "model color": "Vallejo",
    "model colour": "Vallejo",
    "model air": "Vallejo",
    "model wash": "Vallejo",
    "game color": "Vallejo",
    "game air": "Vallejo",
    "game ink": "Vallejo",
    "mecha color": "Vallejo",
    "xpress color": "Vallejo",
    "metal color": "Vallejo",
    "panzer aces": "Vallejo",
    "premium color": "Vallejo",
    "scale 75": "Scale75",
    "scalecolor": "Scale75",
}

# product codes: (manufacturer it usually belongs to, pattern, formatter)
CODE_PATTERNS: list[tuple[str | None, re.Pattern, Any]] = [
    # Vallejo: 70.xxx Model Color, 71.xxx Model Air, 72.xxx Game Color, 77.xxx Metal Color, 26/28/62/69 etc.
    ("Vallejo", re.compile(r"\b([2-7]\d)\s?[.,·]\s?(\d{3})\b"), lambda m: f"{m[1]}.{m[2]}"),
    ("AK", re.compile(r"\bAK\s?-?\s?(\d{3,5})\b", re.I), lambda m: f"AK{m[1]}"),
    ("Army Painter", re.compile(r"\b(WP|CP|GM)\s?(\d{4})\b", re.I), lambda m: f"{m[1].upper()}{m[2]}"),
    ("Tamiya", re.compile(r"\b(XF|X|LP|TS|AS)\s?-\s?(\d{1,3})\b", re.I), lambda m: f"{m[1].upper()}-{m[2]}"),
    # Tamiya prints the catalogue number as "Item 87131" (also "Item No. 81731")
    ("Tamiya", re.compile(r"\bItem\s?(?:No\.?\s?)?(\d{5})\b", re.I), lambda m: m[1]),
    ("Citadel", re.compile(r"\b(\d{2})\s?-\s?(\d{2})\b"), lambda m: f"{m[1]}-{m[2]}"),
    # Green Stuff World: "1750 - PAINT REVITALIZER"
    ("Green Stuff World", re.compile(r"\b(\d{4,5})\s*[-–]\s*[A-Za-z]"), lambda m: m[1]),
]


# codes distinctive enough to tell the manufacturer when its name can't be read
TELLING_CODES = {"Vallejo", "AK", "Army Painter", "Tamiya"}


def find_code_with_maker(text: str, manufacturer: str | None = None) -> tuple[str | None, str | None]:
    """The product code on the label and the manufacturer whose pattern it matched."""
    ordered = sorted(CODE_PATTERNS, key=lambda c: c[0] != manufacturer) if manufacturer else CODE_PATTERNS
    for maker, pattern, fmt in ordered:
        m = pattern.search(text)
        if m:
            return fmt(m), maker
    return None, None


def find_code(text: str, manufacturer: str | None = None) -> str | None:
    return find_code_with_maker(text, manufacturer)[0]


# Trademark marks after a name: "™", "®", and the way OCR reads them — a stray quote or "TM".
TRADEMARK = re.compile(r"[™®©]|[\"'`´“”‘’]+(?=\s|$)|(?<=\s)TM(?=\s|$)")

# Words printed on labels that OCR often glues together when the font is bold and tight
# ("PANELLINE ACCENTCOLOR"). Kept short on purpose: a real one-word name ("Nightshade")
# must not be split, so only plain label words and basic colours are here.
GLUE_WORDS = {
    "panel", "line", "accent", "color", "colour", "model", "game", "air", "metal", "metallic",
    "black", "white", "red", "blue", "green", "yellow", "orange", "purple", "violet", "pink",
    "brown", "grey", "gray", "gold", "silver", "bronze", "copper", "steel", "chrome", "khaki",
    "olive", "flat", "matt", "matte", "gloss", "satin", "clear", "dark", "light", "deep", "pale",
    "wash", "shade", "ink", "primer", "varnish", "thinner", "effect", "enamel", "acrylic",
}


def _unglue(word: str, vocab: set[str]) -> str:
    """'PANELLINE' -> 'PANEL LINE' when the whole word splits into known label words."""
    low = word.lower()
    if len(low) < 7 or not low.isalpha() or low in vocab:
        return word
    best: list[list[int] | None] = [[]] + [None] * len(low)  # cut positions; fewest parts wins
    for end in range(3, len(low) + 1):
        for start in range(0, end - 2):
            if best[start] is not None and low[start:end] in vocab:
                cand = best[start] + [end]
                if best[end] is None or len(cand) < len(best[end]):
                    best[end] = cand
    cuts = best[len(low)]
    if not cuts or len(cuts) < 2:
        return word
    starts = [0] + cuts[:-1]
    return " ".join(word[a:b] for a, b in zip(starts, cuts))


def clean_name(text: str, vocab: set[str] = GLUE_WORDS) -> str:
    """Drop trademark marks, split glued label words, then make the case readable."""
    text = " ".join(TRADEMARK.sub(" ", text).split())
    text = " ".join(_unglue(w, vocab) for w in text.split())
    return _nice_name(text)


def _nice_name(text: str) -> str:
    """'NULN OIL' -> 'Nuln Oil'; mixed case is kept as printed."""
    if text.isupper():
        return " ".join(_cap(w) for w in text.split())
    return text


def _cap(word: str) -> str:
    """'BLACK' -> 'Black', '(BLACK)' -> '(Black)'; codes like '70.951' stay as they are."""
    core = word.strip("()[]")
    if core.isalpha():
        return word.replace(core, core.capitalize(), 1)
    return word


def _maker_score(text: str, key: str, by_alias: bool) -> float:
    from rapidfuzz import fuzz

    if by_alias:
        return 99
    if len(key) <= 3:
        return 100 if key in text.split() else 0  # short names like "AK": whole word only
    if len(text) >= len(key) - 1:
        return fuzz.partial_ratio(key, text)
    return fuzz.ratio(key, text)


def _match_manufacturer(lines: list[Line], makers: list[dict]) -> tuple[dict | None, list[Line]]:
    """Best manufacturer on the label and the line(s) it's printed on.

    A brand is often stacked over several lines ("GREEN" / "STUFF" / "WORLD"), so groups of
    2-3 consecutive lines are tried too — but only when no single line matches as well, and
    only if the group isn't much longer than the name (so "CITADEL" + "NULN OIL" stays apart).
    """
    by_name = {norm(m["name"]): m for m in makers}
    aliases = [(alias, by_name.get(norm(canonical))) for alias, canonical in ALIASES.items()]
    # two neighbouring words of a long name are enough: OCR dropped "GREEN", "STUFF WORLD" is left
    for key, m in by_name.items():
        words = key.split()
        if len(words) >= 3:
            aliases += [(p, m) for p in (" ".join(words[i:i + 2]) for i in range(len(words) - 1)) if len(p) >= 9]
    candidates: list[tuple[str, list[Line]]] = []
    for size in (1, 2, 3):
        for i in range(len(lines) - size + 1):
            group = lines[i:i + size]
            text = norm(" ".join(ln.text for ln in group))
            if text:
                candidates.append((text, group))

    best: tuple[float, dict | None, list[Line]] = (0, None, [])
    for text, group in candidates:
        squashed = text.replace(" ", "")
        options = [(m, alias, True) for alias, m in aliases
                   if m and (alias in text or alias.replace(" ", "") in squashed)]
        options += [(m, key, False) for key, m in by_name.items()]
        for m, key, by_alias in options:
            if len(group) > 1 and len(text) > len(key) * 1.4 + 3:
                continue
            score = _maker_score(text, key, by_alias)
            if score >= 88 and score > best[0]:
                best = (score, m, group)
    return best[1], best[2]


# EAN-13: the first digits are the company's number. Known ones; the catalog adds more (see below).
BARCODE_PREFIXES = {
    "8429551": "Vallejo",
    "8435646": "Green Stuff World",
    "5011921": "Citadel",
    "5713799": "Army Painter",
    "8435568": "AK",
    "4950344": "Tamiya",
}
PREFIX_LEN = 7


def _maker_by_barcode(conn: sqlite3.Connection, barcode: str, makers: list[dict]) -> dict | None:
    """Manufacturer whose paints in the catalog have barcodes from the same company — or a known prefix."""
    if not re.fullmatch(r"\d{12,13}", barcode or ""):
        return None
    prefix = barcode.zfill(13)[:PREFIX_LEN]
    ids = {r["manufacturer_id"] for r in conn.execute(
        "SELECT DISTINCT manufacturer_id FROM paint WHERE substr(printf('%013d', barcode), 1, ?) = ? "
        "AND barcode GLOB '[0-9]*'", (PREFIX_LEN, prefix))}
    if len(ids) == 1:
        return next((m for m in makers if m["id"] in ids), None)
    known = BARCODE_PREFIXES.get(prefix)
    return next((m for m in makers if known and norm(m["name"]) == norm(known)), None)


def _tag_ids(text: str, keywords: list[tuple[str, int]]) -> list[int]:
    words = set(norm(text).split())
    return sorted({tag_id for kw, tag_id in keywords if kw.lower() in words})


def suggest(conn: sqlite3.Connection, reading: Reading) -> dict[str, Any]:
    """Turn what was read into form suggestions and matches with paints already in the catalog."""
    from rapidfuzz import fuzz

    lines = reading.lines
    makers = repo.list_manufacturers(conn)
    keywords = [(r["keyword"], r["tag_id"]) for r in conn.execute("SELECT keyword, tag_id FROM tag_keyword")]
    keyword_set = {kw.lower() for kw, _ in keywords}
    full_text = " ".join(ln.text for ln in lines)

    # --- classify lines
    for ln in lines:
        if ln.score < 0.5 or len(norm(ln.text)) < 2 or not re.search(r"[A-Za-z]{2}|\d", ln.text):
            ln.kind = "noise"

    maker, maker_lines = _match_manufacturer([ln for ln in lines if ln.kind != "noise"], makers)
    for ln in maker_lines:
        ln.kind = "manufacturer"

    code, code_maker = find_code_with_maker(full_text, maker["name"] if maker else None)
    if maker is None and code_maker in TELLING_CODES:
        # "70.863" is Vallejo even when the logo is unreadable
        maker = next((m for m in makers if norm(m["name"]) == norm(code_maker)), None)
    if maker is None:
        # the barcode tells the company: 8435646… is Green Stuff World
        maker = next(filter(None, (_maker_by_barcode(conn, b["text"], makers) for b in reading.barcodes)), None)

    code_line = None
    for ln in lines:
        if ln.kind != "other":
            continue
        words = norm(ln.text).split()
        if code and find_code(ln.text) == code:
            code_line = ln
        if code and code_line is ln and sum(c.isalpha() for c in ln.text) < 3:
            ln.kind = "code"  # "70.863", "100 70.863"
        elif len(norm(ln.text).replace(" ", "")) <= 2:
            ln.kind = "noise"  # a logo read as letters: "AV" -> "NV"
        elif VOLUME.search(ln.text):
            ln.kind = "noise"  # "60ml (2.02 fl.oz.)"
        elif all(w in keyword_set or w in STOPWORDS or re.fullmatch(r"\d+(ml)?", w) for w in words):
            if any(w in keyword_set for w in words):
                ln.kind = "tag"
            else:
                # only generic words ("PAINT"): not a name on its own, but can be part of one
                ln.kind = "filler" if all(w.isalpha() for w in words) else "noise"

    # --- name: the biggest remaining text is usually the paint name
    # if every remaining line is made of tag words ("PAINT" / "REVITALIZER"), the biggest one is the name
    candidates = [ln for ln in lines if ln.kind == "other"] or [ln for ln in lines if ln.kind == "tag"]
    name_line = max(candidates, key=lambda ln: ln.height, default=None)
    if code_line is not None:
        # Vallejo and GSW print the name right after the code, on the same row: "70.863 GUNMETAL GREY"
        beside = [ln for ln in candidates if ln is not code_line and ln.x > code_line.x
                  and abs(ln.y - code_line.y) <= 0.5 * max(ln.height, code_line.height)]
        if beside:
            name_line = min(beside, key=lambda ln: ln.x)
    name_parts: list[Line] = []
    if name_line is not None:
        # a long name is often printed on two lines in the same size: "FLAT" / "BLACK",
        # "PAINT" / "REVITALIZER" — join neighbours above and below
        hh = name_line.height
        joinable = lambda ln: ln.kind in ("other", "filler", "tag") and abs(ln.height - hh) <= 0.25 * hh  # noqa: E731
        name_parts = [name_line]
        for ln in sorted((x for x in lines if x.y > name_line.y), key=lambda x: x.y):
            gap = ln.y - (name_parts[-1].y + name_parts[-1].height)
            if joinable(ln) and -0.2 * hh <= gap <= 0.9 * hh:
                name_parts.append(ln)
            else:
                break
        for ln in sorted((x for x in lines if x.y < name_line.y), key=lambda x: -x.y):
            gap = name_parts[0].y - (ln.y + ln.height)
            if joinable(ln) and -0.2 * hh <= gap <= 0.9 * hh:
                name_parts.insert(0, ln)
            else:
                break
        # a variant in brackets printed smaller right under the name: "ACCENT COLOR" / "(BLACK)"
        last = name_parts[-1]
        below = [ln for ln in lines if ln.y > last.y and ln.kind in ("other", "filler", "tag")
                 and re.fullmatch(r"\(\s*[A-Za-z][A-Za-z .'-]*\)", ln.text.strip())]
        if below:
            nxt = min(below, key=lambda ln: ln.y)
            if nxt.y - (last.y + last.height) <= 0.9 * hh:
                name_parts.append(nxt)
        for ln in name_parts:
            ln.kind = "name"
    vocab = GLUE_WORDS | {w for kw, _ in keywords for w in norm(kw).split() if len(w) >= 3}
    name = clean_name(" ".join(ln.text for ln in name_parts), vocab) if name_parts else None
    if name and code:
        name = " ".join(name.replace(code, "").split()) or name

    # --- barcodes and matches with paints already in the catalog
    matches: dict[int, dict] = {}
    barcodes = []
    for b in reading.barcodes:
        if re.match(r"https?://", b["text"], re.I):
            continue  # a storage QR label in the frame, not the pot's barcode
        paint = repo.find_paint_by_barcode(conn, b["text"])
        barcodes.append({**b, "paint_id": paint["id"] if paint else None})
        if paint:
            matches[paint["id"]] = {"paint": paint, "score": 100, "by": "barcode"}

    text_lines = [norm(ln.text) for ln in lines if ln.kind in ("name", "other", "code", "manufacturer")]
    full = norm(full_text)
    photo_words = set(full.split())

    def every_word_seen(pname: str) -> bool:
        # each word of the paint name must be on the photo, allowing OCR slips ("0il" ~ "oil")
        return all(
            any(fuzz.ratio(w, pw) >= 60 for pw in photo_words)
            for w in pname.split() if len(w) >= 3
        )

    new_barcodes = {b["text"] for b in barcodes if b["paint_id"] is None}

    if text_lines:
        for paint in repo.list_paints(conn):
            if paint["id"] in matches:
                continue
            if maker and paint["manufacturer_id"] != maker["id"]:
                continue  # another manufacturer is another paint, whatever the name
            if new_barcodes and paint["barcode"] and paint["barcode"] not in new_barcodes:
                continue  # it has its own barcode, and the photo shows a different one
            pname = norm(paint["name"])
            if not pname:
                continue
            score = max(fuzz.ratio(pname, t) for t in text_lines)
            if len(pname) >= 4:
                score = max(score, fuzz.partial_ratio(pname, full) * 0.95)
            if maker:
                score += 5
                words = [w for w in pname.split() if len(w) >= 3]
                if len(words) >= 2 and all(any(fuzz.ratio(w, pw) >= 85 for pw in photo_words) for w in words):
                    score = max(score, 90)  # "CHROME" / "METAL" / "COPPER" in any order
            elif len(pname.split()) == 1:
                score = min(score, 88)  # one common word ("Copper") of an unknown brand is only a hint
            if not every_word_seen(pname):
                score = min(score, MATCH_THRESHOLD - 1)  # "Matt Black" is not "Flat Black"
            if code and paint["code"] and norm(paint["code"]) == norm(code):
                score = max(score, 98)
            if score >= MATCH_THRESHOLD:
                matches[paint["id"]] = {"paint": paint, "score": round(min(score, 100)), "by": "text"}

    best_matches = sorted(matches.values(), key=lambda m: -m["score"])[:3]

    return {
        "seconds": reading.seconds,
        "image": {"width": reading.width, "height": reading.height},
        "lines": [{"text": ln.text, "kind": ln.kind, "score": round(ln.score, 2)} for ln in lines],
        "barcodes": barcodes,
        "suggestion": {
            "name": name,
            "manufacturer": {"id": maker["id"], "name": maker["name"]} if maker else None,
            "code": code,
            "barcode": next((b["text"] for b in barcodes if b["paint_id"] is None), None),
            "tag_ids": _tag_ids(f"{full_text} {name or ''}", keywords),  # the name with glued words split
        },
        "matches": best_matches,
    }
