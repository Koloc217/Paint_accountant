"""Tidy text typed or pasted by the user before it is saved."""

from __future__ import annotations

import re

# characters nobody sees but that break sorting and comparison: zero-width spaces and
# joiners, the byte-order mark, soft hyphen, control codes
_INVISIBLE = re.compile("[\u200b-\u200f\u2060\ufeff\u00ad\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value):
    """' \u200bNuln   Oil ' -> 'Nuln Oil': no invisible characters, no spaces at the ends,
    one space between words (tabs, line breaks and non-breaking spaces count as spaces).
    Anything that is not text is returned as it is."""
    if not isinstance(value, str):
        return value
    return " ".join(_INVISIBLE.sub("", value).split())
