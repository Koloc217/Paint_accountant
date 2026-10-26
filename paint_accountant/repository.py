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


def register_functions(conn: sqlite3.Connection) -> None:
    conn.create_function(
        "py_lower", 1, lambda s: s.lower() if isinstance(s, str) else s, deterministic=True
    )


def _like_pattern(text: str) -> str:
    escaped = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ======================= storages =======================

_STORAGE_SELECT = """
    SELECT s.id, s.name, s.nfc_tag, s.at_hand,
           count(p.id)                 AS paint_count,
           coalesce(sum(p.quantity), 0) AS pot_count
    FROM storage s
    LEFT JOIN paint p ON p.storage_id = s.id
"""


def _storage_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["at_hand"] = bool(d["at_hand"])
    return d


def list_storages(conn: sqlite3.Connection, at_hand: bool | None = None) -> list[dict]:
    sql = _STORAGE_SELECT
    params: list[Any] = []
    if at_hand is not None:
        sql += " WHERE s.at_hand = ?"
        params.append(int(at_hand))
    sql += " GROUP BY s.id ORDER BY s.at_hand DESC, py_lower(s.name)"
    return [_storage_row(r) for r in conn.execute(sql, params)]


def get_storage(conn: sqlite3.Connection, storage_id: int) -> dict:
    row = conn.execute(_STORAGE_SELECT + " WHERE s.id = ? GROUP BY s.id", (storage_id,)).fetchone()
    if row is None:
        raise NotFound(f"storage {storage_id} not found")
    return _storage_row(row)


def _check_storage_name_free(conn: sqlite3.Connection, name: str, exclude_id: int | None = None) -> None:
    row = conn.execute(
        "SELECT id FROM storage WHERE py_lower(name) = py_lower(?) AND id IS NOT ?",
        (name, exclude_id),
    ).fetchone()
    if row:
        raise Conflict(f"storage named '{name}' already exists")


def create_storage(conn: sqlite3.Connection, data: dict) -> dict:
    with conn:
        _check_storage_name_free(conn, data["name"])
        storage_id = conn.execute(
            "INSERT INTO storage (name, nfc_tag, at_hand) VALUES (?, ?, ?)",
            (data["name"], data.get("nfc_tag"), int(data.get("at_hand", False))),
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
        if "at_hand" in changes:
            changes["at_hand"] = int(bool(changes["at_hand"]))
        sets = ", ".join(f"{col} = ?" for col in changes)
        conn.execute(
            f"UPDATE storage SET {sets}, updated_at = datetime('now') WHERE id = ?",
            [*changes.values(), storage_id],
        )
    return get_storage(conn, storage_id)


def delete_storage(conn: sqlite3.Connection, storage_id: int) -> None:
    """Paints that were inside keep existing, just without a storage."""
    with conn:
        if conn.execute("DELETE FROM storage WHERE id = ?", (storage_id,)).rowcount == 0:
            raise NotFound(f"storage {storage_id} not found")


# ======================= tags =======================

def list_tags(conn: sqlite3.Connection) -> list[dict]:
    tags = [dict(r) for r in conn.execute("SELECT id, name, color FROM tag ORDER BY id")]
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
    return [dict(r) for r in conn.execute(sql, params)]


def get_manufacturer(conn: sqlite3.Connection, manufacturer_id: int) -> dict:
    row = conn.execute(_MANUFACTURER_SELECT + " WHERE m.id = ? GROUP BY m.id", (manufacturer_id,)).fetchone()
    if row is None:
        raise NotFound(f"manufacturer {manufacturer_id} not found")
    return dict(row)


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

    result = []
    for r in rows:
        storage = None
        if r["storage_id"] is not None:
            storage = {"id": r["storage_id"], "name": r["storage_name"], "at_hand": bool(r["storage_at_hand"])}
        result.append(
            {
                "id": r["id"],
                "name": r["name"],
                "manufacturer": r["manufacturer_name"],
                "manufacturer_id": r["manufacturer_id"],
                "code": r["code"],
                "barcode": r["barcode"],
                "hex": r["hex"],
                "quantity": r["quantity"],
                "storage": storage,
                "tags": tags.get(r["id"], []),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return result


_PAINT_SELECT = """
    SELECT p.*, m.name AS manufacturer_name, s.name AS storage_name, s.at_hand AS storage_at_hand
    FROM paint p
    JOIN manufacturer m ON m.id = p.manufacturer_id
    LEFT JOIN storage s ON s.id = p.storage_id
"""


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
) -> list[dict]:
    where: list[str] = []
    params: list[Any] = []
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
        where.append("p.storage_id IS NULL")
    elif storage_id is not None:
        where.append("p.storage_id = ?")
        params.append(storage_id)
    for tag_id in sorted(set(tag_ids or [])):  # all given tags must be present
        where.append("EXISTS (SELECT 1 FROM paint_tag pt WHERE pt.paint_id = p.id AND pt.tag_id = ?)")
        params.append(tag_id)
    if at_hand is not None:
        where.append("ifnull(s.at_hand, 0) = ?")
        params.append(int(at_hand))
    if in_stock is not None:
        where.append("p.quantity > 0" if in_stock else "p.quantity = 0")

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


def create_paint(conn: sqlite3.Connection, data: dict) -> dict:
    with conn:
        _check_refs(conn, data.get("storage_id"), data.get("tag_ids"))
        manufacturer_id = _resolve_manufacturer(conn, data.get("manufacturer_id"), data.get("manufacturer"))
        _check_paint_unique(conn, manufacturer_id, data["name"], data.get("code"))
        _check_barcode_free(conn, data.get("barcode"))
        paint_id = conn.execute(
            """INSERT INTO paint (name, manufacturer_id, code, barcode, hex, quantity, storage_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                data["name"],
                manufacturer_id,
                data.get("code"),
                data.get("barcode"),
                data.get("hex"),
                data.get("quantity", 1),
                data.get("storage_id"),
            ),
        ).lastrowid
        _set_tags(conn, paint_id, data.get("tag_ids") or [])
    return get_paint(conn, paint_id)


def update_paint(conn: sqlite3.Connection, paint_id: int, changes: dict) -> dict:
    current = get_paint(conn, paint_id)
    for field in ("name", "quantity"):
        if field in changes and changes[field] is None:
            raise InvalidReference(f"{field} cannot be empty")
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if not changes["name"]:
            raise InvalidReference("name cannot be empty")

    tag_ids = changes.pop("tag_ids", None)
    manufacturer_id = changes.pop("manufacturer_id", None)
    manufacturer_name = changes.pop("manufacturer", None)
    with conn:
        _check_refs(conn, changes.get("storage_id"), tag_ids)
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
