"""Change a storage's number (the id in its link /#/storage/<id>), e.g. to match an NFC tag.

Stop the app first, then from the app folder:

    python tools/storage_id.py          list storages with their numbers
    python tools/storage_id.py 2 1      storage 2 becomes 1 (if 1 is taken, the two swap)

A copy of the database is saved next to it before anything is changed.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paint_accountant import db  # noqa: E402
from paint_accountant.config import load_settings  # noqa: E402


def renumber(conn: sqlite3.Connection, old: int, new: int) -> bool:
    """Storage `old` gets id `new`; a storage already at `new` gets `old`. True if they swapped."""
    names = dict(conn.execute("SELECT id, name FROM storage").fetchall())
    if old not in names:
        raise SystemExit(f"Хранилища №{old} нет")
    swap = new in names
    temp = max(names) + 1

    def move(a: int, b: int) -> None:
        conn.execute("UPDATE storage SET id = ? WHERE id = ?", (b, a))
        conn.execute("UPDATE storage SET copies_storage_id = ? WHERE copies_storage_id = ?", (b, a))
        conn.execute("UPDATE paint_location SET storage_id = ? WHERE storage_id = ?", (b, a))

    conn.execute("PRAGMA foreign_keys = OFF")
    with conn:
        if swap:
            move(new, temp)
            move(old, new)
            move(temp, old)
        else:
            move(old, new)
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise SystemExit(f"Что-то не сошлось: {problems}")
    return swap


def main(argv: list[str]) -> None:
    settings = load_settings()
    if not settings.db_path.is_file():
        raise SystemExit(f"Базы нет: {settings.db_path}")
    conn = sqlite3.connect(settings.db_path)
    if not argv:
        for sid, name in conn.execute("SELECT id, name FROM storage ORDER BY id"):
            print(f"{sid:>4}  {name}")
        return
    if len(argv) != 2 or not all(a.isdigit() and int(a) > 0 for a in argv):
        raise SystemExit(__doc__)
    old, new = map(int, argv)
    if old == new:
        return
    backup = settings.db_path.with_name(f"{settings.db_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    conn.close()
    shutil.copy2(settings.db_path, backup)
    conn = sqlite3.connect(settings.db_path)
    names = dict(conn.execute("SELECT id, name FROM storage").fetchall())
    swapped = renumber(conn, old, new)
    print(f"«{names[old]}»: №{old} → №{new}")
    if swapped:
        print(f"«{names[new]}»: №{new} → №{old}")
    print(f"Копия базы до изменений: {backup}")


if __name__ == "__main__":
    main(sys.argv[1:])
