"""Online barcode lookup at UPCitemdb — an optional helper for the new-paint form.

The free "trial" API needs no key: 100 lookups a day and 6 a minute, counted per IP address.
Nothing is sent on its own: a request leaves only when the user presses the button
next to a barcode, and it carries the barcode alone.

The request goes out from the computer running the app. It can be sent through a proxy
(socks5://, socks5h://, http://) or bound to a network interface such as a VPN's "awg0" —
see [lookup] in config.ini. Only the standard library is used, so there is nothing to install.

What comes back is a shop-style title ("Metallic Paint Quicksilver 17ml - Acrylic Brush
Airbrush Modelling Hobby") and a brand; `suggest` turns them into fields of our form.
"""

from __future__ import annotations

import base64
import html
import http.client
import ipaddress
import json
import os
import re
import socket
import sqlite3
import ssl
import struct
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from . import __version__
from . import recognition as rec
from . import repository as repo

API_HOST = "api.upcitemdb.com"
API_PATH = "/prod/trial/lookup"
TIMEOUT = 15  # seconds, for each network step
CACHE_SECONDS = 24 * 3600
CACHE_SIZE = 500
MAX_TITLES = 8  # titles of one item to weigh: its own plus the shops' own
MAX_ANSWER = 2 * 1024 * 1024

BARCODE = re.compile(r"\d{8}|\d{12,14}")  # EAN-8, UPC-A, EAN-13, GTIN-14


class LookupFailed(Exception):
    """The lookup didn't happen. `code` is for programs, the message for people."""

    def __init__(self, code: str, message: str, reset: int | None = None):
        super().__init__(message)
        self.code = code
        self.reset = reset  # unix time when the limit is lifted, if the service told


@dataclass(frozen=True)
class Route:
    """How requests leave this computer."""

    proxy: str | None = None
    interface: str | None = None
    from_env: bool = False  # taken from HTTPS_PROXY / ALL_PROXY, not from config.ini

    @property
    def kind(self) -> str:
        return "proxy" if self.proxy else "interface" if self.interface else "direct"

    @property
    def label(self) -> str:
        """How to name this route to the user — never with the proxy's name and password in it."""
        if self.proxy:
            url = urlsplit(self.proxy if "://" in self.proxy else "http://" + self.proxy)
            port = f":{url.port}" if url.port else ""
            where = f"{url.scheme or 'http'}://{url.hostname or '?'}{port}"
            return f"{where} (from HTTPS_PROXY)" if self.from_env else where
        return self.interface or "no proxy"


def route(settings) -> Route:
    """A proxy set for the app wins; then the interface; then the system-wide proxy variables."""
    if settings.lookup_proxy:
        return Route(proxy=settings.lookup_proxy)
    if settings.lookup_interface:
        return Route(interface=settings.lookup_interface)
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name, "").strip()
        if value:
            return Route(proxy=value, from_env=True)
    return Route()


# ======================= network =======================

def _bind(sock: socket.socket, interface: str | None) -> None:
    """Send through this interface ("awg0") or from this local address ("10.8.0.2")."""
    if not interface:
        return
    try:
        ipaddress.ip_address(interface)
    except ValueError:
        pass
    else:
        sock.bind((interface, 0))
        return
    if not hasattr(socket, "SO_BINDTODEVICE"):
        raise LookupFailed("lookup_config", "an interface name works on Linux only; "
                           "give the interface's IP address or use a proxy")
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b"\0")
    except OSError as exc:
        raise LookupFailed("lookup_config", f"can't use network interface {interface}: {exc.strerror or exc}")


def _connect(host: str, port: int, interface: str | None) -> socket.socket:
    last: Exception | None = None
    for family, kind, proto, _name, addr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(TIMEOUT)
            _bind(sock, interface)
            sock.connect(addr)
            return sock
        except LookupFailed:
            sock.close()
            raise
        except OSError as exc:
            sock.close()
            last = exc
    raise last or OSError(f"no address for {host}")


def _read_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise OSError("the proxy closed the connection")
        data += chunk
    return data


SOCKS_ERRORS = {
    1: "general failure", 2: "not allowed by the proxy's rules", 3: "network unreachable",
    4: "host unreachable", 5: "connection refused", 6: "timed out", 7: "command not supported",
    8: "address type not supported",
}


def _socks5(sock: socket.socket, host: str, port: int, user: str | None, password: str | None, remote_dns: bool) -> None:
    """SOCKS5 CONNECT (RFC 1928), with name/password sign-in (RFC 1929) when given."""
    sock.sendall(b"\x05\x02\x00\x02" if user is not None else b"\x05\x01\x00")
    ver, method = _read_exact(sock, 2)
    if ver != 5:
        raise OSError("this is not a SOCKS5 proxy")
    if method == 2 and user is not None:
        u, p = user.encode(), (password or "").encode()
        sock.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        if _read_exact(sock, 2)[1] != 0:
            raise OSError("the proxy refused the name and password")
    elif method != 0:
        raise OSError("the proxy wants a sign-in method that isn't supported")

    if remote_dns:
        name = host.encode("idna")
        target = b"\x03" + bytes([len(name)]) + name
    else:
        family, _k, _p, _n, addr = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
        target = (b"\x04" if family == socket.AF_INET6 else b"\x01") + socket.inet_pton(family, addr[0])
    sock.sendall(b"\x05\x01\x00" + target + struct.pack(">H", port))
    _ver, reply, _rsv, atyp = _read_exact(sock, 4)
    if reply != 0:
        raise OSError(f"the proxy couldn't connect: {SOCKS_ERRORS.get(reply, f'error {reply}')}")
    _read_exact(sock, {1: 4, 4: 16}.get(atyp) or _read_exact(sock, 1)[0])  # the bound address — not needed
    _read_exact(sock, 2)


def _http_connect(sock: socket.socket, host: str, port: int, user: str | None, password: str | None) -> None:
    lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
    if user is not None:
        token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1)  # byte by byte: nothing after the blank line may be swallowed
        if not chunk or len(head) > 16384:
            raise OSError("the proxy closed the connection")
        head += chunk
    status = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = status.split(" ", 2)
    if len(parts) < 2 or parts[1] != "200":
        raise OSError(f"the proxy answered: {status}")


def parse_proxy(proxy: str) -> tuple[str, str, int, str | None, str | None]:
    """A proxy address as its parts, or LookupFailed saying what is wrong with it."""
    url = urlsplit(proxy if "://" in proxy else "http://" + proxy)
    scheme = url.scheme.lower()
    if scheme not in ("http", "socks5", "socks5h"):
        raise LookupFailed("lookup_config",
                           f"proxy type {scheme}:// isn't supported — use socks5://, socks5h:// or http://")
    try:
        port = url.port or (8080 if scheme == "http" else 1080)
    except ValueError:
        raise LookupFailed("lookup_config", "the proxy's port is not a number")
    if not url.hostname:
        raise LookupFailed("lookup_config", "the proxy address has no host")
    user = unquote(url.username) if url.username is not None else None
    password = unquote(url.password) if url.password is not None else None
    return scheme, url.hostname, port, user, password


def open_tunnel(host: str, port: int, how: Route) -> socket.socket:
    """A plain TCP connection to host:port — direct, through an interface or through a proxy."""
    if not how.proxy:
        return _connect(host, port, how.interface)
    scheme, proxy_host, proxy_port, user, password = parse_proxy(how.proxy)
    sock = _connect(proxy_host, proxy_port, how.interface)
    try:
        if scheme == "http":
            _http_connect(sock, host, port, user, password)
        else:
            _socks5(sock, host, port, user, password, remote_dns=scheme == "socks5h")
    except Exception:
        sock.close()
        raise
    return sock


class _Connection(http.client.HTTPSConnection):
    def __init__(self, host: str, how: Route):
        self._tls = ssl.create_default_context()
        super().__init__(host, 443, timeout=TIMEOUT, context=self._tls)
        self._how = how

    def connect(self) -> None:
        sock = open_tunnel(self.host, self.port, self._how)
        self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)


def check(how: Route) -> None:
    """Can UPCitemdb be reached this way at all? Costs no request of the day's hundred:
    the connection is opened and closed again without asking anything."""
    try:
        sock = open_tunnel(API_HOST, 443, how)
    except LookupFailed:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise LookupFailed("lookup_unreachable", f"{how.label}: {exc}")
    try:
        ssl.create_default_context().wrap_socket(sock, server_hostname=API_HOST)
    except (OSError, ssl.SSLError) as exc:
        raise LookupFailed("lookup_unreachable", f"{how.label}: {exc}")
    finally:
        sock.close()


def fetch(barcode: str, how: Route) -> tuple[int, dict[str, str], Any]:
    """Ask UPCitemdb. Returns the HTTP status, the headers (lowercase names) and the parsed JSON (or None)."""
    conn = _Connection(API_HOST, how)
    try:
        conn.request("GET", f"{API_PATH}?upc={barcode}", headers={
            "Accept": "application/json",
            "User-Agent": f"PaintAccountant/{__version__}",
        })
        res = conn.getresponse()
        body = res.read(MAX_ANSWER)
        headers = {k.lower(): v for k, v in res.getheaders()}
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            data = None
        return res.status, headers, data
    finally:
        conn.close()


# ======================= the answer -> our form =======================

# shop-listing words that are never part of a paint name (on top of the label stopwords)
SHOP_WORDS = {
    "brush", "airbrush", "modelling", "modeling", "hobby", "hobbies", "miniature", "miniatures",
    "mini", "minis", "figure", "figures", "wargame", "wargames", "wargaming", "craft", "crafts",
    "art", "arts", "supplies", "bottle", "dropper", "pot", "jar", "tube", "pack", "pcs", "pc",
    "x", "1x", "scale", "models", "kit", "kits", "tool", "tools", "toy", "toys", "plastic",
    "resin", "by", "from", "single", "unit", "units", "size",
    "warpaints", "3rd", "gen", "generation",  # product lines printed before the name
    "warhammer", "40k", "wargaming", "stock", "all", "official", "genuine", "new",
    "brushes", "brushs",  # a title that sells brushes alongside the paints
    # "GENERAL Series Paints … 17ml Singles" — a shelf of 236 pots, not a colour's name
    "series", "singles", "set", "sets", "collection", "assortment", "bundle", "range",
    "gc", "av",  # how shops shorten Vallejo's Game Color and Acrylicos Vallejo
}

# the generation of a line, however a shop writes it: "3Gen", "3rd Gen", "3rd Generation",
# "Gen 3". Never part of a colour's name, and glued ("3Gen") it is not even a word of its own
GENERATION = re.compile(r"\b\d\s?(?:st|nd|rd|th)?\s?gen(?:eration)?\b|\bgen(?:eration)?\s?\d\b", re.I)

# said of a listing that carries a line rather than a pot
RANGE_PHRASES = ["full range", "complete range", "whole range", "full set",
                 "all colors", "all colours", "color chart", "colour chart"]

# a shop's praise, cut whole so that neither word has to be banned on its own
PUFFERY = ["high quality", "top quality", "best quality", "great value", "brand new"]

# a listing for a whole range rather than for one pot. Looked for in the title as it came,
# so a word that is dropped as shop noise can still say what the listing is
RANGE_HINTS = {"series", "singles", "range", "set", "sets", "collection", "assortment", "bundle"}

# ranges that aren't tags of ours ("Citadel Dry", "Army Painter Spray"): not a colour's name
# either, so they are trimmed off the edges of one just like the kinds we do tag
EXTRA_KINDS = {"dry", "spray", "sprays", "aqueous", "general"}

# "...Pot Paint For Base": the pot is a Technical one, Base is just what it goes over
CROSS_REFERENCE = re.compile(r"\b(?:for|over|under|with|onto|on)\s+(\w+)", re.I)

# a shop's own letters in front of a number: "GAW21-03" is Citadel's 21-03, "val76515"
# and "VJP76515" are Vallejo's 76.515, "GAW9918995020406" is a wholesale number of its own.
# "AKI" and "GUZ" come before the maker's own code rather than before digits:
# "AKIAK11091" is AK11091, "GUZH23" is Mr. Hobby's H-23
RETAILER_SKU = re.compile(r"\b(?:GAW|VAL|VJP?)(?=\d)|\bAKI(?=AK\d)|\bGUZ(?=[A-Z]{1,2}\d)", re.I)

# Vallejo prints "76.515" but shops write "76515"; only worth reading when we already know
# the paint is Vallejo's, since a bare five-digit number proves nothing on its own
VALLEJO_CODE = re.compile(r"\b([2-7]\d)(\d{3})\b")
SEPARATORS = re.compile(r"\s+[-–—|/:]\s+|[|,;:(){}\[\]]")
NUMBER = re.compile(r"\d+([.,]\d+)?")
# an article number rather than a word: five or more digits, with a few letters or dashes
# ("9918995020406", "GAW9918995020406", "70.950"). A paint's name never looks like this.
ARTICLE = re.compile(r"[A-Za-z]{0,4}\d[\d.,\-]{4,}")


# a finish, not a colour: on its own it doesn't name a paint, so "Satin Varnish" keeps
# the word that "Light Grey Wash" drops
FINISHES = {"satin", "matt", "matte", "gloss", "semigloss", "flat", "clear"}


def kind_of(word: str, kinds: set[str]) -> str | None:
    """The kind of paint this word names, if any — "Washes" is the "wash" we tag by."""
    n = rec.norm(word)
    for form in (n, n[:-2] if n.endswith("es") else n, n[:-1] if n.endswith("s") else n):
        if form in kinds:
            return form
    return None


def _is_word(w: str) -> bool:
    """Something that can be part of a name — not a number, an article or a catalogue code."""
    if not rec.norm(w) or NUMBER.fullmatch(w) or ARTICLE.fullmatch(w):
        return False
    # "MC80" is Vallejo's Model Color 80 as a shop writes it; a word with digits that reads
    # as some maker's code is a code, whoever's scheme it belongs to
    return not (any(c.isdigit() for c in w) and rec.find_code(w))


def _text(value: Any, limit: int = 300) -> str:
    """A field of the answer as text. Shops write "Paint&#44; Ancient Copper", and an
    undecoded comma is neither shown nor split on as the comma it is."""
    if not isinstance(value, (str, int, float)):
        return ""
    return " ".join(html.unescape(str(value)).split())[:limit]


def _essentials(item: dict) -> dict[str, Any]:
    """Only what we read, so a cached answer stays small whatever the shops attach to it."""
    kept: dict[str, Any] = {key: _text(item.get(key)) for key in ("title", "brand", "model", "color", "size", "category")}
    offers = item.get("offers") if isinstance(item.get("offers"), list) else []
    kept["offers"] = [{"title": _text(o.get("title"))} for o in offers[:MAX_TITLES] if isinstance(o, dict)]
    return kept


def _find_maker(item: dict, barcode: str, makers: list[dict], conn: sqlite3.Connection) -> dict | None:
    by_name = {rec.norm(m["name"]): m for m in makers}
    brand = rec.norm(_text(item.get("brand")))
    title = f" {rec.norm(_text(item.get('title')))} "
    if brand:
        if brand in by_name:
            return by_name[brand]
        canonical = rec.ALIASES.get(brand)
        if canonical and rec.norm(canonical) in by_name:
            return by_name[rec.norm(canonical)]
        # "Vallejo Acrylics", "The Army Painter ApS": a known name inside the brand; longest first
        for key in sorted(by_name, key=len, reverse=True):
            if len(key) >= 2 and f" {key} " in f" {brand} ":
                return by_name[key]
    # A brand that means nothing to us is not the end of it: it is sometimes plainly wrong
    # ("Upper Deck" on a Vallejo varnish), while the title and the barcode agree with
    # each other. If these find nobody either, the brand is offered as a new manufacturer.
    for key in sorted(by_name, key=len, reverse=True):
        if len(key) >= 4 and f" {key} " in title:
            return by_name[key]
    for alias, canonical in rec.ALIASES.items():
        if f" {alias} " in title and rec.norm(canonical) in by_name:
            return by_name[rec.norm(canonical)]
    return rec._maker_by_barcode(conn, barcode, makers)


def _strip_phrases(text: str, phrases: list[str]) -> str:
    """Cut whole phrases out of the text, whatever the case; longest first ("Green Stuff World"
    before "Green"). Only the letters and digits of a phrase have to match, so "Mr. Hobby"
    also catches "Mr Hobby" and "Mr.Hobby"."""
    for phrase in sorted({p for p in phrases if p}, key=len, reverse=True):
        words = [re.escape(w) for w in re.findall(r"\w+", phrase)]
        if not words:
            continue
        text = re.sub(r"(?<![\w])" + r"\W*".join(words) + r"(?![\w])", " ", text, flags=re.I)
    return text


def clean_title(title: str, maker_names: list[str], code: str | None, tag_words: set[str]) -> str:
    """'Metallic Paint Quicksilver 17ml - Acrylic Brush Airbrush Modelling Hobby' -> 'Quicksilver'.

    The brand, the code and the volume go away; the title is cut at dashes and commas, and the
    first piece that still says something is kept. Words that only tell the kind of paint
    ("Metallic", "Paint", "Shade") are trimmed from its edges — they become tags instead —
    unless nothing else is left ("Black Primer" keeps "Black", a lone "Primer" stays "Primer").
    """
    text, _foreign = rec.latin_only(title)
    text = rec.TRADEMARK.sub(" ", text)
    text = _strip_phrases(text, maker_names)
    if code:
        text = rec.strip_code(text, code)
        text = _strip_phrases(text, [code])
    text = rec.VOLUME.sub(" ", text)
    text = GENERATION.sub(" ", text)
    text = _strip_phrases(text, RANGE_PHRASES + PUFFERY)
    text = re.sub(r"\b\d+([.,]\d+)?\s?(g|gr|oz|cc|mm|cm)\b", " ", text, flags=re.I)

    filler = rec.STOPWORDS | SHOP_WORDS
    best, found, fallback = "", 0, ""  # `found`: how much of the best piece reads as a colour
    for piece in SEPARATORS.split(text):
        # drop filler everywhere, kind-of-paint words only at the edges
        kept = [w for w in piece.replace("#", " ").split() if _is_word(w) and rec.norm(w) not in filler]
        if not kept:
            continue
        core = list(kept)
        while core and kind_of(core[0], tag_words):
            core.pop(0)
        while core and kind_of(core[-1], tag_words):
            # "Satin Varnish" is the paint's name; "Light Grey Wash" is Light Grey in the
            # Wash line. What is left tells them apart: a colour, or only a finish
            rest = core[:-1]
            if not rest or (len(rest) == 1 and rec.norm(rest[0]) in FINISHES):
                break
            core = rest

        # the piece that names a colour rather than a range: "MW: Wash: Light Grey" is not
        # called "MW", and "True Metallic Metal Light Paint, Ancient Copper" is a copper
        kinds_here = len([w for w in core if kind_of(w, tag_words)])
        colour = len(core) - 2 * kinds_here
        if colour > found:
            best, found = rec.clean_name(" ".join(core))[:200], colour
        fallback = fallback or rec.clean_name(" ".join(core or kept))[:200]
    return best or fallback


def tag_words(conn: sqlite3.Connection) -> set[str]:
    """Words that only tell the kind of paint ("shade", "base", "dry") — never a colour's name."""
    return {rec.norm(r["keyword"]) for r in conn.execute("SELECT keyword FROM tag_keyword")} | EXTRA_KINDS


def titles(item: dict) -> list[str]:
    """Every title this listing goes by: the main one, then each shop's own.

    UPCitemdb picks one title for an item, and it is often a listing for the whole range
    ("Citadel Paint Pot - Base, Layer, Contrast & Shade"), while the colour's own name is
    in a shop's title under `offers`. Mind that the free plan often answers with `offers`
    empty and `brand`, `model` and `color` blank, whatever the site shows for that barcode:
    then the one title is all there is, and the manufacturer has to come from it or from
    the barcode itself.
    """
    found, seen = [], set()
    offers = item.get("offers") if isinstance(item.get("offers"), list) else []
    for value in [item.get("title"), *(o.get("title") for o in offers if isinstance(o, dict))]:
        text = _text(value)
        key = rec.norm(RETAILER_SKU.sub(" ", text))
        if key and key not in seen:
            seen.add(key)
            found.append(text)
        if len(found) >= MAX_TITLES:
            break
    return found


def suggest(conn: sqlite3.Connection, barcode: str, item: dict) -> dict[str, Any]:
    """Fields for the paint form out of one UPCitemdb item.

    Of the item's titles, the one that actually names a colour is used — for the name,
    the code and the tags alike, so they can't come from different listings.
    """
    makers = repo.list_manufacturers(conn)
    keywords = [(r["keyword"], r["tag_id"]) for r in conn.execute("SELECT keyword, tag_id FROM tag_keyword")]
    kinds = tag_words(conn)

    brand = _text(item.get("brand"), 100)
    model = RETAILER_SKU.sub(" ", _text(item.get("model"), 50))
    maker = _find_maker(item, barcode, makers, conn)
    # a brand we know under another name is cleaned off the title either way — "The Army
    # Painter" is written "Army Painter" as often as not, and may not be in the catalog yet
    known = rec.ALIASES.get(rec.norm(brand))

    def find_code(title: str) -> str | None:
        """A shop's title can hold any numbers, so only a pattern of this very manufacturer counts."""
        nonlocal maker
        text = f"{model} {title}"  # a shop writes the code in the title, in "model", or in both
        code, code_maker = rec.find_code_with_maker(text, maker["name"] if maker else None)
        if maker:
            if code_maker == maker["name"]:
                return code
            dotless = VALLEJO_CODE.search(text) if maker["name"] == "Vallejo" else None
            return f"{dotless[1]}.{dotless[2]}" if dotless else None
        if code_maker and code_maker == known:
            return code  # the brand and the pattern say the same maker, who is simply new here
        if code_maker in rec.TELLING_CODES:
            found = next((m for m in makers if rec.norm(m["name"]) == rec.norm(code_maker)), None)
            if found:
                maker = found  # a code only this manufacturer uses tells us whose paint it is
                return code
        return None

    names = [brand]
    for canonical in {n for n in (known, maker["name"] if maker else None) if n}:
        names.append(canonical)
        names += [alias for alias, c in rec.ALIASES.items() if rec.norm(c) == rec.norm(canonical)]

    def read(shown: str) -> dict[str, Any]:
        title = RETAILER_SKU.sub(" ", shown)  # the shop's own article is not the maker's
        code = find_code(title)
        name = clean_title(title, names, code, kinds)
        real = {rec.norm(w) for w in name.split() if not kind_of(w, kinds)}
        words = set(rec.norm(title).split())
        said = {k for k in (kind_of(w, kinds) for w in words) if k}
        # a listing of the whole range names no colour of its own — "Base, Layer, Contrast
        # & Shade", "GENERAL Series … Singles" — and its kinds are not this pot's tags
        a_range = not real and (len(said) > 1 or bool(RANGE_HINTS & words))
        # a kind this pot goes over is not a kind this pot is
        for_other = {rec.norm(m.group(1)) for m in CROSS_REFERENCE.finditer(title)} & kinds
        tagged = rec._tag_ids(" ".join(said - for_other), keywords)
        return {"title": shown, "name": "" if a_range else name, "code": code,
                "tag_ids": [] if a_range else tagged, "kinds": len(said - for_other),
                "range": a_range, "real": bool(real)}

    # the best listing names a colour; of those, one with the maker's code, then the one
    # that talks about the fewest kinds ("…Technical Paint" over "…Technical … For Base")
    reads = [read(title) for title in titles(item)] or [read("")]
    best = max(reads, key=lambda r: (r["real"], bool(r["name"]), r["code"] is not None, -r["kinds"]))

    return {
        "name": best["name"] or None,
        "manufacturer": {"id": maker["id"], "name": maker["name"]} if maker else None,
        # a brand that isn't in the manufacturers list yet: the form offers to add it, under
        # the name we know it by, so the next pot of theirs finds it instead of adding a second
        "new_manufacturer": rec.clean_name(known or brand) if brand and not maker else None,
        "code": best["code"],
        "barcode": barcode,
        "tag_ids": best["tag_ids"],
        "title": best["title"],  # the listing these fields came from
        # nothing but listings for the whole range: there is no colour name to take
        "range_only": all(r["range"] or not r["name"] for r in reads),
    }


# ======================= the lookup itself =======================

_cache: dict[str, tuple[float, dict]] = {}

# The last few answers, kept whole and unread, so that what UPCitemdb really sends can be
# looked at when a pot comes out wrong. Kept per barcode, because pressing the button again
# is answered from the day's memory and would otherwise show another pot's answer.
_raw: dict[str, dict[str, Any]] = {}
RAW_KEEP = 10


def last_answer(barcode: str | None = None) -> dict[str, Any] | None:
    """The answer to this barcode, or the newest one of all."""
    if barcode:
        return _raw.get(re.sub(r"[\s-]", "", barcode))
    return next(reversed(_raw.values()), None) if _raw else None


def clear_cache() -> None:
    _cache.clear()
    _raw.clear()


def _int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def lookup(conn: sqlite3.Connection, settings, barcode: str) -> dict[str, Any]:
    """Look the barcode up and turn the answer into form suggestions.

    {"found": False, ...} when UPCitemdb doesn't know it. Raises LookupFailed when the
    request couldn't be made: turned off, no connection, the daily limit is used up.
    """
    if not settings.lookup_enabled:
        raise LookupFailed("lookup_disabled", "online barcode lookup is turned off in Settings")
    barcode = re.sub(r"[\s-]", "", barcode or "")
    if not BARCODE.fullmatch(barcode):
        raise LookupFailed("lookup_bad_barcode", "a barcode is 8, 12, 13 or 14 digits")

    now = time.time()
    hit = _cache.get(barcode)
    if hit and now - hit[0] < CACHE_SECONDS:
        raw, cached = hit[1], True
    else:
        how = route(settings)
        try:
            status, headers, data = fetch(barcode, how)
            if len(_raw) >= RAW_KEEP:
                _raw.pop(next(iter(_raw)))
            _raw[barcode] = {"barcode": barcode, "asked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "status": status, "answer": data}
        except LookupFailed:
            raise
        except (OSError, http.client.HTTPException) as exc:  # includes timeouts and TLS errors
            # a refused connection to the proxy itself is the common one: say whose address it is
            via = {"proxy": f"the proxy {how.label} doesn't answer",
                   "interface": f"no way out through {how.interface}",
                   "direct": "no answer from api.upcitemdb.com"}[how.kind]
            raise LookupFailed("lookup_unreachable", f"{via} ({exc})")
        code = data.get("code") if isinstance(data, dict) else None
        if status == 429 or code in ("EXCEED_LIMIT", "TOO_FAST"):
            too_fast = code == "TOO_FAST"
            raise LookupFailed(
                "lookup_too_fast" if too_fast else "lookup_limit",
                "too many requests in a minute — wait a little" if too_fast
                else "today's free UPCitemdb requests are used up",
                reset=_int(headers.get("x-ratelimit-reset")))
        if status == 404 or code in ("NOT_FOUND", "INVALID_UPC"):
            items: list = []
        elif status == 200 and isinstance(data, dict):
            items = [i for i in data.get("items") or [] if isinstance(i, dict)]
        else:
            message = _text(data.get("message"), 200) if isinstance(data, dict) else ""
            raise LookupFailed("lookup_error", f"UPCitemdb answered {status}" + (f": {message}" if message else ""))
        # one barcode can come back with several listings of the same paint, whose titles differ
        # ("Warhammer Paint: Mephiston Red" vs "… Citadel Base … 21-03"): keep a few to choose
        # from, each cut down to the fields we read so the cache stays small
        raw = {"items": [_essentials(i) for i in items[:5]],
               "remaining": _int(headers.get("x-ratelimit-remaining"))}
        if len(_cache) >= CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[barcode] = (now, raw)
        cached = False

    result: dict[str, Any] = {
        "source": "upcitemdb", "barcode": barcode, "found": bool(raw["items"]),
        "cached": cached, "remaining": raw["remaining"],
    }
    if raw["items"]:
        # the suggestion is built from today's manufacturers and tags, so it is never cached.
        # A real name beats a listing of the whole range ("Citadel Base Paint Pot — All Colors!",
        # whose only word left is the kind of paint), and that beats no name at all.
        kinds = tag_words(conn)

        def worth(suggestion: dict) -> int:
            words = [w for w in (suggestion["name"] or "").split()]
            return 2 if [w for w in words if not kind_of(w, kinds)] else 1 if words else 0

        tries = [(item, suggest(conn, barcode, item)) for item in raw["items"]]
        item, suggestion = max(tries, key=lambda pair: worth(pair[1]))
        result["item"] = {key: item.get(key) or None
                          for key in ("brand", "model", "color", "size", "category")}
        # the listing the fields were taken from, which can be a shop's rather than the main one
        result["item"]["title"] = suggestion.pop("title") or item.get("title") or None
        result["suggestion"] = suggestion
    return result
