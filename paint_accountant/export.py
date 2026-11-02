"""Export of the paint list as Markdown text — to read, to keep, or to hand to a chatbot.

Two levels:

short — for a neural network: "here is what I have, can I mix/substitute or do I
        have to buy?". Only paints in stock (unless a stock filter says otherwise),
        grouped by manufacturer, one line each: name (code) — #hex — tags. No header.

full  — everything the catalog knows: totals, a summary of storages, then per paint
        the code, color, tags, where the pots are (storage ×N, ✋ = at hand) and the
        barcode; paints that ran out are listed in their own section at the end.

Both follow the filters of the paint list, so "only metallics" or "only box 3" can be
exported as well; the full one then says the list is a part of the catalog.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import repository as repo

LEVELS = ("short", "full")

TEXT = {
    "ru": {
        "full_title": "Каталог красок",
        "full_intro": "Выгрузка {date}. Красок: {paints}, в наличии {in_stock}, банок {pots}.",
        "legend": "Строка: название (код) — цвет hex — теги — где лежит (хранилище ×банок, ✋ — под рукой) — штрихкод.",
        "part": "Это часть каталога, выбранная фильтром.",
        "storages": "Хранилища",
        "storage_line": "{name} — красок {paints}, банок {pots}",
        "no_storage": "без хранилища",
        "out": "Нет в наличии",
        "barcode": "штрихкод",
        "empty": "Подходящих красок нет.",
    },
    "en": {
        "full_title": "Paint catalog",
        "full_intro": "Exported {date}. Paints: {paints}, in stock {in_stock}, pots {pots}.",
        "legend": "Line: name (code) — hex color — tags — where (storage ×pots, ✋ = at hand) — barcode.",
        "part": "This is a part of the catalog picked by a filter.",
        "storages": "Storages",
        "storage_line": "{name} — paints {paints}, pots {pots}",
        "no_storage": "no storage",
        "out": "Out of stock",
        "barcode": "barcode",
        "empty": "No matching paints.",
    },
}


def _sorted(paints: list[dict]) -> list[dict]:
    return sorted(paints, key=lambda p: (p["manufacturer"].casefold(), p["name"].casefold(), p["code"] or ""))


def _base(p: dict) -> list[str]:
    """name (code), #hex, tags — the parts every line starts with."""
    name = p["name"] + (f" ({p['code']})" if p["code"] else "")
    parts = [name]
    if p["hex"]:
        parts.append(p["hex"])
    if p["tags"]:
        parts.append(", ".join(tg["name"] for tg in p["tags"]))
    return parts


def _where(p: dict, tx: dict) -> str:
    out = []
    for loc in p["locations"]:
        if not loc["quantity"]:
            continue
        st = loc["storage"]
        label = st["name"] if st else tx["no_storage"]
        mark = " ✋" if st and st.get("at_hand") else ""
        out.append(f"{label} ×{loc['quantity']}{mark}")
    return ", ".join(out)


def _by_maker(paints: list[dict], line) -> list[str]:
    lines: list[str] = []
    maker = None
    for p in paints:
        if p["manufacturer"] != maker:
            maker = p["manufacturer"]
            lines += ["", f"## {maker}"]
        lines.append("- " + line(p))
    return lines


def _short(paints: list[dict]) -> list[str]:
    """Just the list: no header, so it can be pasted into any question as is."""
    lines = _by_maker(paints, lambda p: " — ".join(_base(p)))
    return lines[1:]  # no blank line before the first manufacturer


def _full(paints: list[dict], tx: dict, filtered: bool) -> list[str]:
    have = [p for p in paints if p["quantity"]]
    gone = [p for p in paints if not p["quantity"]]
    lines = [
        f"# {tx['full_title']}", "",
        tx["full_intro"].format(date=datetime.now().strftime("%Y-%m-%d"), paints=len(paints),
                                in_stock=len(have), pots=sum(p["quantity"] for p in paints)),
    ]
    if filtered:
        lines.append(tx["part"])
    lines.append(tx["legend"])

    # storages: how many paints and pots each one holds (within what is exported)
    stats: dict[tuple, list] = {}
    for p in have:
        for loc in p["locations"]:
            if not loc["quantity"]:
                continue
            st = loc["storage"]
            key = (0, st["name"].casefold(), st["id"]) if st else (1, "", 0)
            row = stats.setdefault(key, [st["name"] + (" ✋" if st.get("at_hand") else "") if st else tx["no_storage"], 0, 0])
            row[1] += 1
            row[2] += loc["quantity"]
    if stats:
        lines += ["", f"## {tx['storages']}"]
        lines += ["- " + tx["storage_line"].format(name=n, paints=k, pots=q) for _, (n, k, q) in sorted(stats.items())]

    def line(p: dict) -> str:
        parts = _base(p)
        where = _where(p, tx)
        if where:
            parts.append(where)
        if p["barcode"]:
            parts.append(f"{tx['barcode']} {p['barcode']}")
        return " — ".join(parts)

    lines += _by_maker(have, line)
    if gone:
        lines += ["", f"## {tx['out']}"]
        lines += ["- " + " — ".join([f"{p['manufacturer']} {_base(p)[0]}", *_base(p)[1:]] +
                                    ([f"{tx['barcode']} {p['barcode']}"] if p["barcode"] else []))
                  for p in gone]
    return lines


def paints_markdown(conn: sqlite3.Connection, level: str = "short", lang: str = "ru", **filters) -> str:
    """The paint list as Markdown. `filters` are those of repository.list_paints;
    any filter given marks the text as a part of the catalog."""
    tx = TEXT.get(lang, TEXT["ru"])
    # False means "not set" for switch-like filters, but in_stock=False is a real choice
    filters = {k: v for k, v in filters.items()
               if v is not None and v != [] and v != "" and (v is not False or k == "in_stock")}
    filtered = bool(filters)
    if level == "short" and "in_stock" not in filters:
        filters["in_stock"] = True  # a model should pick from what is on the shelf
    filters.pop("sort", None)
    paints = _sorted(repo.list_paints(conn, **filters))
    if level == "full":
        lines = _full(paints, tx, filtered)
    else:
        lines = _short(paints)
    if not paints:
        lines += ["", tx["empty"]]
    return "\n".join(lines).strip("\n") + "\n"
