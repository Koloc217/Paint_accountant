"""Multi-source paint color and barcode enrichment script for Paint Accountant.

Aggregates data across top community miniature paint repositories:
1. PauloSalum/paint-equivalents: 9,600+ paints across 30+ manufacturers with HEX, Lab, RGB, codes.
2. WarHub/warhub-catalog: Brand catalogs with physical EAN/UPC barcodes, HEX colors, and technical roles.
3. ArturSkowronski/minipainter: Curated paints including Citadel 2022+ Contrast & Shades.
4. Arcturus5404/miniature-paints: Scraped Markdown tables across all 34 brand catalogs.
5. Curated supplementary data for known boxed sets (Kimera Signature, Army Painter Rising Sun, etc.).

Matching pipeline:
- Pass 0: Identification of technical / color-free auxiliaries (mediums, thinners, varnishes, cleaners, textures).
- Pass 1: Exact physical barcode match (EAN-13, UPC-A).
- Pass 2: Normalized manufacturer article code (without spaces, dots, or dashes, case-insensitive).
- Pass 3: Cleaned exact name match (with automated prefix/suffix stripping for range noise like 'Contrast ', 'GAW...').
- Pass 4: Fuzzy name matching via difflib (threshold >= 0.82).

Supports updating SQLite database (data/paints.sqlite3) or CSV export files (paints-*.csv).
Creates automated timestamped safety backups before modifying files.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from paint_accountant.config import load_settings
except ImportError:
    load_settings = None

DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "unified-catalogs"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "paints.sqlite3"
DEFAULT_UNMATCHED_CSV = PROJECT_ROOT / "unmatched_paints.csv"
DEFAULT_SIMILARITY_THRESHOLD = 0.82

USER_AGENT = "PaintAccountant-Enrichment/2.0 (Python urllib)"

# Brand alias mapping to unify different naming schemes across repositories
BRAND_ALIASES: dict[str, list[str]] = {
    "AK": ["ak", "ak interactive", "ak_interactive", "akrc", "ak real color", "ak-interactive", "ak-real-color"],
    "Army Painter": ["army painter", "the army painter", "the_army_painter", "army_painter", "army-painter"],
    "Citadel": ["citadel", "citadel colour", "citadel_colour", "citadel-colour", "games workshop", "gw"],
    "Green Stuff World": ["green stuff world", "greenstuffworld", "green_stuff_world", "green-stuff-world", "gsw"],
    "Kimera": ["kimera", "kimera kolors", "kimerakolors", "kimera-kolors", "kimera_kolors"],
    "Mr. Hobby": ["mr. hobby", "mr hobby", "mrhobby", "mr-hobby", "mr_hobby", "mr. color", "mr color", "gunze"],
    "Scale75": ["scale75", "scale 75", "scale_75", "scale-75"],
    "Tamiya": ["tamiya"],
    "Vallejo": ["vallejo"],
    "Monument": ["monument", "monument hobbies", "pro acryl", "monument-pro-acryl"],
    "Duncan": ["duncan", "duncan rhodes", "two thin coats", "two-thin-coats"],
    "Mig": ["mig", "ammo", "ammo by mig", "ammo by mig jimenez", "ammo-mig"],
    "P3": ["p3", "privateer press"],
    "Reaper": ["reaper", "reaper msp"],
    "Revell": ["revell"],
    "Humbrol": ["humbrol"],
    "Italeri": ["italeri"],
    "Coat d'Arms": ["coat d'arms", "coat d arms", "coatdarmes", "coat-darmes"],
    "Foundry": ["foundry"],
    "Turbo Dork": ["turbo dork", "turbodork", "turbo-dork"],
    "Warcolours": ["warcolours"],
    "Kaleido": ["kaleido"],
    "Daler-Rowney": [
        "daler-rowney",
        "daler rowney",
        "daler_rowney",
        "daler",
        "rowney",
        "fw",
        "fw ink",
        "fw inks",
        "fw acrylic ink",
        "fw acrylic inks",
    ],
}

# Reverse lookup: normalized brand string -> canonical brand name
CANONICAL_BRAND: dict[str, str] = {}
for canon, aliases in BRAND_ALIASES.items():
    CANONICAL_BRAND[canon.lower()] = canon
    for a in aliases:
        CANONICAL_BRAND[a.lower()] = canon


def canonical_brand_name(brand: str | None) -> str:
    """Normalize manufacturer name to canonical brand."""
    if not brand:
        return "Unknown"
    norm = brand.strip().lower()
    return CANONICAL_BRAND.get(norm, brand.strip())


# Known technical auxiliary keywords (items that do not possess intrinsic pigment color)
TECHNICAL_KEYWORDS = (
    "flow improver",
    "thinner",
    "retarder",
    "glaze medium",
    "matt medium",
    "matte medium",
    "satin medium",
    "gloss medium",
    "medium",
    "decal fix",
    "decal softener",
    "varnish",
    "cleaner",
    "sandy paste",
    "ground texture",
    "snow texture",
    "liquid frost",
    "paint revitalizer",
    "pigment fixer",
    "surface primer",
    "splash gel",
    "cracke paint",
    "crackle",
    "crackle medium",
    "airbrush cleaner",
    "spider serum",
    "splashgel",
    "textures",
    "primer",
    "pigment",
    "pigments",
    "light earth",
)

# Curated supplementary colors for niche/boxed sets not yet fully indexed in community repos
SUPPLEMENTARY_COLORS: list[dict[str, Any]] = [
    # Citadel Contrast & Shade updates (2022)
    {"brand": "Citadel", "name": "Aeldari Emerald", "code": "", "barcode": "5011921145065", "hex": "#008973"},
    {"brand": "Citadel", "name": "Imperial Fist", "code": "", "barcode": "5011921145126", "hex": "#f9a01b"},
    {"brand": "Citadel", "name": "Soulblight Grey", "code": "", "barcode": "5011921176403", "hex": "#a6a1a5"},
    {"brand": "Citadel", "name": "Gutrippa Flesh", "code": "", "barcode": "8014684145072", "hex": "#8aa878"},
    {"brand": "Citadel", "name": "Mourn Mountain Snow", "code": "26-04", "barcode": "5011921026791", "hex": "#eef2f5"},
    # Army Painter - Rising Sun Boxed Set
    {"brand": "Army Painter", "name": "Kasaitoshi skin", "code": "", "barcode": "", "hex": "#c89b7b"},
    {"brand": "Army Painter", "name": "Kurainori fog", "code": "", "barcode": "", "hex": "#757d82"},
    {"brand": "Army Painter", "name": "Kyojin grey", "code": "", "barcode": "", "hex": "#9c9c9c"},
    {"brand": "Army Painter", "name": "Oni red", "code": "", "barcode": "", "hex": "#8a1c1d"},
    {"brand": "Army Painter", "name": "Rising red", "code": "", "barcode": "", "hex": "#c52227"},
    {"brand": "Army Painter", "name": "Shuto skin", "code": "", "barcode": "", "hex": "#d6aa8b"},
    {"brand": "Army Painter", "name": "Tokai grey", "code": "", "barcode": "", "hex": "#b4b9bc"},
    {"brand": "Army Painter", "name": "Zensho skin", "code": "", "barcode": "", "hex": "#e2bda3"},
    # Kimera Kolors Signature Series (Marc Masclans Skin Kolors)
    {"brand": "Kimera", "name": "Ivory light", "code": "Kms-030", "barcode": "", "hex": "#ece5d8"},
    {"brand": "Kimera", "name": "Pumpkin", "code": "Kms-031", "barcode": "", "hex": "#d6693a"},
    {"brand": "Kimera", "name": "Pale carmine red", "code": "Kns-032", "barcode": "", "hex": "#9b323c"},
    {"brand": "Kimera", "name": "Middle brown", "code": "Kms-033", "barcode": "", "hex": "#5e4235"},
    {"brand": "Kimera", "name": "Black skintone", "code": "Kms-034", "barcode": "", "hex": "#2b262d"},
    {"brand": "Kimera", "name": "Shiny skintone", "code": "Kms-027", "barcode": "", "hex": "#d29272"},
    # AK Interactive Markers & Sci-Fi
    {"brand": "AK", "name": "Gold pigment", "code": "2435", "barcode": "8436574507942", "hex": "#cda434"},
    {"brand": "AK", "name": "Pure Magenta", "code": "AK16003", "barcode": "", "hex": "#b51a53"},
    {"brand": "AK", "name": "Primary Yellow", "code": "AK16006", "barcode": "", "hex": "#f5d400"},
    {"brand": "AK", "name": "Camo Green", "code": "AK16007", "barcode": "", "hex": "#506940"},
    {"brand": "AK", "name": "Nature Green", "code": "AK16008", "barcode": "", "hex": "#2c6c39"},
    {"brand": "AK", "name": "Blood Scarlet", "code": "AK16009", "barcode": "", "hex": "#96131c"},
    {"brand": "AK", "name": "Intense Sienna", "code": "AK16010", "barcode": "", "hex": "#7d3e2b"},
    {"brand": "AK", "name": "Fire Orange", "code": "AK16012", "barcode": "", "hex": "#e3521d"},
    {"brand": "AK", "name": "Immaculate White", "code": "AK16013", "barcode": "", "hex": "#f8f9fa"},
    {"brand": "AK", "name": "Fluor Magenta", "code": "AK16041", "barcode": "", "hex": "#ff1493"},
    {"brand": "AK", "name": "Fluor Red", "code": "AK16042", "barcode": "", "hex": "#ff2400"},
    {"brand": "AK", "name": "Fluor Orange", "code": "AK16043", "barcode": "", "hex": "#ff7f00"},
    {"brand": "AK", "name": "Fluor Yellow", "code": "AK16044", "barcode": "", "hex": "#ccff00"},
    {"brand": "AK", "name": "Fluor Green", "code": "AK16045", "barcode": "", "hex": "#00ff66"},
    {"brand": "AK", "name": "Fluor Blue", "code": "AK16046", "barcode": "", "hex": "#00d4ff"},
    {"brand": "AK", "name": "Mahogany", "code": "AK16047", "barcode": "", "hex": "#4a2420"},
    {"brand": "AK", "name": "Crimson", "code": "AK16048", "barcode": "", "hex": "#800020"},
    {"brand": "AK", "name": "Toad Green", "code": "AK16050", "barcode": "", "hex": "#446e3e"},
    {"brand": "AK", "name": "Forge Red", "code": "AK16051", "barcode": "", "hex": "#a32626"},
    {"brand": "AK", "name": "Eggplant", "code": "AK16052", "barcode": "", "hex": "#382333"},
    {"brand": "AK", "name": "Light Wood", "code": "AK16053", "barcode": "", "hex": "#b38b6d"},
    {"brand": "AK", "name": "Cyan", "code": "AK16054", "barcode": "", "hex": "#00b7eb"},
    {"brand": "AK", "name": "Xtreme White 1a", "code": "AK1501", "barcode": "8435568327337", "hex": "#ffffff"},
    {"brand": "AK", "name": "Robot White 1b", "code": "AK1502", "barcode": "8435568327344", "hex": "#e8ecef"},
    {"brand": "AK", "name": "Purple Nebula 10A", "code": "AK1519", "barcode": "8435568327511", "hex": "#623b6d"},
    {"brand": "AK", "name": "Cosmic Dust 19A", "code": "AK1537", "barcode": "8435568327696", "hex": "#d1be9b"},
    {"brand": "AK", "name": "Infinity black 19b", "code": "AK1538", "barcode": "8435568327702", "hex": "#1b1c1e"},
    {"brand": "AK", "name": "Light Brick 23A", "code": "AK1573", "barcode": "409551777247", "hex": "#a85d4d"},
    # Kaleido Acrylic Paints & Metallics
    {"brand": "Kaleido", "name": "Aluminum", "code": "Km007", "barcode": "06975834948313", "hex": "#d1d5db"},
    {"brand": "Kaleido", "name": "Champagne Gold", "code": "Km011", "barcode": "06975834948351", "hex": "#e5d3a2"},
    {"brand": "Kaleido", "name": "Clear amber", "code": "Kc012", "barcode": "", "hex": "#ffbf00"},
    {"brand": "Kaleido", "name": "Clear black", "code": "Kc002", "barcode": "", "hex": "#222222"},
    {"brand": "Kaleido", "name": "Clear blue", "code": "Kc008", "barcode": "", "hex": "#0077be"},
    {"brand": "Kaleido", "name": "Clear brown", "code": "Kc011", "barcode": "", "hex": "#6e473b"},
    {"brand": "Kaleido", "name": "Clear green", "code": "Kc006", "barcode": "", "hex": "#00a86b"},
    {"brand": "Kaleido", "name": "Clear orange", "code": "Kc004", "barcode": "", "hex": "#ff7518"},
    {"brand": "Kaleido", "name": "Clear pink", "code": "Kc010", "barcode": "", "hex": "#ff69b4"},
    {"brand": "Kaleido", "name": "Clear purple", "code": "Kc009", "barcode": "", "hex": "#800080"},
    {"brand": "Kaleido", "name": "Clear red", "code": "Kc003", "barcode": "", "hex": "#e32636"},
    {"brand": "Kaleido", "name": "Clear teal", "code": "Kc007", "barcode": "", "hex": "#008080"},
    {"brand": "Kaleido", "name": "Clear white", "code": "Kc001", "barcode": "", "hex": "#f5f5f5"},
    {"brand": "Kaleido", "name": "Clear yellow", "code": "Kc005", "barcode": "", "hex": "#ffef00"},
    {"brand": "Kaleido", "name": "Copper", "code": "Km009", "barcode": "06975834948337", "hex": "#b87333"},
    {"brand": "Kaleido", "name": "Flashy gold", "code": "Km012", "barcode": "06975834948368", "hex": "#ffd700"},
    {"brand": "Kaleido", "name": "Gun Metall", "code": "Km002", "barcode": "06975834948269", "hex": "#434b4d"},
    {"brand": "Kaleido", "name": "Red Copper", "code": "Km008", "barcode": "06975834948320", "hex": "#c45a3a"},
    {"brand": "Kaleido", "name": "Scorched Gold", "code": "Km010", "barcode": "06975834948344", "hex": "#aa7a30"},
    {"brand": "Kaleido", "name": "Silver", "code": "Km006", "barcode": "06975834948306", "hex": "#e0e0e0"},
    {"brand": "Kaleido", "name": "Stainless steel", "code": "Km005", "barcode": "06975834948290", "hex": "#8a9ea7"},
    {"brand": "Kaleido", "name": "Titanium Alloy", "code": "Km004", "barcode": "06975834948283", "hex": "#9fa4a6"},
    {"brand": "Kaleido", "name": "Tungsten Steel", "code": "Km003", "barcode": "06975834948276", "hex": "#505459"},
    {"brand": "Kaleido", "name": "Zirconium Black", "code": "Km001", "barcode": "06975834948252", "hex": "#1a1a1a"},
    # Scale75 Artist Ink & Color
    {"brand": "Scale75", "name": "OSL White", "code": "3506", "barcode": "8435646508665", "hex": "#ffffff"},
    {"brand": "Scale75", "name": "PBk-7 Carbon Black", "code": "Sart-02", "barcode": "8423153061766", "hex": "#141414"},
    {"brand": "Scale75", "name": "Scalecolor Artist White Ink", "code": "sart-80", "barcode": "8435635306326", "hex": "#ffffff"},
    # Green Stuff World Metallics, Opaque & Inks
    {"brand": "Green Stuff World", "name": "Metal color 1870 - El Dorado", "code": "1870", "barcode": "", "hex": "#d4af37"},
    {"brand": "Green Stuff World", "name": "Metal color Shiny Gold", "code": "1869", "barcode": "", "hex": "#ffd700"},
    {"brand": "Green Stuff World", "name": "Fluorescent white", "code": "1760", "barcode": "8436574501193", "hex": "#ffffff"},
    {"brand": "Green Stuff World", "name": "Osl White", "code": "Pw6", "barcode": "8435646516455", "hex": "#ffffff"},
    {"brand": "Green Stuff World", "name": "Transparent Warm Green", "code": "Pg7/py83", "barcode": "8435646516387", "hex": "#29ab87"},
    {"brand": "Green Stuff World", "name": "Transparent Yellow", "code": "Py83", "barcode": "8435646516417", "hex": "#ffea00"},
    {"brand": "Green Stuff World", "name": "Dark rose tan opaque", "code": "4119", "barcode": "8435646514789", "hex": "#b38b7d"},
    {"brand": "Green Stuff World", "name": "Dead flesh opaque", "code": "4112", "barcode": "8435646514857", "hex": "#8e9b7b"},
    {"brand": "Green Stuff World", "name": "Feral Brown Opaque", "code": "4110", "barcode": "8435646514833", "hex": "#734d36"},
    {"brand": "Green Stuff World", "name": "Light tan opaque", "code": "4118", "barcode": "8435646514772", "hex": "#d2b48c"},
    {"brand": "Green Stuff World", "name": "Khaki Bone Opaque", "code": "4129", "barcode": "8435646514703", "hex": "#c3b091"},
    {"brand": "Green Stuff World", "name": "Granite grey opaque", "code": "", "barcode": "8435646514840", "hex": "#676767"},
    {"brand": "Green Stuff World", "name": "Green lnteerrence", "code": "2585", "barcode": "8436574509441", "hex": "#2e8b57"},
    {"brand": "Green Stuff World", "name": "Holographic", "code": "COLORSHIFT", "barcode": "8436574500325", "hex": "#dcdcdc"},
    {"brand": "Green Stuff World", "name": "Black shadow gip", "code": "3487", "barcode": "8435646508474", "hex": "#222222"},
    # Daler-Rowney FW Acrylic Artists' Inks
    {"brand": "Daler-Rowney", "name": "Fluorescent Green", "code": "160029349", "barcode": "50859641", "hex": "#00ff40"},
    {"brand": "Daler-Rowney", "name": "Fluorescent Blue", "code": "160029100", "barcode": "50859658", "hex": "#00d4ff"},
    {"brand": "Daler-Rowney", "name": "Fluorescent Orange", "code": "160029653", "barcode": "50859665", "hex": "#ff6700"},
    {"brand": "Daler-Rowney", "name": "Fluorescent Pink", "code": "160029538", "barcode": "50859672", "hex": "#ff1493"},
    {"brand": "Daler-Rowney", "name": "Fluorescent Yellow", "code": "160029681", "barcode": "50859634", "hex": "#ccff00"},
    {"brand": "Daler-Rowney", "name": "Fluorescent Red", "code": "160029544", "barcode": "50859627", "hex": "#ff2a4d"},
    {"brand": "Daler-Rowney", "name": "Emerald Green", "code": "160029335", "barcode": "50761814", "hex": "#009b77"},
    {"brand": "Daler-Rowney", "name": "Cool Grey", "code": "160029053", "barcode": "50761906", "hex": "#788287"},
    {"brand": "Daler-Rowney", "name": "Sepia", "code": "160029251", "barcode": "50761890", "hex": "#4a3525"},
    {"brand": "Daler-Rowney", "name": "Black", "code": "160029028", "barcode": "50761913", "hex": "#0b0b0b"},
]


def normalize_code(code: str | None) -> str:
    """Normalize article code: strip whitespace, dots, and hyphens, uppercase."""
    if not code:
        return ""
    return re.sub(r"[\s.\-_/]", "", str(code)).upper()


def normalize_barcode(barcode: str | None) -> str:
    """Normalize barcode: digits only, strip leading zeroes if length 14 or 13."""
    if not barcode:
        return ""
    digits = re.sub(r"\D", "", str(barcode))
    # Strip leading zero from 14-digit GTIN if it wraps an EAN-13
    if len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def normalize_hex(raw_hex: str | None) -> str | None:
    """Extract and validate 6-digit hex color with lowercase leading '#', e.g. '#3b4039'."""
    if not raw_hex:
        return None
    match = re.search(r"#([0-9a-fA-F]{6})\b", raw_hex)
    if match:
        return f"#{match.group(1).lower()}"
    return None


def clean_name(name: str | None) -> str:
    """Clean paint name by stripping range prefixes and commercial noise."""
    if not name:
        return ""
    n = name.strip()
    # Strip range prefixes
    n = re.sub(r"^(Contrast|Base|Layer|Shade|Dry|Air|Technical|Primer)\s+", "", n, flags=re.I)
    n = re.sub(r"^(GAW\d+|Games Texture|Texture)\s+", "", n, flags=re.I)
    # Strip commercial suffixes
    n = re.sub(r"\s+(Paint|Color|Colour|17ml|12ml|18ml|24ml|Dropper|Ink)$", "", n, flags=re.I)
    # Normalize multiple whitespace
    n = re.sub(r"\s+", " ", n)
    return n.strip().lower()


def is_technical_product(name: str, code: str = "") -> tuple[bool, str]:
    """Determine whether an item is a medium, thinner, varnish, cleaner or auxiliary."""
    nl = name.lower()
    for kw in TECHNICAL_KEYWORDS:
        if kw in nl:
            return True, kw
    return False, ""


def download_url_text(url: str, timeout: int = 30) -> str:
    """Download text from URL with custom User Agent."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def parse_warhub_yaml(text: str) -> list[dict[str, Any]]:
    """Lightweight zero-dependency parser for WarHub brand YAML catalog files."""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_key: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        # Item start
        m_item = re.match(r"^- name:\s*(?:'|\")?(.*?)(?:'|\")?\s*$", line)
        if m_item:
            if current:
                items.append(current)
            current = {"name": m_item.group(1), "eans": []}
            current_key = None
            continue

        if current is None:
            continue

        # Key-value scalar properties
        m_prop = re.match(r"^\s+([a-zA-Z0-9_]+):\s*(?:'|\")?(.*?)(?:'|\")?\s*$", line)
        if m_prop:
            key, val = m_prop.group(1), m_prop.group(2)
            current_key = key
            if key == "ean" and val:
                current["eans"].append(normalize_barcode(val))
                current["ean"] = normalize_barcode(val)
            elif key == "hex" and val:
                current["hex"] = normalize_hex(val)
            elif key in ("set", "category", "role", "finish", "volumeMl", "code", "colourless", "productCode"):
                current[key] = val
            continue

        # Additional list elements (e.g. under additionalEans)
        m_list = re.match(r"^\s+-\s*(?:'|\")?(.*?)(?:'|\")?\s*$", line)
        if m_list and current_key in ("additionalEans", "eans"):
            val = m_list.group(1)
            if val:
                current["eans"].append(normalize_barcode(val))

    if current:
        items.append(current)
    return items


def parse_markdown_table(text: str) -> list[dict[str, Any]]:
    """Parse GitHub Markdown paint table into a list of paint records."""
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return []

    header_cells = [col.strip() for col in lines[0].split("|")[1:-1]]
    records = []

    for row_line in lines[2:]:
        if re.match(r"^\|(\s*:?-+:?\s*\|)+$", row_line):
            continue

        cells = [col.strip() for col in row_line.split("|")[1:-1]]
        if len(cells) < len(header_cells):
            continue

        row_dict = dict(zip(header_cells, cells))
        name = row_dict.get("Name", "").strip()
        code_val = row_dict.get("Code", "").strip() if "Code" in row_dict else ""
        if code_val.lower() in ("null", "none", "-"):
            code_val = ""

        hex_color = normalize_hex(row_dict.get("Hex", ""))

        records.append({
            "name": name,
            "code": code_val or None,
            "hex": hex_color,
        })

    return records


def get_source_priority(source: str) -> int:
    """Return priority score for a data source.

    Arcturus5404 is the primary, highest priority repository (priority 100).
    """
    s_lower = source.lower()
    if "arcturus" in s_lower:
        return 100
    if "curated" in s_lower:
        return 90
    if "warhub" in s_lower:
        return 80
    if "minipainter" in s_lower:
        return 70
    if "paulosalum" in s_lower:
        return 50
    return 10


class UnifiedPaintCatalog:
    """Unified master catalog combining Arcturus5404 (primary), WarHub, Minipainter, and PauloSalum."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Indexes
        # (canonical_brand, barcode) -> record
        self.by_barcode: dict[tuple[str, str], dict[str, Any]] = {}
        # (canonical_brand, norm_code) -> record
        self.by_code: dict[tuple[str, str], dict[str, Any]] = {}
        # (canonical_brand, clean_name) -> record
        self.by_name: dict[tuple[str, str], dict[str, Any]] = {}
        # canonical_brand -> list of records (for fuzzy search)
        self.by_brand_items: dict[str, list[dict[str, Any]]] = {}

    def add_record(
        self,
        brand: str,
        name: str,
        code: str | None = None,
        barcode: str | None = None,
        hex_color: str | None = None,
        source: str = "",
        is_technical: bool = False,
        eans: list[str] | None = None,
    ) -> None:
        c_brand = canonical_brand_name(brand)
        hex_val = normalize_hex(hex_color)
        prio = get_source_priority(source)

        record = {
            "brand": c_brand,
            "name": name.strip(),
            "code": code.strip() if code else "",
            "barcode": normalize_barcode(barcode),
            "hex": hex_val,
            "source": source,
            "priority": prio,
            "is_technical": is_technical,
        }

        # Index by barcodes
        all_eans = list(eans or [])
        if barcode:
            all_eans.append(normalize_barcode(barcode))

        for e in all_eans:
            norm_b = normalize_barcode(e)
            if norm_b:
                existing = self.by_barcode.get((c_brand, norm_b))
                if not existing:
                    self.by_barcode[(c_brand, norm_b)] = record
                elif not existing.get("hex") and hex_val:
                    self.by_barcode[(c_brand, norm_b)] = record
                elif hex_val and prio > existing.get("priority", 0):
                    self.by_barcode[(c_brand, norm_b)] = record

        # Index by code
        nc = normalize_code(code)
        if nc and len(nc) >= 2:
            existing = self.by_code.get((c_brand, nc))
            if not existing:
                self.by_code[(c_brand, nc)] = record
            elif not existing.get("hex") and hex_val:
                self.by_code[(c_brand, nc)] = record
            elif hex_val and prio > existing.get("priority", 0):
                self.by_code[(c_brand, nc)] = record

        # Index by cleaned name
        cn = clean_name(name)
        if cn:
            existing = self.by_name.get((c_brand, cn))
            if not existing:
                self.by_name[(c_brand, cn)] = record
            elif not existing.get("hex") and hex_val:
                self.by_name[(c_brand, cn)] = record
            elif hex_val and prio > existing.get("priority", 0):
                self.by_name[(c_brand, cn)] = record

        self.by_brand_items.setdefault(c_brand, []).append(record)

    def load_all_sources(self, force_download: bool = False) -> None:
        """Download and index all repository datasets."""
        print(f"Loading unified paint catalogs (cache: {self.cache_dir})...")

        # 1. PauloSalum/paint-equivalents (9,600+ paints)
        ps_file = self.cache_dir / "paulosalum_paints.json"
        if not ps_file.is_file() or force_download:
            print("  Downloading PauloSalum dataset...")
            try:
                content = download_url_text(
                    "https://raw.githubusercontent.com/PauloSalum/paint-equivalents/main/paints.json"
                )
                ps_file.write_text(content, encoding="utf-8")
            except Exception as e:
                print(f"  [WARN] Failed to fetch PauloSalum: {e}")

        if ps_file.is_file():
            try:
                ps_data = json.loads(ps_file.read_text(encoding="utf-8"))
                for item in ps_data:
                    self.add_record(
                        brand=item.get("brand", ""),
                        name=item.get("name", ""),
                        code=item.get("manufacturer_code"),
                        hex_color=item.get("hex"),
                        source="PauloSalum",
                        is_technical=not item.get("has_color", True),
                    )
                print(f"  ✓ PauloSalum: {len(ps_data)} records indexed")
            except Exception as e:
                print(f"  [ERROR] Parsing PauloSalum: {e}")

        # 2. WarHub/warhub-catalog (Barcodes & Brands YAMLs)
        warhub_brands = [
            "citadel-colour",
            "vallejo",
            "ak-interactive",
            "ak-real-color",
            "army-painter",
            "green-stuff-world",
            "scale75",
            "tamiya",
            "mr-hobby",
            "kimera-kolors",
            "monument-pro-acryl",
            "two-thin-coats",
            "ammo-mig",
        ]
        wh_count = 0
        for wb in warhub_brands:
            wb_file = self.cache_dir / f"warhub_{wb}.yaml"
            if not wb_file.is_file() or force_download:
                url = f"https://raw.githubusercontent.com/WarHub/warhub-catalog/main/data/paints/brands/{wb}.yaml"
                try:
                    text = download_url_text(url)
                    wb_file.write_text(text, encoding="utf-8")
                except Exception as e:
                    continue

            if wb_file.is_file():
                try:
                    items = parse_warhub_yaml(wb_file.read_text(encoding="utf-8"))
                    wh_count += len(items)
                    wb_map = {
                        "citadel-colour": "Citadel",
                        "vallejo": "Vallejo",
                        "ak-interactive": "AK",
                        "ak-real-color": "AK",
                        "army-painter": "Army Painter",
                        "green-stuff-world": "Green Stuff World",
                        "scale75": "Scale75",
                        "tamiya": "Tamiya",
                        "mr-hobby": "Mr. Hobby",
                        "kimera-kolors": "Kimera",
                        "monument-pro-acryl": "Monument",
                        "two-thin-coats": "Duncan",
                        "ammo-mig": "Mig",
                    }
                    target_brand = wb_map.get(wb, wb)
                    for item in items:
                        is_tech = item.get("role") in ("medium", "varnish", "cleaner", "primer") or item.get("colourless") == "true"
                        self.add_record(
                            brand=target_brand,
                            name=item.get("name", ""),
                            code=item.get("code") or item.get("productCode"),
                            barcode=item.get("ean"),
                            hex_color=item.get("hex"),
                            source=f"WarHub({wb})",
                            is_technical=is_tech,
                            eans=item.get("eans"),
                        )
                except Exception:
                    pass
        print(f"  ✓ WarHub: {wh_count} records indexed")

        # 3. ArturSkowronski/minipainter (2022+ Contrast, Fanatic, etc.)
        for mp_brand in ["citadel", "vallejo", "ak_interactive", "army_painter"]:
            mp_file = self.cache_dir / f"minipainter_{mp_brand}.json"
            if not mp_file.is_file() or force_download:
                url = f"https://raw.githubusercontent.com/ArturSkowronski/minipainter/master/data/catalog/{mp_brand}.json"
                try:
                    text = download_url_text(url)
                    mp_file.write_text(text, encoding="utf-8")
                except Exception:
                    continue

            if mp_file.is_file():
                try:
                    paints_list = json.loads(mp_file.read_text(encoding="utf-8")).get("paints", [])
                    for item in paints_list:
                        rgb = item.get("rgb")
                        hex_val = None
                        if rgb and isinstance(rgb, dict) and "r" in rgb:
                            hex_val = f"#{rgb['r']:02x}{rgb['g']:02x}{rgb['b']:02x}"
                        self.add_record(
                            brand=item.get("provider", mp_brand),
                            name=item.get("name", ""),
                            code=item.get("code"),
                            hex_color=hex_val,
                            source=f"Minipainter({mp_brand})",
                        )
                except Exception:
                    pass

        # 4. Arcturus5404/miniature-paints (all 34 markdown files - highest priority)
        arcturus_brands = {
            "Citadel": "Citadel_Colour.md",
            "Vallejo": "Vallejo.md",
            "AK": "AK.md",
            "AKRC": "AKRC.md",
            "Green Stuff World": "GreenStuffWorld.md",
            "Scale75": "Scale75.md",
            "Tamiya": "Tamiya.md",
            "Army Painter": "Army_Painter.md",
            "Kimera": "KimeraKolors.md",
            "Mr. Hobby": "MrHobby.md",
            "Monument": "Monument.md",
            "Duncan": "Duncan.md",
            "Mig": "Mig.md",
            "Reaper": "Reaper.md",
            "Coat d'Arms": "CoatDArmes.md",
            "Foundry": "Foundry.md",
            "Turbo Dork": "TurboDork.md",
            "Warcolours": "Warcolours.md",
            "Acrilex": "Acrilex.md",
            "Apple Barrel": "AppleBarrel.md",
            "Arteza": "Arteza.md",
            "Creature": "Creature.md",
            "FolkArt": "FolkArt.md",
            "Golden": "Golden.md",
            "Humbrol": "Humbrol.md",
            "Italeri": "Italeri.md",
            "Liquitex": "Liquitex.md",
            "Mission Models": "MissionModels.md",
            "Mr. Paint": "MrPaint.md",
            "P3": "P3.md",
            "Pantone": "Pantone.md",
            "RAL": "RAL.md",
            "Revell": "Revell.md",
            "Tom Color": "TomColor.md",
        }
        arc_count = 0
        for b_name, md_file in arcturus_brands.items():
            cache_file = self.cache_dir / f"arcturus_{md_file.replace('.md', '.json')}"
            records = []
            if cache_file.is_file() and not force_download:
                try:
                    records = json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:
                    records = []

            if not records:
                md_url = f"https://raw.githubusercontent.com/Arcturus5404/miniature-paints/main/paints/{md_file}"
                try:
                    text = download_url_text(md_url)
                    records = parse_markdown_table(text)
                    cache_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

            arc_count += len(records)
            for r in records:
                self.add_record(
                    brand=b_name,
                    name=r.get("name", ""),
                    code=r.get("code"),
                    hex_color=r.get("hex"),
                    source=f"Arcturus({b_name})",
                )
        print(f"  ✓ Arcturus5404 (Primary): {arc_count} records indexed")

        # 5. Supplementary known boxed sets
        for supp in SUPPLEMENTARY_COLORS:
            self.add_record(
                brand=supp["brand"],
                name=supp["name"],
                code=supp.get("code"),
                barcode=supp.get("barcode"),
                hex_color=supp.get("hex"),
                source="CuratedSupplementary",
            )
        print(f"  ✓ Curated Supplementary: {len(SUPPLEMENTARY_COLORS)} items indexed")

        total_unique = sum(len(items) for items in self.by_brand_items.values())
        print(f"Master Catalog ready: {total_unique} entries across {len(self.by_brand_items)} brands.\n")

    def match(
        self,
        brand: str,
        name: str,
        code: str | None = None,
        barcode: str | None = None,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> tuple[str | None, str, float, bool]:
        """Perform multi-pass matching for a paint, prioritizing Arcturus5404.

        Returns: (hex_color, match_explanation, score, is_technical)
        """
        c_brand = canonical_brand_name(brand)
        is_tech, tech_reason = is_technical_product(name, code or "")

        # Pass 1: Physical Barcode Match (EAN/UPC)
        norm_b = normalize_barcode(barcode)
        if norm_b:
            rec = self.by_barcode.get((c_brand, norm_b))
            if rec:
                hex_c = rec.get("hex")
                src = rec.get("source", "")
                if hex_c:
                    return hex_c, f"Pass 1: Exact Barcode ({norm_b}) -> '{rec['name']}' [{src}]", 1.0, False
                if rec.get("is_technical"):
                    return None, f"Pass 1: Barcode matched technical product '{rec['name']}' [{src}]", 1.0, True

        # Pass 2: Normalized Code Match
        nc = normalize_code(code)
        ignored_codes = {"BASE", "AIR", "LAYER", "SHADE", "CONTRAST", "TECHNICAL", "DRY"}
        if nc and len(nc) >= 2 and nc not in ignored_codes:
            rec = self.by_code.get((c_brand, nc))
            if rec and rec.get("hex"):
                src = rec.get("source", "")
                return rec["hex"], f"Pass 2: Exact Code ({nc}) -> '{rec['name']}' [{src}]", 1.0, False

        # Pass 3: Cleaned Name Exact Match
        cn = clean_name(name)
        if cn:
            rec = self.by_name.get((c_brand, cn))
            if rec and rec.get("hex"):
                src = rec.get("source", "")
                return rec["hex"], f"Pass 3: Cleaned Name -> '{rec['name']}' [{src}]", 1.0, False

        # Pass 4: Fuzzy Name Match via difflib (prioritizing Arcturus5404)
        brand_candidates = self.by_brand_items.get(c_brand, [])
        best_ratio = 0.0
        best_cand: dict[str, Any] | None = None

        for item in brand_candidates:
            if not item.get("hex"):
                continue
            item_cn = clean_name(item.get("name"))
            if not item_cn:
                continue
            ratio = difflib.SequenceMatcher(None, cn, item_cn).ratio()
            item_prio = item.get("priority", 0)
            best_prio = best_cand.get("priority", 0) if best_cand else 0
            if ratio > best_ratio or (abs(ratio - best_ratio) < 1e-4 and item_prio > best_prio):
                best_ratio = ratio
                best_cand = item

        if best_ratio >= threshold and best_cand:
            src = best_cand.get("source", "")
            return best_cand["hex"], f"Pass 4: Fuzzy Name ({best_ratio:.2f}) -> '{best_cand['name']}' [{src}]", best_ratio, False

        # Technical check fallback
        if is_tech:
            return None, f"Technical product without color ({tech_reason})", 1.0, True

        best_cand_name = best_cand["name"] if best_cand else "None"
        return None, f"No match (best: '{best_cand_name}', score: {best_ratio:.2f})", best_ratio, False


def process_database(
    db_path: Path,
    catalog: UnifiedPaintCatalog,
    dry_run: bool = False,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    unmatched_file: Path = DEFAULT_UNMATCHED_CSV,
    verbose: bool = False,
) -> None:
    """Match paints from SQLite database and fill missing HEX codes."""
    if not db_path.is_file():
        raise SystemExit(f"Database file not found: {db_path}")

    print(f"\nConnecting to database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    paints = conn.execute("""
        SELECT p.id, m.name AS manufacturer, p.name, p.code, p.hex, p.barcode
        FROM paint p
        JOIN manufacturer m ON p.manufacturer_id = m.id
        ORDER BY m.name, p.name
    """).fetchall()

    stats_by_mfr: dict[str, dict[str, int]] = {}
    to_update: list[tuple[str, int, str, str, str, str]] = []
    unmatched: list[dict[str, Any]] = []

    for row in paints:
        mfr = row["manufacturer"]
        name = row["name"]
        code = row["code"]
        current_hex = row["hex"]
        barcode = row["barcode"]
        pid = row["id"]

        if mfr not in stats_by_mfr:
            stats_by_mfr[mfr] = {
                "total": 0,
                "already_filled": 0,
                "matched_barcode": 0,
                "matched_code": 0,
                "matched_name": 0,
                "technical": 0,
                "unmatched": 0,
            }

        stats_by_mfr[mfr]["total"] += 1

        if current_hex and current_hex.strip():
            stats_by_mfr[mfr]["already_filled"] += 1
            continue

        hex_val, method_str, score, is_tech = catalog.match(
            brand=mfr,
            name=name,
            code=code,
            barcode=barcode,
            threshold=threshold,
        )

        if hex_val:
            if "Pass 1: Exact Barcode" in method_str:
                stats_by_mfr[mfr]["matched_barcode"] += 1
            elif "Pass 2: Exact Code" in method_str:
                stats_by_mfr[mfr]["matched_code"] += 1
            else:
                stats_by_mfr[mfr]["matched_name"] += 1
            to_update.append((hex_val, pid, mfr, name, code, method_str))
            if verbose:
                print(f"  ✓ [{mfr}] '{name}' ({code or '—'}) -> {hex_val} via {method_str}")
        elif is_tech:
            stats_by_mfr[mfr]["technical"] += 1
            if verbose:
                print(f"  ⚙ [{mfr}] '{name}' -> Technical (No Color): {method_str}")
            unmatched.append({
                "Manufacturer": mfr,
                "Name": name,
                "Code": code or "",
                "Barcode": barcode or "",
                "Reason": method_str,
                "Similarity": f"{score:.2f}",
                "Category": "Technical (No Color)",
            })
        else:
            stats_by_mfr[mfr]["unmatched"] += 1
            unmatched.append({
                "Manufacturer": mfr,
                "Name": name,
                "Code": code or "",
                "Barcode": barcode or "",
                "Reason": method_str,
                "Similarity": f"{score:.2f}",
                "Category": "Missing Color",
            })

    # Summary table
    print("\n" + "=" * 90)
    print("ENRICHMENT STATISTICS BY MANUFACTURER")
    print("=" * 90)
    header_fmt = "{:<18} | {:>5} | {:>8} | {:>7} | {:>7} | {:>7} | {:>6} | {:>6} | {:>6}"
    row_fmt = "{:<18} | {:>5} | {:>8} | {:>7} | {:>7} | {:>7} | {:>6} | {:>6} | {:>5.1f}%"

    print(header_fmt.format(
        "Manufacturer", "Total", "Had HEX", "Barcode", "By Code", "By Name", "Tech", "Miss", "Done %"
    ))
    print("-" * 90)

    total_all = sum(s["total"] for s in stats_by_mfr.values())
    total_had = sum(s["already_filled"] for s in stats_by_mfr.values())
    total_bar = sum(s["matched_barcode"] for s in stats_by_mfr.values())
    total_code = sum(s["matched_code"] for s in stats_by_mfr.values())
    total_name = sum(s["matched_name"] for s in stats_by_mfr.values())
    total_tech = sum(s["technical"] for s in stats_by_mfr.values())
    total_miss = sum(s["unmatched"] for s in stats_by_mfr.values())

    for mfr, s in sorted(stats_by_mfr.items()):
        needed = s["matched_barcode"] + s["matched_code"] + s["matched_name"] + s["technical"] + s["unmatched"]
        resolved = s["matched_barcode"] + s["matched_code"] + s["matched_name"] + s["technical"]
        pct = (resolved / needed * 100) if needed else 100.0
        print(row_fmt.format(
            mfr,
            s["total"],
            s["already_filled"],
            s["matched_barcode"],
            s["matched_code"],
            s["matched_name"],
            s["technical"],
            s["unmatched"],
            pct,
        ))

    print("-" * 90)
    total_resolved = total_bar + total_code + total_name + total_tech
    total_needed = total_bar + total_code + total_name + total_tech + total_miss
    total_pct = (total_resolved / total_needed * 100) if total_needed else 100.0
    print(row_fmt.format(
        "TOTAL",
        total_all,
        total_had,
        total_bar,
        total_code,
        total_name,
        total_tech,
        total_miss,
        total_pct,
    ))
    print("=" * 90)

    # Save unmatched positions
    unmatched_file.parent.mkdir(parents=True, exist_ok=True)
    with open(unmatched_file, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["Manufacturer", "Name", "Code", "Barcode", "Category", "Reason", "Similarity"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(unmatched)

    print(f"\nUnmatched / Technical items saved ({len(unmatched)}): {unmatched_file}")

    if dry_run:
        print(f"\n[DRY-RUN] Preview complete. {len(to_update)} new HEX codes would be saved into database.")
        print("[DRY-RUN] Database was NOT modified.")
    else:
        if to_update:
            backup_path = db_path.with_name(f"{db_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
            conn.close()
            shutil.copy2(db_path, backup_path)
            print(f"Safety backup created: {backup_path}")

            conn = sqlite3.connect(db_path)
            with conn:
                for hex_val, pid, mfr, name, code, method_str in to_update:
                    conn.execute(
                        "UPDATE paint SET hex = ?, updated_at = datetime('now') WHERE id = ?",
                        (hex_val, pid),
                    )
            conn.close()
            print(f"Successfully updated {len(to_update)} paints with newly matched HEX colors in {db_path}!")
        else:
            print("No empty HEX values required updating.")


def process_csv(
    csv_path: Path,
    catalog: UnifiedPaintCatalog,
    dry_run: bool = False,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    unmatched_file: Path = DEFAULT_UNMATCHED_CSV,
) -> None:
    """Match paints from a CSV file (e.g. paints-2026-11-21.csv) and fill missing HEX codes."""
    if not csv_path.is_file():
        raise SystemExit(f"CSV file not found: {csv_path}")

    print(f"\nProcessing CSV file: {csv_path}")
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = list(csv.DictReader(f))

    if not reader:
        print("CSV file is empty.")
        return

    headers = list(reader[0].keys())
    hex_key = next((k for k in ("Color (hex)", "hex", "Color") if k in headers), None)
    if not hex_key:
        hex_key = "Color (hex)"
        headers.append(hex_key)

    updated_count = 0
    unmatched: list[dict[str, Any]] = []

    for row in reader:
        mfr = row.get("Manufacturer", "").strip()
        name = row.get("Name", "").strip()
        code = row.get("Code", "").strip()
        barcode = row.get("Barcode", "").strip()
        current_hex = row.get(hex_key, "").strip()

        if current_hex:
            continue

        hex_val, method_str, score, is_tech = catalog.match(
            brand=mfr,
            name=name,
            code=code,
            barcode=barcode,
            threshold=threshold,
        )

        if hex_val:
            row[hex_key] = hex_val
            updated_count += 1
        elif is_tech:
            unmatched.append({
                "Manufacturer": mfr,
                "Name": name,
                "Code": code,
                "Barcode": barcode,
                "Category": "Technical (No Color)",
                "Reason": method_str,
                "Similarity": f"{score:.2f}",
            })
        else:
            unmatched.append({
                "Manufacturer": mfr,
                "Name": name,
                "Code": code,
                "Barcode": barcode,
                "Category": "Missing Color",
                "Reason": method_str,
                "Similarity": f"{score:.2f}",
            })

    print(f"CSV processing finished: {updated_count} rows resolved with new HEX colors.")

    unmatched_file.parent.mkdir(parents=True, exist_ok=True)
    with open(unmatched_file, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["Manufacturer", "Name", "Code", "Barcode", "Category", "Reason", "Similarity"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(unmatched)

    if not dry_run and updated_count > 0:
        backup_csv = csv_path.with_name(f"{csv_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(csv_path, backup_csv)
        print(f"Safety backup created: {backup_csv}")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(reader)
        print(f"Updated CSV saved: {csv_path} ({updated_count} HEX codes filled).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified multi-source paint color and barcode enrichment tool for Paint Accountant."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview matching results and statistics without modifying database or CSV.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite database (default: data_dir/paints.sqlite3).",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional path to a CSV file to process instead of SQLite DB.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"Directory to store cached catalogs (default: {DEFAULT_CACHE_DIR}).",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download all catalogs from GitHub even if local cache exists.",
    )
    parser.add_argument(
        "--unmatched",
        type=Path,
        default=DEFAULT_UNMATCHED_CSV,
        help=f"Output CSV path for unmatched positions (default: {DEFAULT_UNMATCHED_CSV}).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help=f"Fuzzy matching threshold for difflib (default: {DEFAULT_SIMILARITY_THRESHOLD}).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print each matched paint and its matched source in the terminal output.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Determine database path
    if args.db:
        db_path = args.db
    elif load_settings:
        try:
            settings = load_settings()
            db_path = settings.db_path
        except Exception:
            db_path = DEFAULT_DB_PATH
    else:
        db_path = DEFAULT_DB_PATH

    # Load multi-source catalog
    catalog = UnifiedPaintCatalog(cache_dir=args.cache_dir)
    catalog.load_all_sources(force_download=args.force_download)

    if args.csv:
        process_csv(
            csv_path=args.csv,
            catalog=catalog,
            dry_run=args.dry_run,
            threshold=args.threshold,
            unmatched_file=args.unmatched,
        )
    else:
        process_database(
            db_path=db_path,
            catalog=catalog,
            dry_run=args.dry_run,
            threshold=args.threshold,
            unmatched_file=args.unmatched,
            verbose=args.verbose,
        )


if __name__ == "__main__":
    main()
