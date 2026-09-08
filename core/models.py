"""The shapes that move between steps: a candidate, and a screened candidate."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def canonical_url(url: str) -> str:
    """Strip tracking params and trailing slashes so the same event dedups."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))


class Candidate(BaseModel):
    """One hackathon as it appears on a listing page, before any judging.

    Everything here was read straight off the DOM — no model wrote any of it.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    url: str
    source: str = "devpost"
    location_raw: str = ""
    dates_raw: str = ""
    status_raw: str = ""
    host: str = ""
    themes: tuple[str, ...] = ()
    found_via: str = ""

    # Some sources publish these outright. When they do, code uses them and
    # no page needs reading: MLH gives ISO timestamps and an explicit format,
    # Devpost gives neither.
    starts_at: str = ""       # ISO 8601, if the source states one
    ends_at: str = ""         # ISO 8601, if the source states one
    format_raw: str = ""      # the source's own word for the format
    website_url: str = ""     # the event's own site, when the source links it
    also_in: tuple[str, ...] = ()  # other sources that listed this same event

    @property
    def key(self) -> str:
        """Identity for dedup: the canonical URL."""
        return canonical_url(self.url)


Stated = Literal["yes", "no", "unstated"]
ExtractedFormat = Literal["in-person", "online", "hybrid", "unstated"]


class ListingFacts(BaseModel):
    """The raw facts one listing states about itself.

    Every field is nullable on purpose: "the page does not say" is a real and
    useful answer, and far better than an invented one.
    """

    title: str | None = Field(description="The hackathon's name as the page shows it.")
    location_text: str | None = Field(
        description="The location EXACTLY as written, e.g. 'Sheridan College, "
        "Oakville, ON' or 'Online'. Do not normalize, expand or correct it."
    )
    event_format: ExtractedFormat = Field(
        description="in-person, online or hybrid if the page states or clearly "
        "shows it; otherwise 'unstated'."
    )
    format_evidence: str | None = Field(
        description="The words on the page that establish the format. Null if unstated."
    )
    dates_text: str | None = Field(
        description="When the event runs, verbatim, e.g. 'Oct 24 - 25, 2026'."
    )
    deadline_text: str | None = Field(
        description="The submission or registration deadline, verbatim, including "
        "any time and timezone. Null if the page gives no deadline."
    )
    eligibility_text: str | None = Field(
        description="Who may participate, quoted from the page — age, student "
        "status, residency, team size. Often under Rules or 'Who can participate'. "
        "Null if the page says nothing about eligibility."
    )
    themes: list[str] = Field(
        description="Theme or topic tags the page lists, e.g. ['Machine Learning/AI', 'Web']."
    )
    beginner_friendly: Stated = Field(
        description="'yes' only if the page says beginners are welcome or tags "
        "itself Beginner Friendly; 'no' only if it requires experience; "
        "otherwise 'unstated'."
    )
    beginner_evidence: str | None = Field(
        description="The words that establish the beginner answer. Null if unstated."
    )


class Reading(BaseModel):
    """One candidate plus whatever reading its page produced.

    A failed read is still a Reading — it records the error rather than
    vanishing, so a re-run knows what to retry and the totals stay honest.
    """

    model_config = ConfigDict(frozen=True)

    candidate: Candidate
    facts: ListingFacts | None = None
    error: str | None = None
    read_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def ok(self) -> bool:
        return self.facts is not None

    @property
    def location_text(self) -> str:
        """The best location string available, page first, tile as fallback.

        The page states a city; the tile often only names a venue. Choosing
        between them is code's call, not the model's.
        """
        if self.facts and self.facts.location_text:
            return self.facts.location_text
        return self.candidate.location_raw

    @property
    def themes(self) -> tuple[str, ...]:
        """Tile themes win — they are Devpost's own tags, read off the DOM."""
        if self.candidate.themes:
            return self.candidate.themes
        return tuple(self.facts.themes) if self.facts else ()

    @property
    def deadline_text(self) -> str:
        """The page's deadline wording, or "" if it states none.

        Deliberately does NOT fall back to the tile's date range: those are
        when the event runs, not when submissions close. Substituting one for
        the other would put a confident wrong date in front of you.
        """
        return (self.facts.deadline_text or "") if self.facts else ""

    @property
    def dates_text(self) -> str:
        """When the event runs — the page's wording, or the tile's range."""
        if self.facts and self.facts.dates_text:
            return self.facts.dates_text
        return self.candidate.dates_raw


class FitVerdict(BaseModel):
    """The AI's judgement of one hackathon against your criteria.

    A score, not a rank: ordering a list is arithmetic, and code does it.
    The model's job is the un-scriptable part — deciding whether "runs at a
    community centre, open to all ages, no experience needed" matches
    "beginner-friendly, open to non-students".
    """

    model_config = ConfigDict(frozen=True)

    score: int = Field(
        ge=0, le=5,
        description="0 = clearly wrong, 1-2 = weak, 3 = partial, 4 = good, 5 = ideal.",
    )
    reason: str = Field(
        description="One sentence citing SPECIFIC facts from the listing, e.g. "
        "'in-person in Toronto, tagged Beginner Friendly, but students-only'."
    )
    supports: list[str] = Field(description="Facts that match the criteria.")
    conflicts: list[str] = Field(description="Facts that go against the criteria.")
    missing: list[str] = Field(
        description="What the listing does not say that would change the score, "
        "e.g. 'eligibility'. Empty if nothing important is missing."
    )


class Judgement(BaseModel):
    """A FitVerdict tied to the exact criteria it was made against.

    Storing the criteria fingerprint is what makes the cache safe: edit the
    criteria sentence and every stale judgement is ignored automatically.
    """

    model_config = ConfigDict(frozen=True)

    key: str                      # the candidate's canonical URL
    criteria_hash: str
    verdict: FitVerdict | None = None
    error: str | None = None
    judged_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def ok(self) -> bool:
        return self.verdict is not None

    @property
    def score(self) -> int:
        return self.verdict.score if self.verdict else -1


def criteria_fingerprint(criteria: str) -> str:
    """Stable id for a criteria sentence, ignoring case and spacing."""
    normalized = " ".join(criteria.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
