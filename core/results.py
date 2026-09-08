"""The `results.json` schema — the artifact everything else reads.

The shape is deliberately three-part per hackathon, mirroring how the value
was produced:

    raw      what a source or page actually said, verbatim
    derived  what code decided from it (location, dates, bucket)
    fit      what the model judged against your criteria

Anyone opening the file can tell which parts were read, which were computed,
and which were judged — and a `legend` block spells out every vocabulary the
file uses, so it needs no external documentation.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from core.config import Config
from core.models import Judgement, Reading
from core.screening import Screened

SCHEMA_VERSION = "1.0"

LEGEND: dict[str, dict[str, str]] = {
    "provenance": {
        "raw": "Verbatim from the listing or its page. No code or model altered it.",
        "derived": "Computed by core.gta, core.location and core.dates. Deterministic.",
        "fit": "Scored by the AI against the run's criteria. The only judgement call.",
    },
    "bucket": {
        "primary": "In the target area and physically there. The shortlist.",
        "online-gta": "Organised in the target area but run online.",
        "unresolved": "Code could not place it; the listing has not been read.",
        "excluded": "Code is confident it does not belong. See derived.reasons.",
    },
    "location_status": {
        "in-area": "Names a place inside the target area.",
        "elsewhere": "Names a place known to be outside it.",
        "online-only": "No venue given at all.",
        "unclear": "Names something code cannot place, usually a venue.",
    },
    "deadline_status": {
        "passed": "Before today.",
        "soon": "Within the next 7 days.",
        "upcoming": "Further out than 7 days.",
        "unknown": "No deadline could be parsed. Never guessed.",
    },
    "fit_score": {
        "5": "Ideal — matches every part of the criteria.",
        "4": "Good — matches the important parts.",
        "3": "Partial — matches some, misses others.",
        "1-2": "Weak — mostly does not match.",
        "0": "Clearly wrong for this person.",
    },
}


class RawFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    location: str = ""
    dates: str = ""
    deadline: str = ""
    status: str = ""
    themes: tuple[str, ...] = ()
    eligibility: str | None = None
    beginner: str | None = None
    was_read: bool = Field(default=False, description="Whether the detail page was opened.")


class DerivedFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    in_area: bool
    area: str
    location_status: str
    places: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    event_format: str = "unknown"
    starts_on: date | None = None
    ends_on: date | None = None
    deadline_date: date | None = None
    deadline_status: str = "unknown"
    days_away: int | None = None
    bucket: str = "unresolved"
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()


class Fit(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: int
    reason: str
    supports: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


class ResultRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int | None = None
    title: str
    url: str
    source: str
    also_in: tuple[str, ...] = ()
    website_url: str = ""
    raw: RawFacts
    derived: DerivedFacts
    fit: Fit | None = None


class RunContext(BaseModel):
    """What produced this file. Enough to reproduce the run."""

    model_config = ConfigDict(frozen=True)

    generated_at: str
    today: date
    mode: str
    location: str | None
    target_area: str
    criteria: str
    criteria_hash: str
    sources: tuple[str, ...]
    filters: dict[str, object]
    counts: dict[str, int]
    excluded_reasons: dict[str, int] = Field(
        default_factory=dict,
        description="Why code ruled things out. Excluded rows are summarised "
        "here rather than listed, unless the run asked for them.",
    )


class Results(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    legend: dict[str, dict[str, str]] = Field(default_factory=lambda: LEGEND)
    run: RunContext
    results: tuple[ResultRow, ...]


def _raw_facts(item: Screened, reading: Reading | None) -> RawFacts:
    facts = reading.facts if reading and reading.facts else None
    return RawFacts(
        location=item.candidate.location_raw,
        dates=item.candidate.dates_raw,
        deadline=(reading.deadline_text if reading else "") or "",
        status=item.candidate.status_raw,
        themes=item.candidate.themes,
        eligibility=facts.eligibility_text if facts else None,
        beginner=facts.beginner_friendly if facts else None,
        was_read=reading is not None and reading.ok,
    )


def _derived_facts(item: Screened) -> DerivedFacts:
    return DerivedFacts(
        in_area=item.location.in_area,
        area=item.location.area,
        location_status=item.location.status,
        places=tuple(place.name for place in item.location.places),
        regions=tuple(dict.fromkeys(place.region for place in item.location.places)),
        event_format=item.candidate.format_raw or item.location.event_format,
        starts_on=item.starts_on,
        ends_on=item.ends_on,
        deadline_date=item.deadline.date,
        deadline_status=item.deadline.status,
        days_away=item.deadline.days_away,
        bucket=item.bucket,
        reasons=item.reasons,
        flags=item.flags,
    )


def _fit(judgement: Judgement | None) -> Fit | None:
    if judgement is None or judgement.verdict is None:
        return None
    verdict = judgement.verdict
    return Fit(
        score=verdict.score,
        reason=verdict.reason,
        supports=tuple(verdict.supports),
        conflicts=tuple(verdict.conflicts),
        missing=tuple(verdict.missing),
    )


def build_results(
    ranked: Sequence[tuple[Screened, Judgement]],
    unranked: Sequence[Screened],
    all_screened: Sequence[Screened],
    config: Config,
    target_area: str,
    criteria_hash: str,
    readings: dict[str, Reading],
    today: date,
) -> Results:
    """Assemble the full file: ranked rows first, then everything else kept."""
    rows: list[ResultRow] = []

    def row(item: Screened, judgement: Judgement | None, position: int | None) -> ResultRow:
        return ResultRow(
            rank=position,
            title=item.candidate.title,
            url=item.candidate.url,
            source=item.candidate.source,
            also_in=item.candidate.also_in,
            website_url=item.candidate.website_url,
            raw=_raw_facts(item, readings.get(item.candidate.key)),
            derived=_derived_facts(item),
            fit=_fit(judgement),
        )

    for position, (item, judgement) in enumerate(ranked, start=1):
        rows.append(row(item, judgement, position))
    for item in unranked:
        rows.append(row(item, None, None))

    counts: dict[str, int] = {"collected": len(all_screened), "in_file": len(rows)}
    for item in all_screened:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1

    excluded_reasons: dict[str, int] = {}
    for item in all_screened:
        if item.bucket == "excluded" and item.reasons:
            key = _reason_kind(item.reasons[-1])
            excluded_reasons[key] = excluded_reasons.get(key, 0) + 1

    return Results(
        run=RunContext(
            generated_at=datetime.now().isoformat(timespec="seconds"),
            today=today,
            mode=config.mode,
            location=config.location,
            target_area=target_area,
            criteria=config.criteria,
            criteria_hash=criteria_hash,
            sources=config.collect.sources,
            filters=config.filters.model_dump(),
            counts=counts,
            excluded_reasons=excluded_reasons,
        ),
        results=tuple(rows),
    )


def _reason_kind(reason: str) -> str:
    """Group exclusion reasons so the summary stays readable."""
    if "outside" in reason:
        return "location: elsewhere"
    if "already happened" in reason:
        return "date: already happened"
    if "beyond the" in reason:
        return "date: beyond timeframe"
    if "online" in reason:
        return "online, no area link"
    if "filter wants" in reason:
        return "format filter"
    if "themes" in reason:
        return "theme filter"
    return reason
