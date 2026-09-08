"""Spotting listings that look like the same event.

`core.merge` fuses records only when titles match exactly, because fusing
"DeerHacks V" with "DeerHacks V (2026)" would be a guess. This module makes
the softer call: those two are *probably* related, so flag both and let a
person decide. Nothing is removed.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from core.models import Candidate

#: Stripped when reducing a title to its series stem.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_EDITION_RE = re.compile(r"\b([ivx]+|\d+(st|nd|rd|th))\b", re.I)
_NOISE_RE = re.compile(r"[^a-z0-9]+")

#: Titles that announce their own unreliability. Real example from Devpost:
#: "NOT DeerHacks V (OLD DEVPOST; DO NOT USE)".
_SUSPECT_RE = re.compile(
    r"\b(do not use|don't use|deprecated|old devpost|test event|ignore this|duplicate)\b",
    re.I,
)


def series_stem(title: str) -> str:
    """Reduce a title to the series it belongs to.

    "DeerHacks V (2026)", "DeerHacks 2023" and "DeerHacks" all stem to
    "deerhacks", which is what makes them worth looking at together.
    """
    without_year = _YEAR_RE.sub(" ", title.casefold())
    without_edition = _EDITION_RE.sub(" ", without_year)
    return " ".join(_NOISE_RE.sub(" ", without_edition).split())


def is_suspect_title(title: str) -> bool:
    """True if the title itself says not to trust the listing."""
    return bool(_SUSPECT_RE.search(title))


def near_duplicates(candidates: Sequence[Candidate]) -> dict[str, tuple[str, ...]]:
    """Map each candidate's key to the other titles sharing its series stem.

    Only groups of two or more appear. Nothing is merged or dropped.
    """
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        stem = series_stem(candidate.title)
        if stem:
            groups.setdefault(stem, []).append(candidate)

    found: dict[str, tuple[str, ...]] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        for member in members:
            others = tuple(m.title for m in members if m.key != member.key)
            if others:
                found[member.key] = others
    return found
