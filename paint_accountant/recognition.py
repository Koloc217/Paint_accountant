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


# words printed on labels that are not part of a paint name
STOPWORDS = {
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
    "greenstuffworld": "Green Stuff World",
    "the army painter": "Army Painter",
    "army painter": "Army Painter",
    "acrylicos vallejo": "Vallejo",
}

# product codes: (manufacturer it usually belongs to, pattern, formatter)
CODE_PATTERNS: list[tuple[str | None, re.Pattern, Any]] = [
    ("Vallejo", re.compile(r"\b(7[0-4])\s?[.,·]\s?(\d{3})\b"), lambda m: f"{m[1]}.{m[2]}"),
    ("AK", re.compile(r"\bAK\s?-?\s?(\d{3,5})\b", re.I), lambda m: f"AK{m[1]}"),
    ("Army Painter", re.compile(r"\b(WP|CP|GM)\s?(\d{4})\b", re.I), lambda m: f"{m[1].upper()}{m[2]}"),
    ("Tamiya", re.compile(r"\b(XF|X|LP|TS|AS)\s?-\s?(\d{1,3})\b", re.I), lambda m: f"{m[1].upper()}-{m[2]}"),
    ("Citadel", re.compile(r"\b(\d{2})\s?-\s?(\d{2})\b"), lambda m: f"{m[1]}-{m[2]}"),
]


def find_code(text: str, manufacturer: str | None = None) -> str | None:
    ordered = sorted(CODE_PATTERNS, key=lambda c: c[0] != manufacturer) if manufacturer else CODE_PATTERNS
    for _, pattern, fmt in ordered:
        m = pattern.search(text)
        if m:
            return fmt(m)
    return None


def _nice_name(text: str) -> str:
    """'NULN OIL' -> 'Nuln Oil'; mixed case is kept as printed."""
    if text.isupper():
        return " ".join(w.capitalize() if w.isalpha() else w for w in text.split())
    return text


def _match_manufacturer(lines: list[Line], makers: list[dict]) -> tuple[dict | None, Line | None]:
    from rapidfuzz import fuzz

    by_name = {norm(m["name"]): m for m in makers}
    best: tuple[float, dict | None, Line | None] = (0, None, None)
    for line in lines:
        text = norm(line.text)
        if not text:
            continue
        words = set(text.split())
        squashed = text.replace(" ", "")
        for alias, canonical in ALIASES.items():
            m = by_name.get(norm(canonical))
            if m and (alias in text or alias.replace(" ", "") in squashed):
                score = 99
                if score > best[0]:
                    best = (score, m, line)
        for key, m in by_name.items():
            if len(key) <= 3:
                score = 100 if key in words else 0  # short names like "AK": whole word only
            else:
                score = fuzz.partial_ratio(key, text) if len(text) >= len(key) - 1 else fuzz.ratio(key, text)
            if score >= 88 and score > best[0]:
                best = (score, m, line)
    return best[1], best[2]


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

    maker, maker_line = _match_manufacturer([ln for ln in lines if ln.kind != "noise"], makers)
    if maker_line is not None:
        maker_line.kind = "manufacturer"

    code = find_code(full_text, maker["name"] if maker else None)

    for ln in lines:
        if ln.kind != "other":
            continue
        words = norm(ln.text).split()
        if code and find_code(ln.text) == code and len(words) <= 2:
            ln.kind = "code"
        elif all(w in keyword_set or w in STOPWORDS or re.fullmatch(r"\d+(ml)?", w) for w in words):
            ln.kind = "tag" if any(w in keyword_set for w in words) else "noise"

    # --- name: the biggest remaining text is usually the paint name
    name_line = max((ln for ln in lines if ln.kind == "other"), key=lambda ln: ln.height, default=None)
    name_parts: list[Line] = []
    if name_line is not None:
        # a long name is often printed on two lines in the same size: "FLAT" / "BLACK"
        name_parts = [name_line]
        for ln in sorted((x for x in lines if x.y > name_line.y), key=lambda x: x.y):
            last = name_parts[-1]
            gap = ln.y - (last.y + last.height)
            same_size = abs(ln.height - name_line.height) <= 0.25 * name_line.height
            if ln.kind == "other" and same_size and -0.2 * name_line.height <= gap <= 0.9 * name_line.height:
                name_parts.append(ln)
            else:
                break
        for ln in name_parts:
            ln.kind = "name"
    name = _nice_name(" ".join(ln.text for ln in name_parts)) if name_parts else None
    if name and code:
        name = " ".join(name.replace(code, "").split()) or name

    # --- barcodes and matches with paints already in the catalog
    matches: dict[int, dict] = {}
    barcodes = []
    for b in reading.barcodes:
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

    if text_lines:
        for paint in repo.list_paints(conn):
            if paint["id"] in matches:
                continue
            pname = norm(paint["name"])
            if not pname:
                continue
            score = max(fuzz.ratio(pname, t) for t in text_lines)
            if len(pname) >= 4:
                score = max(score, fuzz.partial_ratio(pname, full) * 0.95)
            if maker:
                score += 5 if paint["manufacturer_id"] == maker["id"] else -15
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
            "tag_ids": _tag_ids(full_text, keywords),
        },
        "matches": best_matches,
    }
