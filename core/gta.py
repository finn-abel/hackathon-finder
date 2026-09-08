"""The Greater Toronto Area, defined explicitly.

This is the constant the whole `gta` mode rests on: the five regions and
their municipalities, plus Toronto's six former-city districts. The AI never
guesses geography — it extracts a raw location string and this module decides
whether that place is in the GTA.

To extend: add a Municipality (or an alias) to the right Region below. The
index is built at import time and raises on duplicate names, so a mistake
shows up immediately rather than silently shadowing an existing place.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterator, Literal, Mapping

GTA_AREA_NAME = "Greater Toronto Area"

#: Names that refer to the whole area rather than one place in it.
GTA_ALIASES = ("GTA", "Greater Toronto Area", "Greater Toronto")

PlaceKind = Literal["city", "district", "region", "area"]

_QUALIFIER_RE = re.compile(
    r"^(the\s+)?(regional\s+municipality|city|town|municipality|township|village)\s+of\s+"
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize(name: str) -> str:
    """Fold a place name to its comparison key.

    Strips accents, case, punctuation and civic qualifiers, so "Whitchurch-
    Stouffville", "City of Toronto" and "T.O." all reduce to something stable.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    spaced = _NON_ALNUM_RE.sub(" ", unaccented.casefold()).strip()
    return _QUALIFIER_RE.sub("", spaced).strip()


@dataclass(frozen=True, slots=True)
class Municipality:
    """One city or Toronto district, with the other names listings call it."""

    name: str
    aliases: tuple[str, ...] = ()
    kind: PlaceKind = "city"

    def names(self) -> Iterator[str]:
        yield self.name
        yield from self.aliases


@dataclass(frozen=True, slots=True)
class Region:
    """One of the five GTA regions and the municipalities inside it."""

    name: str
    municipalities: tuple[Municipality, ...]
    aliases: tuple[str, ...] = ()

    def names(self) -> Iterator[str]:
        yield self.name
        yield from self.aliases


@dataclass(frozen=True, slots=True)
class Place:
    """A resolved GTA place: what it is and which region it sits in."""

    name: str
    region: str
    kind: PlaceKind = "city"
    matched_as: str = field(default="", compare=False)


# --- The definition -------------------------------------------------------

TORONTO = Region(
    name="Toronto",
    aliases=("City of Toronto", "T.O.", "The 6ix", "Toronto Ontario"),
    municipalities=(
        Municipality("Toronto", aliases=("Downtown Toronto", "Old Toronto")),
        Municipality("Scarborough", kind="district"),
        Municipality("North York", kind="district"),
        Municipality("Etobicoke", kind="district"),
        Municipality("York", kind="district", aliases=("Weston",)),
        Municipality("East York", kind="district", aliases=("Leaside",)),
    ),
)

PEEL = Region(
    name="Peel",
    aliases=("Peel Region", "Regional Municipality of Peel"),
    municipalities=(
        Municipality("Mississauga", aliases=("Port Credit", "Streetsville", "Erin Mills")),
        Municipality("Brampton", aliases=("Bramalea",)),
        Municipality("Caledon", aliases=("Bolton",)),
    ),
)

YORK = Region(
    name="York Region",
    aliases=("York", "Regional Municipality of York"),
    municipalities=(
        Municipality("Markham", aliases=("Unionville", "Thornhill")),
        Municipality("Vaughan", aliases=("Woodbridge", "Maple", "Concord", "Kleinburg")),
        Municipality("Richmond Hill", aliases=("Oak Ridges",)),
        Municipality("Newmarket"),
        Municipality("Aurora"),
        Municipality("King", aliases=("King City", "Nobleton")),
        Municipality("Whitchurch-Stouffville", aliases=("Stouffville",)),
        Municipality("East Gwillimbury", aliases=("Sharon", "Mount Albert")),
        Municipality("Georgina", aliases=("Keswick", "Sutton")),
    ),
)

DURHAM = Region(
    name="Durham",
    aliases=("Durham Region", "Regional Municipality of Durham"),
    municipalities=(
        Municipality("Pickering"),
        Municipality("Ajax"),
        Municipality("Whitby", aliases=("Brooklin",)),
        Municipality("Oshawa"),
        Municipality("Clarington", aliases=("Bowmanville", "Courtice", "Newcastle")),
        Municipality("Scugog", aliases=("Port Perry",)),
        Municipality("Uxbridge"),
        Municipality("Brock", aliases=("Cannington", "Beaverton")),
    ),
)

HALTON = Region(
    name="Halton",
    aliases=("Halton Region", "Regional Municipality of Halton"),
    municipalities=(
        Municipality("Oakville", aliases=("Bronte",)),
        Municipality("Burlington", aliases=("Aldershot",)),
        Municipality("Milton"),
        Municipality("Halton Hills", aliases=("Georgetown", "Acton")),
    ),
)

GTA_REGIONS: tuple[Region, ...] = (TORONTO, PEEL, YORK, DURHAM, HALTON)


# --- Index ----------------------------------------------------------------


def _build_index() -> Mapping[str, Place]:
    """Map every normalized name and alias to its Place. Raises on conflicts."""
    index: dict[str, Place] = {}

    def add(key_source: str, place: Place, *, allow_shadow: bool = False) -> None:
        key = normalize(key_source)
        if not key:
            raise ValueError(f"{key_source!r} normalizes to an empty key")
        existing = index.get(key)
        if existing is None:
            index[key] = place
            return
        if allow_shadow:
            return  # a municipality of the same name already won this key
        raise ValueError(
            f"Duplicate GTA place name {key_source!r}: claimed by both "
            f"{existing.name} ({existing.region}) and {place.name} ({place.region})"
        )

    # Municipalities and districts first — they are the specific answer.
    for region in GTA_REGIONS:
        for municipality in region.municipalities:
            place = Place(municipality.name, region.name, municipality.kind)
            for name in municipality.names():
                add(name, place)

    # Region names second; a region whose name a municipality already owns
    # (Toronto, York) defers to the municipality.
    for region in GTA_REGIONS:
        place = Place(region.name, region.name, "region")
        for name in region.names():
            add(name, place, allow_shadow=True)

    # The area itself.
    area = Place(GTA_AREA_NAME, GTA_AREA_NAME, "area")
    for name in GTA_ALIASES:
        add(name, area, allow_shadow=True)

    return MappingProxyType(index)


#: Every normalized name/alias in the GTA → the Place it resolves to.
PLACE_KEYS: Mapping[str, Place] = _build_index()


# --- Lookup ---------------------------------------------------------------


def lookup(name: str) -> Place | None:
    """Resolve one place name. Returns None if it is not a GTA place.

    This expects a *name* ("Mississauga", "North York"), not a messy listing
    string — pulling names out of prose is the location classifier's job.
    """
    place = PLACE_KEYS.get(normalize(name))
    if place is None:
        return None
    return Place(place.name, place.region, place.kind, matched_as=name)


def is_in_gta(name: str) -> bool:
    """True if `name` is a GTA municipality, district, region or the area."""
    return normalize(name) in PLACE_KEYS


def region_of(name: str) -> str | None:
    """Which GTA region a place belongs to, or None if it is outside."""
    place = lookup(name)
    return place.region if place else None


def municipalities() -> tuple[Place, ...]:
    """Every canonical municipality and district, region order preserved."""
    return tuple(
        Place(m.name, region.name, m.kind)
        for region in GTA_REGIONS
        for m in region.municipalities
    )


if __name__ == "__main__":
    import sys

    queries = sys.argv[1:]
    if not queries:
        total = len(municipalities())
        print(f"{total} municipalities and districts across {len(GTA_REGIONS)} regions:")
        for region in GTA_REGIONS:
            names = ", ".join(m.name for m in region.municipalities)
            print(f"  {region.name:<12} {names}")
        raise SystemExit(0)

    for query in queries:
        place = lookup(query)
        if place is None:
            print(f"  {query:<24} NOT in the GTA")
        else:
            print(f"  {query:<24} in GTA — {place.name} ({place.kind}, {place.region})")
