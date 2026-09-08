"""The judging engine — the one genuinely un-scriptable job.

Everything else in this project is a rule: a place is in a set or it isn't, a
date is before today or it isn't. Fit is not. "Beginner-friendly, in-person,
open to non-students" against "runs at a community centre, all ages welcome,
no experience needed" is a judgement call, and this is where the model earns
its place.

Two things it is deliberately not allowed to do:
  - decide where the event is (core.gta did that)
  - decide whether the deadline has passed (core.dates did that)

It needs no browser. Judging is text in, score out, so no Steel session is
opened and the whole step runs in seconds.
"""

from __future__ import annotations

import asyncio
from typing import Sequence

from browser_use import ChatOpenAI
from browser_use.llm.messages import SystemMessage, UserMessage

from core.models import Candidate, FitVerdict, Judgement, Reading, criteria_fingerprint
from core.screening import Screened

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_CONCURRENCY = 5

SYSTEM_PROMPT = """You judge how well one hackathon fits a person's stated criteria.

Score 0-5:
  5  ideal — matches every part of the criteria
  4  good — matches the important parts
  3  partial — matches some, misses others
  1-2 weak — mostly does not match
  0  clearly wrong for this person

Rules:
- Judge ONLY fit against the criteria. The location has already been confirmed
  to be in the target area by other code, and the deadline has already been
  checked. Do not re-litigate either, and do not lower a score because you
  personally are unsure where somewhere is.
- Cite specific facts from the listing in your reason. "Beginner Friendly tag,
  in-person in Mississauga, but 18+ and students only" is a good reason.
  "Seems like a decent fit" is not.
- If the listing does not state something the criteria care about, do not
  assume it either way. Put it in `missing` and score on what is known.
- A listing with very little information should not score highly just because
  nothing contradicts the criteria."""

FACTS_TEMPLATE = """CRITERIA (what the person wants):
{criteria}

HACKATHON:
  name:        {title}
  location:    {location}
  format:      {event_format}
  when:        {when}
  deadline:    {deadline}
  themes:      {themes}
  source:      {source}
  eligibility: {eligibility}
  beginner:    {beginner}

Judge the fit."""


def _facts_for(item: Screened, reading: Reading | None, criteria: str) -> str:
    candidate: Candidate = item.candidate
    facts = reading.facts if reading and reading.facts else None
    return FACTS_TEMPLATE.format(
        criteria=criteria,
        title=candidate.title,
        location=item.location.raw or "not stated",
        event_format=candidate.format_raw or item.location.event_format,
        when=item.starts_on.isoformat() if item.starts_on else (candidate.dates_raw or "not stated"),
        deadline=item.deadline.date.isoformat() if item.deadline.date else "not stated",
        themes=", ".join(candidate.themes) or "none listed",
        source=candidate.source,
        eligibility=(facts.eligibility_text if facts and facts.eligibility_text
                     else "not stated (listing not read in full)"),
        beginner=(facts.beginner_friendly if facts else "not stated (listing not read in full)"),
    )


async def judge_one(
    llm: ChatOpenAI, item: Screened, criteria: str, reading: Reading | None = None
) -> Judgement:
    """Score one hackathon. Failures are recorded, never raised."""
    key = item.candidate.key
    fingerprint = criteria_fingerprint(criteria)
    try:
        result = await llm.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT),
             UserMessage(content=_facts_for(item, reading, criteria))],
            output_format=FitVerdict,
        )
        return Judgement(key=key, criteria_hash=fingerprint, verdict=result.completion)
    except Exception as exc:
        return Judgement(key=key, criteria_hash=fingerprint,
                         error=f"{type(exc).__name__}: {exc}")


async def judge_all(
    items: Sequence[Screened],
    criteria: str,
    readings: dict[str, Reading] | None = None,
    model: str = DEFAULT_MODEL,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_done=None,
) -> tuple[Judgement, ...]:
    """Score every item, a few at a time. Order of results matches `items`."""
    llm = ChatOpenAI(model=model)
    readings = readings or {}
    limit = asyncio.Semaphore(concurrency)

    async def run(item: Screened) -> Judgement:
        async with limit:
            judgement = await judge_one(llm, item, criteria, readings.get(item.candidate.key))
            if on_done:
                on_done(item, judgement)
            return judgement

    return tuple(await asyncio.gather(*(run(item) for item in items)))


def rank(
    items: Sequence[Screened], judgements: dict[str, Judgement]
) -> tuple[tuple[Screened, Judgement], ...]:
    """Best fit first. This is arithmetic, so code does it, not the model.

    Ties are broken by the nearer deadline, then by name, so the order is
    stable across runs rather than dependent on dict ordering.
    """
    def sort_key(item: Screened):
        judgement = judgements.get(item.candidate.key)
        score = judgement.score if judgement else -1
        days = item.deadline.days_away
        return (-score, days if days is not None else 10**6, item.candidate.title.casefold())

    ordered = sorted(items, key=sort_key)
    return tuple(
        (item, judgements[item.candidate.key])
        for item in ordered
        if item.candidate.key in judgements
    )
