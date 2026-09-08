"""Flags: everything the pipeline noticed but could not settle.

The rule for this whole project is that nothing is silently dropped and
nothing is silently guessed. When code has to assume something, or finds two
facts that contradict each other, or cannot reach a page at all, it says so
here and the listing stays in the output carrying its flag.

Severity drives what happens next:
    info       worth knowing, no action needed
    warn       the data is thinner than it looks
    attention  a human should look at this one
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Severity = Literal["info", "warn", "attention"]


class Flag(BaseModel):
    """One thing the pipeline noticed about a listing."""

    model_config = ConfigDict(frozen=True)

    code: str
    detail: str = ""
    severity: Severity = "info"

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


#: The catalogue. Codes are stable strings so the dashboard and any downstream
#: consumer can rely on them; the human wording lives in `detail`.
CATALOGUE: dict[str, tuple[Severity, str]] = {
    "not_read": ("warn", "The detail page has not been opened yet."),
    "read_failed": ("attention", "The detail page could not be read."),
    "no_parseable_date": ("attention", "No date could be parsed from the listing."),
    "no_deadline": ("warn", "The listing states no submission deadline."),
    "assumed_deadline": ("info", "Deadline taken from the event's last day, not stated."),
    "missing_eligibility": ("warn", "The page was read but states no eligibility."),
    "format_conflict": ("attention", "The source and the page disagree about the format."),
    "online_but_placed": ("attention", "Tagged for a place but the listing says it runs online."),
    "deadline_after_event": ("attention", "The deadline falls after the event ends."),
    "possible_duplicate": ("attention", "Another listing looks like the same event."),
    "suspect_title": ("attention", "The title itself warns the listing is stale or wrong."),
}


def flag(code: str, detail: str = "") -> Flag:
    """Build a catalogued flag. Unknown codes are still allowed, as warnings."""
    severity, description = CATALOGUE.get(code, ("warn", ""))
    return Flag(code=code, detail=detail or description, severity=severity)


def needs_attention(flags: tuple[Flag, ...]) -> bool:
    return any(f.severity == "attention" for f in flags)
