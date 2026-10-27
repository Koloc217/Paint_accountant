"""Data access: all SQL lives here, the API layer only calls these functions.

Text comparisons for search and duplicate checks go through `py_lower`
(Python's str.lower registered in SQLite), because SQLite's own LIKE and
NOCASE only fold ASCII letters — Cyrillic names would otherwise be
case-sensitive.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class InvalidReference(Exception):
    pass


class DuplicateAtHand(Exception):
    """Adding a pot to an "at hand" storage that already holds this paint — needs confirmation."""

    def __init__(self, storage: dict, have: int, suggest: dict | None):
        super().__init__(f"this paint is already in '{storage['name']}' ({have}); confirm with allow_duplicate")
        self.storage = storage
        self.have = have
        self.suggest = suggest


def register_functions(conn: sqlite3.Connection) -> None:
    conn.create_function(
        "py_lower", 1, lambda s: s.lower() if isinstance(s, str) else s, deterministic=True
    )


def _like_pattern(text: str) -> str:
    escaped = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _paint_key(name: str) -> str:
    return " ".join((name or "").lower().split())


def _unique_counts(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict[Any, int]:
    """How many different paints each group has; `sql` yields (grp, manufacturer_id, name, code).

    The same paint entered twice (same manufacturer and name) counts once. Same name with
    different codes are different paints (Vallejo "Black" 70.950 and 72.051); an entry
    without a code joins the others of that name.
    """
    codes: dict[Any, dict[tuple, set]] = {}
    for grp, maker_id, name, code in conn.execute(sql, params):
        per_name = codes.setdefault(grp, {}).setdefault((maker_id, _paint_key(name)), set())
        code = _paint_key(code or "").replace(" ", "")
        if code:
            per_name.add(code)
    return {grp: sum(max(1, len(c)) for c in names.values()) for grp, names in codes.items()}


def _set_unique_counts(rows: list[dict], conn: sqlite3.Connection, sql: str) -> list[dict]:
    counts = _unique_counts(conn, sql)
    for r in rows:
        r["paint_count"] = counts.get(r["id"], 0)
    return rows


_STORAGE_PAINTS = """SELECT l.storage_id, p.manufacturer_id, p.name, p.code
                     FROM paint_location l JOIN paint p ON p.id = l.paint_id"""
_MANUFACTURER_PAINTS = "SELECT manufacturer_id, manufacturer_id, name, code FROM paint"
_TAG_PAINTS = """SELECT pt.tag_id, p.manufacturer_id, p.name, p.code
                 FROM paint_tag pt JOIN paint p ON p.id = pt.paint_id"""


# ======================= settings =======================

def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    """Part of the caller's transaction: call inside `with conn:`."""
    if value is None:
        conn.execute("DELETE FROM setting WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO setting (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


# ======================= storages =======================

_STORAGE_SELECT = """
    SELECT s.id, s.name, s.nfc_tag, s.at_hand, s.is_default, s.copies_storage_id,
           c.name                          AS copies_storage_name,
           count(l.paint_id)               AS paint_count,
           coalesce(sum(l.quantity), 0)    AS pot_count
    FROM storage s
    LEFT JOIN storage c ON c.id = s.copies_storage_id
    LEFT JOIN paint_location l ON l.storage_id = s.id
"""


def _storage_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["at_hand"] = bool(d["at_hand"])
    d["is_default"] = bool(d["is_default"])
    d["copies_storage"] = (
        {"id": d["copies_storage_id"], "name": d["copies_storage_name"]} if d["copies_storage_id"] else None
    )
    del d["copies_storage_name"]
    return d


def list_storages(conn: sqlite3.Connection, at_hand: bool | None = None) -> list[dict]:
    sql = _STORAGE_SELECT
    params: list[Any] = []
    if at_hand is not None:
        sql += " WHERE s.at_hand = ?"
        params.append(int(at_hand))
    sql += " GROUP BY s.id ORDER BY s.is_default DESC, s.at_hand DESC, py_lower(s.name)"
    return _set_unique_counts([_storage_row(r) for r in conn.execute(sql, params)], conn, _STORAGE_PAINTS)


def get_storage(conn: sqlite3.Connection, storage_id: int) -> dict:
    row = conn.execute(_STORAGE_SELECT + " WHERE s.id = ? GROUP BY s.id", (storage_id,)).fetchone()
    if row is None:
        raise NotFound(f"storage {storage_id} not found")
    return _set_unique_counts([_storage_row(row)], conn, _STORAGE_PAINTS)[0]


def default_storage_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM storage WHERE is_default = 1").fetchone()
    return row["id"] if row else None


def _check_storage_name_free(conn: sqlite3.Connection, name: str, exclude_id: int | None = None) -> None:
    row = conn.execute(
        "SELECT id FROM storage WHERE py_lower(name) = py_lower(?) AND id IS NOT ?",
        (name, exclude_id),
    ).fetchone()
    if row:
        raise Conflict(f"storage named '{name}' already exists")


def _check_copies_target(conn: sqlite3.Connection, storage_id: int | None, copies_id: int | None) -> None:
    if copies_id is None:
        return
    if copies_id == storage_id:
        raise InvalidReference("a storage can't be its own copies storage")
    if conn.execute("SELECT 1 FROM storage WHERE id = ?", (copies_id,)).fetchone() is None:
        raise InvalidReference(f"storage {copies_id} does not exist")


def create_storage(conn: sqlite3.Connection, data: dict) -> dict:
    with conn:
        _check_storage_name_free(conn, data["name"])
        _check_copies_target(conn, None, data.get("copies_storage_id"))
        if data.get("is_default"):
            conn.execute("UPDATE storage SET is_default = 0 WHERE is_default = 1")
        storage_id = conn.execute(
            "INSERT INTO storage (name, nfc_tag, at_hand, is_default, copies_storage_id) VALUES (?, ?, ?, ?, ?)",
            (
                data["name"],
                data.get("nfc_tag"),
                int(data.get("at_hand", False)),
                int(bool(data.get("is_default"))),
                data.get("copies_storage_id"),
            ),
        ).lastrowid
    return get_storage(conn, storage_id)


def update_storage(conn: sqlite3.Connection, storage_id: int, changes: dict) -> dict:
    get_storage(conn, storage_id)
    if not changes:
        return get_storage(conn, storage_id)
    with conn:
        if "name" in changes:
            if changes["name"] is None or not changes["name"].strip():
                raise InvalidReference("name must not be empty")
            changes["name"] = changes["name"].strip()
            _check_storage_name_free(conn, changes["name"], exclude_id=storage_id)
        if "copies_storage_id" in changes:
            _check_copies_target(conn, storage_id, changes["copies_storage_id"])
        for flag in ("at_hand", "is_default"):
            if flag in changes:
                changes[flag] = int(bool(changes[flag]))
        if changes.get("is_default"):
            conn.execute("UPDATE storage SET is_default = 0 WHERE is_default = 1 AND id != ?", (storage_id,))
        sets = ", ".join(f"{col} = ?" for col in changes)
        conn.execute(
            f"UPDATE storage SET {sets}, updated_at = datetime('now') WHERE id = ?",
            [*changes.values(), storage_id],
        )
    return get_storage(conn, storage_id)


def delete_storage(conn: sqlite3.Connection, storage_id: int) -> None:
    """Pots that were inside stay in the catalog, without a storage.
    The default storage can't be deleted — make another one default first."""
    s = get_storage(conn, storage_id)
    if s["is_default"]:
        raise Conflict("the default storage can't be deleted; make another storage default first")
    with conn:
        for row in conn.execute(
            "SELECT paint_id, quantity FROM paint_location WHERE storage_id = ?", (storage_id,)
        ).fetchall():
            _change(conn, row["paint_id"], None, row["quantity"])
        conn.execute("DELETE FROM paint_location WHERE storage_id = ?", (storage_id,))
        conn.execute("UPDATE storage SET copies_storage_id = NULL WHERE copies_storage_id = ?", (storage_id,))
        conn.execute("DELETE FROM storage WHERE id = ?", (storage_id,))


# ======================= tags =======================

def list_tags(conn: sqlite3.Connection) -> list[dict]:
    tags = [dict(r) for r in conn.execute(
        """SELECT t.id, t.name, t.color, count(pt.paint_id) AS paint_count
           FROM tag t LEFT JOIN paint_tag pt ON pt.tag_id = t.id
           GROUP BY t.id ORDER BY t.id"""
    )]
    _set_unique_counts(tags, conn, _TAG_PAINTS)
    keywords: dict[int, list[str]] = {}
    for r in conn.execute("SELECT tag_id, keyword FROM tag_keyword ORDER BY keyword"):
        keywords.setdefault(r["tag_id"], []).append(r["keyword"])
    for t in tags:
        t["keywords"] = keywords.get(t["id"], [])
    return tags


def get_tag(conn: sqlite3.Connection, tag_id: int) -> dict:
    for t in list_tags(conn):
        if t["id"] == tag_id:
            return t
    raise NotFound(f"tag {tag_id} not found")


def _check_tag_name_free(conn: sqlite3.Connection, name: str, exclude_id: int | None = None) -> None:
    row = conn.execute(
        "SELECT id FROM tag WHERE py_lower(name) = py_lower(?) AND id IS NOT ?",
        (name, exclude_id),
    ).fetchone()
    if row:
        raise Conflict(f"tag named '{name}' already exists")


def create_tag(conn: sqlite3.Connection, data: dict) -> dict:
    name = data["name"].strip()
    with conn:
        _check_tag_name_free(conn, name)
        tag_id = conn.execute(
            "INSERT INTO tag (name, color) VALUES (?, ?)", (name, data["color"])
        ).lastrowid
    return get_tag(conn, tag_id)


def update_tag(conn: sqlite3.Connection, tag_id: int, changes: dict) -> dict:
    get_tag(conn, tag_id)
    changes = {k: v for k, v in changes.items() if v is not None}
    if "name" in changes:
        changes["name"] = changes["name"].strip()
    if changes:
        with conn:
            if "name" in changes:
                _check_tag_name_free(conn, changes["name"], exclude_id=tag_id)
            sets = ", ".join(f"{col} = ?" for col in changes)
            conn.execute(f"UPDATE tag SET {sets} WHERE id = ?", [*changes.values(), tag_id])
    return get_tag(conn, tag_id)


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> None:
    with conn:
        if conn.execute("DELETE FROM tag WHERE id = ?", (tag_id,)).rowcount == 0:
            raise NotFound(f"tag {tag_id} not found")


# ======================= manufacturers =======================

_MANUFACTURER_SELECT = """
    SELECT m.id, m.name, count(p.id) AS paint_count
    FROM manufacturer m
    LEFT JOIN paint p ON p.manufacturer_id = m.id
"""


def list_manufacturers(conn: sqlite3.Connection, q: str | None = None) -> list[dict]:
    sql = _MANUFACTURER_SELECT
    params: list[Any] = []
    if q and q.strip():
        sql += " WHERE py_lower(m.name) LIKE ? ESCAPE '\\'"
        params.append(_like_pattern(q.strip()))
    sql += " GROUP BY m.id ORDER BY py_lower(m.name)"
    return _set_unique_counts([dict(r) for r in conn.execute(sql, params)], conn, _MANUFACTURER_PAINTS)


def get_manufacturer(conn: sqlite3.Connection, manufacturer_id: int) -> dict:
    row = conn.execute(_MANUFACTURER_SELECT + " WHERE m.id = ? GROUP BY m.id", (manufacturer_id,)).fetchone()
    if row is None:
        raise NotFound(f"manufacturer {manufacturer_id} not found")
    return _set_unique_counts([dict(row)], conn, _MANUFACTURER_PAINTS)[0]


def _find_manufacturer(conn: sqlite3.Connection, name: str, exclude_id: int | None = None) -> int | None:
    row = conn.execute(
        "SELECT id FROM manufacturer WHERE py_lower(name) = py_lower(?) AND id IS NOT ?",
        (name.strip(), exclude_id),
    ).fetchone()
    return row["id"] if row else None


def create_manufacturer(conn: sqlite3.Connection, data: dict) -> dict:
    name = data["name"].strip()
    with conn:
        if _find_manufacturer(conn, name) is not None:
            raise Conflict(f"manufacturer '{name}' already exists")
        manufacturer_id = conn.execute("INSERT INTO manufacturer (name) VALUES (?)", (name,)).lastrowid
    return get_manufacturer(conn, manufacturer_id)


def update_manufacturer(conn: sqlite3.Connection, manufacturer_id: int, changes: dict) -> dict:
    get_manufacturer(conn, manufacturer_id)
    name = (changes.get("name") or "").strip()
    if name:
        with conn:
            if _find_manufacturer(conn, name, exclude_id=manufacturer_id) is not None:
                raise Conflict(f"manufacturer '{name}' already exists")
            conn.execute("UPDATE manufacturer SET name = ? WHERE id = ?", (name, manufacturer_id))
    return get_manufacturer(conn, manufacturer_id)


def delete_manufacturer(conn: sqlite3.Connection, manufacturer_id: int) -> None:
    m = get_manufacturer(conn, manufacturer_id)
    if m["paint_count"]:
        raise Conflict(f"manufacturer '{m['name']}' still has {m['paint_count']} paints")
    with conn:
        conn.execute("DELETE FROM manufacturer WHERE id = ?", (manufacturer_id,))


def _resolve_manufacturer(conn: sqlite3.Connection, manufacturer_id: int | None, name: str | None) -> int:
    """By id (must exist) or by name (found ignoring case, created if new)."""
    if manufacturer_id is not None:
        if conn.execute("SELECT 1 FROM manufacturer WHERE id = ?", (manufacturer_id,)).fetchone() is None:
            raise InvalidReference(f"manufacturer {manufacturer_id} does not exist")
        return manufacturer_id
    name = (name or "").strip()
    if not name:
        raise InvalidReference("manufacturer is required")
    found = _find_manufacturer(conn, name)
    if found is not None:
        return found
    return conn.execute("INSERT INTO manufacturer (name) VALUES (?)", (name,)).lastrowid


# ======================= paints =======================

def _check_refs(conn: sqlite3.Connection, storage_id: int | None, tag_ids: Iterable[int] | None) -> None:
    if storage_id is not None:
        if conn.execute("SELECT 1 FROM storage WHERE id = ?", (storage_id,)).fetchone() is None:
            raise InvalidReference(f"storage {storage_id} does not exist")
    if tag_ids:
        ids = sorted(set(tag_ids))
        placeholders = ",".join("?" * len(ids))
        found = {r[0] for r in conn.execute(f"SELECT id FROM tag WHERE id IN ({placeholders})", ids)}
        missing = [i for i in ids if i not in found]
        if missing:
            raise InvalidReference(f"tags do not exist: {missing}")


def _check_paint_unique(
    conn: sqlite3.Connection, manufacturer_id: int, name: str, code: str | None, exclude_id: int | None = None
) -> None:
    row = conn.execute(
        """SELECT id FROM paint
           WHERE manufacturer_id = ?
             AND py_lower(name) = py_lower(?)
             AND py_lower(ifnull(code, '')) = py_lower(ifnull(?, ''))
             AND id IS NOT ?""",
        (manufacturer_id, name, code, exclude_id),
    ).fetchone()
    if row:
        raise Conflict(
            f"paint '{name}'{' ' + code if code else ''} of this manufacturer already exists "
            f"(id {row['id']}); change its quantity instead"
        )


def _check_barcode_free(conn: sqlite3.Connection, barcode: str | None, exclude_id: int | None = None) -> None:
    if not barcode:
        return
    row = conn.execute(
        "SELECT p.id, p.name FROM paint p WHERE p.barcode = ? AND p.id IS NOT ?", (barcode, exclude_id)
    ).fetchone()
    if row:
        raise Conflict(f"barcode {barcode} already belongs to paint '{row['name']}' (id {row['id']})")


def find_paint_by_barcode(conn: sqlite3.Connection, barcode: str) -> dict | None:
    rows = conn.execute(_PAINT_SELECT + " WHERE p.barcode = ?", (barcode,)).fetchall()
    return _assemble_paints(conn, rows)[0] if rows else None


def _set_tags(conn: sqlite3.Connection, paint_id: int, tag_ids: Iterable[int]) -> None:
    conn.execute("DELETE FROM paint_tag WHERE paint_id = ?", (paint_id,))
    conn.executemany(
        "INSERT INTO paint_tag (paint_id, tag_id) VALUES (?, ?)",
        [(paint_id, t) for t in sorted(set(tag_ids))],
    )


def _locations(conn: sqlite3.Connection, paint_ids: list[int]) -> dict[int, list[dict]]:
    placeholders = ",".join("?" * len(paint_ids))
    result: dict[int, list[dict]] = {}
    for r in conn.execute(
        f"""SELECT l.paint_id, l.quantity, s.id, s.name, s.at_hand, s.is_default
            FROM paint_location l LEFT JOIN storage s ON s.id = l.storage_id
            WHERE l.paint_id IN ({placeholders})
            ORDER BY s.id IS NULL, s.is_default DESC, s.at_hand DESC, py_lower(s.name)""",
        paint_ids,
    ):
        storage = None
        if r["id"] is not None:
            storage = {"id": r["id"], "name": r["name"], "at_hand": bool(r["at_hand"]), "is_default": bool(r["is_default"])}
        result.setdefault(r["paint_id"], []).append({"storage": storage, "quantity": r["quantity"]})
    return result


def _assemble_paints(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    tags: dict[int, list[dict]] = {}
    for r in conn.execute(
        f"""SELECT pt.paint_id, t.id, t.name, t.color
            FROM paint_tag pt JOIN tag t ON t.id = pt.tag_id
            WHERE pt.paint_id IN ({placeholders}) ORDER BY t.id""",
        ids,
    ):
        tags.setdefault(r["paint_id"], []).append({"id": r["id"], "name": r["name"], "color": r["color"]})
    locations = _locations(conn, ids)

    result = []
    for r in rows:
        locs = locations.get(r["id"], [])
        result.append(
            {
                "id": r["id"],
                "uuid": r["uuid"],
                "name": r["name"],
                "manufacturer": r["manufacturer_name"],
                "manufacturer_id": r["manufacturer_id"],
                "code": r["code"],
                "barcode": r["barcode"],
                "hex": r["hex"],
                "quantity": sum(loc["quantity"] for loc in locs),
                "locations": locs,
                "tags": tags.get(r["id"], []),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return result


_PAINT_SELECT = """
    SELECT p.*, m.name AS manufacturer_name
    FROM paint p
    JOIN manufacturer m ON m.id = p.manufacturer_id
"""

_TOTAL = "(SELECT coalesce(sum(quantity), 0) FROM paint_location WHERE paint_id = p.id)"


def list_paints(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    manufacturer: str | None = None,
    manufacturer_id: int | None = None,
    storage_id: int | None = None,
    no_storage: bool = False,
    tag_ids: list[int] | None = None,
    at_hand: bool | None = None,
    in_stock: bool | None = None,
    barcode: str | None = None,
    uuids: list[str] | None = None,
) -> list[dict]:
    where: list[str] = []
    params: list[Any] = []
    if uuids:
        keys = sorted({u.strip().strip("{}").lower() for u in uuids if u.strip()})
        where.append(f"p.uuid IN ({','.join('?' * len(keys))})")
        params += keys
    if q and q.strip():
        where.append(
            "(py_lower(p.name) LIKE ? ESCAPE '\\' OR py_lower(m.name) LIKE ? ESCAPE '\\'"
            " OR py_lower(ifnull(p.code, '')) LIKE ? ESCAPE '\\')"
        )
        params += [_like_pattern(q.strip())] * 3
    if manufacturer_id is not None:
        where.append("p.manufacturer_id = ?")
        params.append(manufacturer_id)
    elif manufacturer:
        where.append("py_lower(m.name) = py_lower(?)")
        params.append(manufacturer.strip())
    if no_storage:
        where.append("EXISTS (SELECT 1 FROM paint_location l WHERE l.paint_id = p.id AND l.storage_id IS NULL)")
    elif storage_id is not None:
        where.append("EXISTS (SELECT 1 FROM paint_location l WHERE l.paint_id = p.id AND l.storage_id = ?)")
        params.append(storage_id)
    for tag_id in sorted(set(tag_ids or [])):  # all given tags must be present
        where.append("EXISTS (SELECT 1 FROM paint_tag pt WHERE pt.paint_id = p.id AND pt.tag_id = ?)")
        params.append(tag_id)
    if at_hand is not None:
        hand = ("EXISTS (SELECT 1 FROM paint_location l JOIN storage s ON s.id = l.storage_id"
                " WHERE l.paint_id = p.id AND s.at_hand = 1)")
        where.append(hand if at_hand else f"NOT {hand}")
    if in_stock is not None:
        where.append(f"{_TOTAL} > 0" if in_stock else f"{_TOTAL} = 0")
    if barcode:
        where.append("p.barcode = ?")
        params.append(barcode.strip())

    sql = _PAINT_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY py_lower(m.name), py_lower(p.name), p.id"
    return _assemble_paints(conn, conn.execute(sql, params).fetchall())


def get_paint(conn: sqlite3.Connection, paint_id: int) -> dict:
    rows = conn.execute(_PAINT_SELECT + " WHERE p.id = ?", (paint_id,)).fetchall()
    if not rows:
        raise NotFound(f"paint {paint_id} not found")
    return _assemble_paints(conn, rows)[0]


def get_paint_by_uuid(conn: sqlite3.Connection, paint_uuid: str) -> dict:
    """The permanent id other apps keep. Upper case and braces are accepted."""
    key = paint_uuid.strip().strip("{}").lower()
    rows = conn.execute(_PAINT_SELECT + " WHERE p.uuid = ?", (key,)).fetchall()
    if not rows:
        raise NotFound(f"paint {paint_uuid} not found")
    return _assemble_paints(conn, rows)[0]


# ---------- pots: where they lie ----------

AUTO = object()  # "no storage given": use the default storage and its copies rule


def _qty(conn: sqlite3.Connection, paint_id: int, storage_id: int | None) -> int:
    row = conn.execute(
        "SELECT quantity FROM paint_location WHERE paint_id = ? AND storage_id IS ?", (paint_id, storage_id)
    ).fetchone()
    return row["quantity"] if row else 0


def _change(conn: sqlite3.Connection, paint_id: int, storage_id: int | None, delta: int) -> None:
    current = _qty(conn, paint_id, storage_id)
    new = current + delta
    if new < 0:
        raise Conflict("not enough pots there")
    if current == 0:
        if new:
            conn.execute(
                "INSERT INTO paint_location (paint_id, storage_id, quantity) VALUES (?, ?, ?)",
                (paint_id, storage_id, new),
            )
    elif new == 0:
        conn.execute("DELETE FROM paint_location WHERE paint_id = ? AND storage_id IS ?", (paint_id, storage_id))
    else:
        conn.execute(
            "UPDATE paint_location SET quantity = ? WHERE paint_id = ? AND storage_id IS ?",
            (new, paint_id, storage_id),
        )


def _storage_brief(conn: sqlite3.Connection, storage_id: int | None) -> dict | None:
    if storage_id is None:
        return None
    r = conn.execute("SELECT id, name, at_hand FROM storage WHERE id = ?", (storage_id,)).fetchone()
    return {"id": r["id"], "name": r["name"], "at_hand": bool(r["at_hand"])}


def _add_pots(conn: sqlite3.Connection, paint_id: int, count: int, target: Any = AUTO,
              allow_duplicate: bool = False) -> dict:
    """Put `count` pots of a paint into one storage and say where they went.

    No storage given -> the default storage. Any storage may hold several pots of a
    paint, but for an "at hand" storage a second pot needs allow_duplicate — otherwise
    DuplicateAtHand is raised, suggesting the storage's copies storage instead.
    The answer also lists storages that held this paint while the target didn't
    (to warn "you already have it in Box 1").
    """
    if target is AUTO:
        target = default_storage_id(conn)
    else:
        _check_refs(conn, target, None)

    have = _qty(conn, paint_id, target)
    if target is not None and not allow_duplicate and have + count > 1:
        row = conn.execute("SELECT at_hand, copies_storage_id FROM storage WHERE id = ?", (target,)).fetchone()
        if row["at_hand"]:
            raise DuplicateAtHand(_storage_brief(conn, target), have, _storage_brief(conn, row["copies_storage_id"]))

    already_in = []
    if target is not None and have == 0:
        for r in conn.execute(
            "SELECT storage_id, quantity FROM paint_location WHERE paint_id = ? AND storage_id IS NOT ?",
            (paint_id, target),
        ).fetchall():
            already_in.append({"storage": _storage_brief(conn, r["storage_id"]), "quantity": r["quantity"]})

    _change(conn, paint_id, target, +count)
    return {
        "action": "added",
        "placed": [{"storage": _storage_brief(conn, target), "count": count}],
        "already_in": already_in,
    }


def add_pots(conn: sqlite3.Connection, paint_id: int, count: int = 1, storage_id: Any = AUTO,
             allow_duplicate: bool = False) -> dict:
    get_paint(conn, paint_id)
    with conn:
        notice = _add_pots(conn, paint_id, count, storage_id, allow_duplicate)
        conn.execute("UPDATE paint SET updated_at = datetime('now') WHERE id = ?", (paint_id,))
    return {"paint": get_paint(conn, paint_id), "notice": notice}


def remove_pots(conn: sqlite3.Connection, paint_id: int, count: int = 1, storage_id: Any = AUTO) -> dict:
    """Take pots away (used up). Without a storage: from the default storage first,
    then other "at hand" ones, then the rest. If an "at hand" storage runs out while
    pots are left elsewhere, the answer offers where to take one from."""
    paint = get_paint(conn, paint_id)
    if count > paint["quantity"]:
        raise Conflict("there are fewer pots than that")
    with conn:
        taken: dict[Any, int] = {}
        if storage_id is AUTO:
            for loc in paint["locations"]:  # already ordered: default, at hand, others, no storage
                if count == 0:
                    break
                sid = loc["storage"]["id"] if loc["storage"] else None
                n = min(count, loc["quantity"])
                _change(conn, paint_id, sid, -n)
                taken[sid] = n
                count -= n
        else:
            if _qty(conn, paint_id, storage_id) < count:
                raise Conflict("not enough pots in that storage")
            _change(conn, paint_id, storage_id, -count)
            taken[storage_id] = count
        conn.execute("UPDATE paint SET updated_at = datetime('now') WHERE id = ?", (paint_id,))

    paint = get_paint(conn, paint_id)
    refill_from = []
    emptied_hand = [sid for sid in taken if sid is not None and _storage_brief(conn, sid)["at_hand"]
                    and _qty(conn, paint_id, sid) == 0]
    if emptied_hand:
        refill_from = [loc for loc in paint["locations"] if not (loc["storage"] and loc["storage"]["at_hand"])]
    return {
        "paint": paint,
        "notice": {
            "action": "removed",
            "taken": [{"storage": _storage_brief(conn, sid), "count": n} for sid, n in taken.items()],
            "emptied": [_storage_brief(conn, sid) for sid in emptied_hand],
            "refill_from": refill_from,
        },
    }


def move_pots(conn: sqlite3.Connection, paint_id: int, from_storage_id: int | None,
              to_storage_id: int | None, count: int = 1) -> dict:
    """Move pots between storages as asked — the copies rule doesn't apply here."""
    get_paint(conn, paint_id)
    if from_storage_id == to_storage_id:
        raise InvalidReference("from and to are the same")
    _check_refs(conn, to_storage_id, None)
    with conn:
        if _qty(conn, paint_id, from_storage_id) < count:
            raise Conflict("not enough pots in that storage")
        _change(conn, paint_id, from_storage_id, -count)
        _change(conn, paint_id, to_storage_id, +count)
        conn.execute("UPDATE paint SET updated_at = datetime('now') WHERE id = ?", (paint_id,))
    return {
        "paint": get_paint(conn, paint_id),
        "notice": {
            "action": "moved",
            "from": _storage_brief(conn, from_storage_id),
            "to": _storage_brief(conn, to_storage_id),
            "count": count,
        },
    }


# ---------- create / update / delete ----------

def create_paint(conn: sqlite3.Connection, data: dict, storage_id: Any = AUTO,
                 allow_duplicate: bool = False) -> dict:
    """`storage_id` AUTO puts the pots into the default storage; an explicit id
    (or None for "no storage") puts them there. Several pots into an "at hand"
    storage need allow_duplicate."""
    with conn:
        _check_refs(conn, None if storage_id is AUTO else storage_id, data.get("tag_ids"))
        manufacturer_id = _resolve_manufacturer(conn, data.get("manufacturer_id"), data.get("manufacturer"))
        _check_paint_unique(conn, manufacturer_id, data["name"], data.get("code"))
        _check_barcode_free(conn, data.get("barcode"))
        paint_id = conn.execute(
            """INSERT INTO paint (name, manufacturer_id, code, barcode, hex) VALUES (?, ?, ?, ?, ?)""",
            (data["name"], manufacturer_id, data.get("code"), data.get("barcode"), data.get("hex")),
        ).lastrowid
        _set_tags(conn, paint_id, data.get("tag_ids") or [])
        notice = None
        quantity = data.get("quantity", 1)
        if quantity:
            notice = _add_pots(conn, paint_id, quantity, storage_id, allow_duplicate)
    return {**get_paint(conn, paint_id), "notice": notice}


def update_paint(conn: sqlite3.Connection, paint_id: int, changes: dict) -> dict:
    """Name, manufacturer, code, barcode, color, tags. Pots change via add/remove/move."""
    current = get_paint(conn, paint_id)
    if "name" in changes:
        if changes["name"] is None or not changes["name"].strip():
            raise InvalidReference("name cannot be empty")
        changes["name"] = changes["name"].strip()

    tag_ids = changes.pop("tag_ids", None)
    manufacturer_id = changes.pop("manufacturer_id", None)
    manufacturer_name = changes.pop("manufacturer", None)
    with conn:
        _check_refs(conn, None, tag_ids)
        if manufacturer_id is not None or manufacturer_name is not None:
            changes["manufacturer_id"] = _resolve_manufacturer(conn, manufacturer_id, manufacturer_name)
        if {"name", "manufacturer_id", "code"} & changes.keys():
            _check_paint_unique(
                conn,
                changes.get("manufacturer_id", current["manufacturer_id"]),
                changes.get("name", current["name"]),
                changes["code"] if "code" in changes else current["code"],
                exclude_id=paint_id,
            )
        if changes.get("barcode"):
            _check_barcode_free(conn, changes["barcode"], exclude_id=paint_id)
        if changes:
            sets = ", ".join(f"{col} = ?" for col in changes)
            conn.execute(
                f"UPDATE paint SET {sets}, updated_at = datetime('now') WHERE id = ?",
                [*changes.values(), paint_id],
            )
        if tag_ids is not None:
            _set_tags(conn, paint_id, tag_ids)
            if not changes:
                conn.execute("UPDATE paint SET updated_at = datetime('now') WHERE id = ?", (paint_id,))
    return get_paint(conn, paint_id)


def delete_paint(conn: sqlite3.Connection, paint_id: int) -> None:
    with conn:
        if conn.execute("DELETE FROM paint WHERE id = ?", (paint_id,)).rowcount == 0:
            raise NotFound(f"paint {paint_id} not found")
