"""Merging candidates that several sources found.

Sources disagree about URLs for the same event — Devpost links its own
subdomain, MLH links mlh.com — so URL dedup cannot catch a cross-source
duplicate. Titles can, imperfectly, and imperfect merging is handled by
preferring the record that carries the most structured data.
"""

from __future__ import annotations

import re
from typing import Iterable

from core.models import Candidate

_NOISE_RE = re.compile(r"[^a-z0-9]+")


def title_key(title: str) -> str:
    """Fold a title for comparison: 'Hack the 6ix!' and 'hack the 6ix' match."""
    return _NOISE_RE.sub(" ", title.casefold()).strip()


def _richness(candidate: Candidate) -> int:
    """How much structured data a record carries. Higher wins a merge."""
    return sum(
        bool(value)
        for value in (
            candidate.starts_at,
            candidate.ends_at,
            candidate.format_raw,
            candidate.location_raw,
            candidate.dates_raw,
            candidate.website_url,
        )
    )


def merge(candidates: Iterable[Candidate]) -> tuple[Candidate, ...]:
    """Collapse the same event seen in several sources into one record.

    Matching is on the exact folded title only. Near-misses like "DeerHacks V"
    and "DeerHacks V (2026)" stay separate on purpose — silently fusing two
    events that merely look alike is worse than showing both.
    """
    best: dict[str, Candidate] = {}
    seen_in: dict[str, list[str]] = {}

    for candidate in candidates:
        key = title_key(candidate.title)
        if not key:
            continue
        sources = seen_in.setdefault(key, [])
        if candidate.source not in sources:
            sources.append(candidate.source)

        incumbent = best.get(key)
        if incumbent is None or _richness(candidate) > _richness(incumbent):
            best[key] = candidate

    return tuple(
        winner.model_copy(
            update={"also_in": tuple(s for s in seen_in[key] if s != winner.source)}
        )
        for key, winner in best.items()
    )
