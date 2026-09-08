"""The deterministic layer: code's verdict on every candidate.

Nothing here calls a model. Location is decided by `core.gta` via
`core.location`, dates by `core.dates`, and the config's filters are applied
as plain comparisons. What comes out is a bucketed, flagged list that the
judging step can rank — and that a person can audit line by line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Sequence

from core.config import Config
from core.duplicates import is_suspect_title, near_duplicates
from core.dates import Deadline, parse_date_range, parse_deadline, parse_iso_date
from core.flags import Flag, flag, needs_attention
from core.location import AreaMatcher, LocationVerdict, classify
from core.models import Candidate, Reading

#: primary      — in the area and physically there. The shortlist.
#: online-gta   — organised in the area but run online. Kept, labelled apart.
#: unresolved   — code cannot place it; the listing still needs reading.
#: excluded     — code is confident it does not belong.
Bucket = Literal["primary", "online-gta", "unresolved", "excluded"]

#: Sources say this outright, and they are more reliable than parsing prose.
ENDED_WORDS = frozenset({"ended", "closed", "winners announced"})


@dataclass(frozen=True, slots=True)
class Screened:
    """One candidate after the deterministic layer has had its say."""

    candidate: Candidate
    location: LocationVerdict
    deadline: Deadline
    starts_on: date | None
    ends_on: date | None
    bucket: Bucket
    reasons: tuple[str, ...] = ()
    flags: tuple[Flag, ...] = field(default=())

    @property
    def needs_attention(self) -> bool:
        """True when something here should be looked at by a person."""
        return needs_attention(self.flags)

    @property
    def title(self) -> str:
        return self.candidate.title

    @property
    def is_past(self) -> bool:
        return self.ends_on is not None and self.ends_on < date.today()


def _event_dates(
    candidate: Candidate, today: date
) -> tuple[date | None, date | None]:
    """When the event runs. ISO timestamps win; a date range is parsed."""
    iso_start, iso_end = parse_iso_date(candidate.starts_at), parse_iso_date(candidate.ends_at)
    if iso_start:
        return iso_start, iso_end or iso_start

    # A source that says "Ended" must not have its year rolled into the future.
    ended = candidate.status_raw.strip().casefold() in ENDED_WORDS
    return parse_date_range(candidate.dates_raw, today, assume_future=not ended)


def _best_location(candidate: Candidate, reading: Reading | None) -> str:
    """The page's location if one was read, otherwise the listing's."""
    if reading is not None and reading.location_text:
        return reading.location_text
    return candidate.location_raw


def _best_deadline(
    candidate: Candidate, reading: Reading | None, ends_on: date | None, today: date
) -> Deadline:
    """The stated submission deadline, or the event's last day as a stand-in.

    Falling back to the end date is a real assumption, so it is flagged rather
    than presented as if the page had said it.
    """
    if reading is not None and reading.deadline_text:
        parsed = parse_deadline(reading.deadline_text, today=today)
        if parsed.date is not None:
            return parsed
    if ends_on is not None:
        days = (ends_on - today).days
        status = "passed" if days < 0 else "soon" if days <= 7 else "upcoming"
        return Deadline(raw="", date=ends_on, status=status, days_away=days)
    return Deadline(raw="", date=None, status="unknown", days_away=None)


def _event_format(candidate: Candidate, reading: Reading | None, location: LocationVerdict) -> str:
    """The format, preferring what a source stated outright."""
    if candidate.format_raw:
        return candidate.format_raw
    if reading is not None and reading.facts and reading.facts.event_format != "unstated":
        return reading.facts.event_format
    return location.event_format


def _bucket_for(
    location: LocationVerdict, event_format: str, structured_location: bool = False
) -> tuple[Bucket, str]:
    if location.status == "elsewhere":
        return "excluded", location.reason
    if location.status == "unclear":
        if structured_location:
            # The source gave a full city/region/country and none of it is the
            # target area. Reading the page cannot change that.
            return "excluded", f"structured address is not in {location.area}"
        return "unresolved", location.reason
    if location.status == "online-only":
        return "excluded", "online with no link to the area"
    if event_format == "online":
        return "online-gta", f"{location.reason}, but runs online"
    return "primary", location.reason


def place(
    candidate: Candidate,
    matcher: AreaMatcher,
    reading: Reading | None = None,
) -> tuple[LocationVerdict, Bucket, str]:
    """Decide where a candidate sits on location alone, ignoring dates.

    Shared by the collector's summary and the full screening pass so the two
    can never report different numbers for the same data.
    """
    location = classify(_best_location(candidate, reading), matcher)
    event_format = _event_format(candidate, reading, location)
    bucket, reason = _bucket_for(
        location, event_format,
        structured_location=candidate.location_structured and reading is None,
    )
    return location, bucket, reason


def _flags_for(
    candidate: Candidate,
    reading: Reading | None,
    location: LocationVerdict,
    event_format: str,
    starts_on: date | None,
    ends_on: date | None,
    deadline: Deadline,
    duplicates: dict[str, tuple[str, ...]] | None,
) -> tuple[Flag, ...]:
    """Everything worth saying about this listing that is not a verdict."""
    found: list[Flag] = []

    if reading is not None and not reading.ok:
        found.append(flag("read_failed", f"read failed: {reading.error}"))
    elif reading is None and location.needs_a_reader:
        found.append(flag("not_read"))

    facts = reading.facts if reading and reading.facts else None
    if facts is not None and not facts.eligibility_text:
        found.append(flag("missing_eligibility"))

    if starts_on is None:
        found.append(flag("no_parseable_date",
                          f"could not parse {candidate.dates_raw!r}" if candidate.dates_raw
                          else "the listing gives no dates"))
    if deadline.date is None:
        found.append(flag("no_deadline"))
    elif not deadline.raw:
        found.append(flag("assumed_deadline"))

    if ends_on is not None and deadline.date is not None and deadline.date > ends_on:
        found.append(flag("deadline_after_event",
                          f"deadline {deadline.date} is after the event ends {ends_on}"))

    # "Tagged for a city but actually online" — the case the plan calls out.
    if location.in_area and event_format == "online":
        found.append(flag("online_but_placed",
                          f"names {location.places[0].name if location.places else location.area}"
                          f" but runs online"))

    if (facts is not None and candidate.format_raw
            and facts.event_format != "unstated"
            and facts.event_format != candidate.format_raw):
        found.append(flag("format_conflict",
                          f"source says {candidate.format_raw}, page says {facts.event_format}"))

    if is_suspect_title(candidate.title):
        found.append(flag("suspect_title", f"title reads {candidate.title!r}"))

    others = (duplicates or {}).get(candidate.key)
    if others:
        found.append(flag("possible_duplicate", "also seen as " + "; ".join(others[:3])))

    return tuple(found)


def screen(
    candidate: Candidate,
    config: Config,
    matcher: AreaMatcher,
    reading: Reading | None = None,
    today: date | None = None,
    duplicates: dict[str, tuple[str, ...]] | None = None,
) -> Screened:
    """Run every deterministic check over one candidate."""
    today = today or date.today()

    location, bucket, reason = place(candidate, matcher, reading)
    starts_on, ends_on = _event_dates(candidate, today)
    deadline = _best_deadline(candidate, reading, ends_on, today)
    event_format = _event_format(candidate, reading, location)
    reasons = [reason]
    flags = _flags_for(
        candidate, reading, location, event_format, starts_on, ends_on, deadline, duplicates
    )

    # Config filters. These only ever demote to "excluded" — they never rescue.
    if bucket in ("primary", "online-gta"):
        if config.filters.format != "any" and event_format != config.filters.format:
            bucket = "excluded"
            reasons.append(f"format is {event_format}, filter wants {config.filters.format}")
        elif starts_on is not None and starts_on < today and not config.filters.include_past:
            bucket = "excluded"
            reasons.append(f"already happened ({starts_on})")
        elif starts_on is not None and not _within_months(starts_on, config.filters.timeframe_months, today):
            bucket = "excluded"
            reasons.append(f"starts {starts_on}, beyond the {config.filters.timeframe_months}-month window")
        elif config.filters.themes and not _theme_overlap(candidate, config.filters.themes):
            bucket = "excluded"
            reasons.append(f"no overlap with themes {list(config.filters.themes)}")

    return Screened(
        candidate=candidate,
        location=location,
        deadline=deadline,
        starts_on=starts_on,
        ends_on=ends_on,
        bucket=bucket,
        reasons=tuple(reasons),
        flags=tuple(flags),
    )


def _within_months(when: date, months: int, today: date) -> bool:
    from dateutil.relativedelta import relativedelta

    return when <= today + relativedelta(months=months)


def _theme_overlap(candidate: Candidate, wanted: Sequence[str]) -> bool:
    have = {t.casefold() for t in candidate.themes}
    return any(w.casefold() in h or h in w.casefold() for w in wanted for h in have)


def screen_all(
    candidates: Sequence[Candidate],
    config: Config,
    matcher: AreaMatcher,
    readings: dict[str, Reading] | None = None,
    today: date | None = None,
) -> tuple[Screened, ...]:
    """Screen every candidate, attaching a reading where one exists."""
    readings = readings or {}
    duplicates = near_duplicates(candidates)
    return tuple(
        screen(candidate, config, matcher, readings.get(candidate.key), today, duplicates)
        for candidate in candidates
    )


def by_bucket(screened: Sequence[Screened], bucket: Bucket) -> tuple[Screened, ...]:
    return tuple(s for s in screened if s.bucket == bucket)
