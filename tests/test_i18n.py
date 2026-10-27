"""Every interface text has an English version, with the same {0} placeholders."""

from __future__ import annotations

import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "paint_accountant" / "web"


def _keys() -> set[str]:
    src = (WEB / "app.js").read_text(encoding="utf-8")
    keys = {m.replace("\\'", "'") for m in re.findall(r"\bt\('((?:[^'\\]|\\.)*)'", src)}
    keys |= set(re.findall(r'data-i18n(?:-label)?="([^"]+)"', (WEB / "index.html").read_text(encoding="utf-8")))
    return keys


def _english() -> dict[str, str]:
    src = (WEB / "i18n.js").read_text(encoding="utf-8")
    body = src[src.index("const EN = {") + len("const EN = "):]
    body = body[:body.index("\n};") + 2]
    return json.loads(re.sub(r",\s*}$", "}", body))


def test_every_text_is_translated():
    en = _english()
    missing = sorted(_keys() - en.keys())
    assert not missing, f"no English for: {missing}"


def test_placeholders_match():
    for ru, en in _english().items():
        assert sorted(re.findall(r"\{\d+\}", ru)) == sorted(re.findall(r"\{\d+\}", en)), ru


def test_no_untranslated_russian_left_in_code():
    # Russian text in app.js must go through t(...) — except inside comments and plural forms
    src = (WEB / "app.js").read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/|//[^\n]*", "", src, flags=re.S)
    src = re.sub(r"\bt\('(?:[^'\\]|\\.)*'", "", src)
    src = src.replace("plural(n, 'краска', 'краски', 'красок')", "").replace("plural(n, 'банка', 'банки', 'банок')", "")
    left = [ln.strip() for ln in src.splitlines() if re.search(r"[А-Яа-яЁё]", ln)]
    assert not left, left
