"""Turn a raw listing location string into a deterministic verdict.

The AI extracts whatever the page said — "Toronto, ON (in-person)",
"hybrid — online + Vaughan", "Remote". This module decides two things about
that string, with no LLM involved:

  1. the event format (in-person / online / hybrid), from keywords;
  2. whether it names a place inside the target area, from `core.gta`.

`gta` mode matches against the GTA definition; `general` mode matches against
a location from config. Same code path, different matcher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping, Sequence

from core.gta import GTA_AREA_NAME, PLACE_KEYS, Place, normalize

EventFormat = Literal["in-person", "online", "hybrid", "unknown"]

#: What the code could conclude about the location, which is not always
#: "yes" or "no":
#:   in-area     — it names a place inside the target area
#:   elsewhere   — it names a place we know is outside it
#:   online-only — no venue at all
#:   unclear     — it names something (usually a venue) code cannot place,
#:                 so the listing needs a reader. This is the AI's queue.
LocationStatus = Literal["in-area", "elsewhere", "online-only", "unclear"]

# Keywords are written in normalized form ("in-person" normalizes to "in person").
ONLINE_WORDS = ("online", "virtual", "remote", "worldwide", "anywhere", "digital", "zoom")
IN_PERSON_WORDS = ("in person", "onsite", "on site", "irl", "face to face")
HYBRID_WORDS = ("hybrid",)

#: GTA names that are also ordinary words, street names or institutions.
#: Matching these bare invites false positives like "Brock University,
#: St. Catharines" reading as Durham Region.
AMBIGUOUS_BARE_KEYS = frozenset(
    {"king", "york", "brock", "milton", "aurora", "sutton", "sharon",
     "maple", "concord", "newcastle", "bronte", "weston", "acton"}
)

#: If an ambiguous name is followed by one of these, it is not the municipality.
NON_PLACE_FOLLOWERS = frozenset(
    {"university", "college", "school", "campus", "street", "st", "road", "rd",
     "avenue", "ave", "boulevard", "blvd", "drive", "dr", "lane", "way",
     "hall", "building", "tower", "institute", "academy"}
)

#: Countries and US states that share names with GTA municipalities —
#: "Milton Keynes, UK", "Toronto, Ohio", "London, England". A listing that
#: names one of these and no Canadian marker is not in the GTA.
FOREIGN_MARKERS = frozenset(
    {"uk", "united kingdom", "england", "scotland", "wales", "ireland",
     "usa", "united states", "america", "india", "germany", "france", "spain",
     "italy", "netherlands", "portugal", "poland", "singapore", "australia",
     "new zealand", "japan", "china", "korea", "brazil", "mexico", "argentina",
     "nigeria", "kenya", "ghana", "egypt", "uae", "dubai", "israel", "turkey",
     "united arab emirates", "saudi arabia", "south africa", "morocco",
     "tunisia", "pakistan", "bangladesh", "sri lanka", "nepal", "malaysia",
     "indonesia", "philippines", "vietnam", "thailand", "taiwan", "hong kong",
     "belgium", "switzerland", "austria", "czechia", "denmark", "finland",
     "greece", "hungary", "romania", "ukraine", "serbia", "croatia", "chile",
     "colombia", "peru", "sweden", "norway",
     "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
     "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
     "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
     "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
     "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
     "new mexico", "new york", "north carolina", "north dakota", "ohio",
     "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
     "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
     "washington", "west virginia", "wisconsin", "wyoming"}
)

#: Canadian places that are definitely not in the GTA. Naming one is enough
#: to rule a listing out without opening its page.
ELSEWHERE_CANADIAN_MARKERS = frozenset(
    {"waterloo", "kitchener", "cambridge", "guelph", "hamilton", "ottawa",
     "london", "windsor", "kingston", "barrie", "peterborough", "brantford",
     "st catharines", "niagara falls", "niagara", "sudbury", "thunder bay",
     "north bay", "sarnia", "belleville", "orillia", "collingwood",
     "montreal", "quebec", "laval", "gatineau", "sherbrooke",
     "vancouver", "victoria", "surrey", "burnaby", "kelowna",
     "calgary", "edmonton", "winnipeg", "saskatoon", "regina",
     "halifax", "moncton", "fredericton", "st johns", "charlottetown",
     "whitehorse", "yellowknife", "iqaluit"}
)

#: If any of these appear, the listing is Canadian and FOREIGN_MARKERS is moot.
CANADIAN_MARKERS = frozenset({"canada", "canadian", "ontario", "ont", "on"})

#: Dropped when deriving matchable names from a `general` mode location.
PROVINCE_AND_COUNTRY_TOKENS = frozenset(
    {"on", "ont", "ontario", "canada", "ca", "qc", "quebec", "bc", "ab", "mb",
     "sk", "ns", "nb", "nl", "pe", "yt", "nt", "nu", "us", "usa", "united states"}
)

_NEXT_WORD_RE = re.compile(r"\s+([a-z0-9]+)")


@dataclass(frozen=True, slots=True)
class AreaMatcher:
    """The set of place names that count as "inside the area" for one run."""

    name: str
    keys: tuple[str, ...]  # normalized, longest-first
    index: Mapping[str, Place]
    #: True only for the GTA matcher, which is the one area this project has
    #: real geographic knowledge of: which namesakes are foreign, and which
    #: Canadian cities are definitely outside it. A `general` mode area is
    #: just a name the user supplied, so it can never say "elsewhere" —
    #: only "in-area" or "unclear".
    has_gazetteer: bool = False


@dataclass(frozen=True, slots=True)
class LocationVerdict:
    """What the code concluded about one listing's location string."""

    raw: str
    event_format: EventFormat
    places: tuple[Place, ...]
    status: LocationStatus
    area: str
    reason: str

    @property
    def in_area(self) -> bool:
        return self.status == "in-area"

    @property
    def needs_a_reader(self) -> bool:
        """True when only the AI can settle this one — a venue, not a city."""
        return self.status == "unclear"


def _longest_first(keys: Sequence[str]) -> tuple[str, ...]:
    """Order keys so "north york" is tried before "york"."""
    return tuple(sorted(keys, key=lambda k: (-len(k.split()), -len(k), k)))


GTA_MATCHER = AreaMatcher(
    GTA_AREA_NAME, _longest_first(tuple(PLACE_KEYS)), PLACE_KEYS, has_gazetteer=True
)


def target_matcher(location: str, nearby: Sequence[str] = ()) -> AreaMatcher:
    """Build a matcher for `general` mode from a location like "Waterloo, ON".

    Splits off province/country qualifiers and keeps the place names. Extra
    `nearby` names let a caller widen the area without touching this code.
    """
    area = location.strip()
    if not area:
        raise ValueError("general mode needs a non-empty location")

    candidates = [part.strip() for part in area.split(",")] + [n.strip() for n in nearby]
    index: dict[str, Place] = {}
    for candidate in candidates:
        key = normalize(candidate)
        if not key or key in PROVINCE_AND_COUNTRY_TOKENS:
            continue
        index.setdefault(key, Place(candidate, area, "city"))

    if not index:
        raise ValueError(f"{location!r} has no place name in it, only qualifiers")

    frozen = MappingProxyType(index)
    return AreaMatcher(area, _longest_first(tuple(frozen)), frozen)


def matcher_for(mode: str, location: str | None = None, nearby: Sequence[str] = ()) -> AreaMatcher:
    """Pick the matcher for a run's mode. This is the whole of `general` mode."""
    if mode == "gta":
        return GTA_MATCHER
    if mode != "general":
        raise ValueError(f"unknown mode {mode!r}")
    if not (location or "").strip():
        raise ValueError("general mode needs a location")
    return target_matcher(location, nearby)  # type: ignore[arg-type]


def _mentions(text: str, words: frozenset[str]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def names_a_foreign_place(text: str) -> bool:
    """True if normalized `text` names a foreign country/state and no Canadian one."""
    return _mentions(text, FOREIGN_MARKERS) and not _mentions(text, CANADIAN_MARKERS)


def names_a_known_outside_place(text: str) -> bool:
    """True if `text` names somewhere we know is outside the GTA."""
    return names_a_foreign_place(text) or _mentions(text, ELSEWHERE_CANADIAN_MARKERS)


def _is_false_positive(text: str, end: int, key: str) -> bool:
    """True if an ambiguous bare name is really a street or an institution."""
    if key not in AMBIGUOUS_BARE_KEYS:
        return False
    following = _NEXT_WORD_RE.match(text, end)
    return bool(following) and following.group(1) in NON_PLACE_FOLLOWERS


def find_places(raw: str, matcher: AreaMatcher = GTA_MATCHER) -> tuple[Place, ...]:
    """Every in-area place named in `raw`, in the order they appear.

    Longer names win: "North York" matches as North York, and the "York"
    inside it is not counted a second time.
    """
    text = normalize(raw)
    if not text:
        return ()
    if matcher.has_gazetteer and names_a_foreign_place(text):
        return ()

    claimed: list[tuple[int, int]] = []
    hits: list[tuple[int, Place]] = []

    for key in matcher.keys:
        for match in re.finditer(rf"\b{re.escape(key)}\b", text):
            start, end = match.span()
            if any(start < claimed_end and claimed_start < end for claimed_start, claimed_end in claimed):
                continue  # inside a longer name we already matched
            if _is_false_positive(text, end, key):
                continue
            claimed.append((start, end))
            place = matcher.index[key]
            if place not in [p for _, p in hits]:
                hits.append((start, place))

    return tuple(place for _, place in sorted(hits, key=lambda pair: pair[0]))


def detect_format(raw: str, has_place: bool) -> EventFormat:
    """Read the event format off the string's keywords."""
    text = normalize(raw)
    if not text:
        return "unknown"

    def mentions(words: Sequence[str]) -> bool:
        return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)

    is_online, is_in_person = mentions(ONLINE_WORDS), mentions(IN_PERSON_WORDS)

    if mentions(HYBRID_WORDS):
        return "hybrid"
    if is_online and (is_in_person or has_place):
        # "online + Vaughan" is a hybrid even without the word.
        return "hybrid"
    if is_online:
        return "online"
    if is_in_person or has_place:
        return "in-person"
    return "unknown"


def classify(raw: str, matcher: AreaMatcher = GTA_MATCHER) -> LocationVerdict:
    """Classify one listing's location string against the target area.

    The important outcome is `unclear`. Devpost listings very often show a
    venue — "Sheridan College Hazel McCallion Campus", "Bur Oak Secondary
    School" — rather than a city. Code cannot place those, but they are not
    rejections: they are the listings worth opening and reading.
    """
    places = find_places(raw, matcher)
    event_format = detect_format(raw, has_place=bool(places))
    text = normalize(raw)

    if places:
        named = ", ".join(place.name for place in places)
        return LocationVerdict(raw, event_format, places, "in-area", matcher.name,
                               f"names {named} in {matcher.name}")

    if matcher.has_gazetteer and names_a_known_outside_place(text):
        return LocationVerdict(raw, event_format, (), "elsewhere", matcher.name,
                               f"names somewhere outside {matcher.name}")

    if event_format == "online":
        return LocationVerdict(raw, event_format, (), "online-only", matcher.name,
                               "online only — no venue given")

    if not text:
        return LocationVerdict(raw, event_format, (), "unclear", matcher.name,
                               "no location given — needs the listing read")

    return LocationVerdict(raw, event_format, (), "unclear", matcher.name,
                           f"{raw.strip()!r} is not a place name — needs the listing read")


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    matcher = GTA_MATCHER
    if args and args[0].startswith("--area="):
        matcher = target_matcher(args.pop(0).split("=", 1)[1])

    samples = args or [
        "Toronto, ON (in-person)",
        "hybrid — online + Vaughan",
        "Online",
        "Waterloo, ON",
        "Mississauga, Ontario, Canada",
        "Brock University, St. Catharines",
    ]
    print(f"Area: {matcher.name}\n")
    for sample in samples:
        verdict = classify(sample, matcher)
        flag = "IN " if verdict.in_area else "OUT"
        print(f"  {flag}  {sample!r}")
        print(f"        {verdict.event_format:<10} {verdict.reason}")
