"""Parse a deadline string and compare it to today.

The AI copies the deadline text off the page exactly as written; every
judgement about it — is it real, has it passed, is it close — happens here,
where it is reproducible. `today` is injectable so tests never depend on
the calendar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta

DeadlineStatus = Literal["passed", "soon", "upcoming", "unknown"]

DEFAULT_SOON_DAYS = 7

_MONTH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", re.I
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

#: Numeric shapes that are actually dates, so "closes at 5pm" is not one.
_NUMERIC_DATE_RE = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"          # 2026-03-05
    r"|\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?"  # 03/05/2026, 3.5.26
    r"|\b\d{1,2}(?:st|nd|rd|th)\b"    # 5th
)

_TODAY_RE = re.compile(r"\b(today|tonight|ends today|last day)\b", re.I)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.I)
_RELATIVE_RE = re.compile(r"\b(\d{1,3})\s*(day|week|month)s?\b", re.I)

_RELATIVE_UNITS = {"day": "days", "week": "weeks", "month": "months"}


@dataclass(frozen=True, slots=True)
class Deadline:
    """One listing's deadline, resolved against a specific `today`."""

    raw: str
    date: date | None
    status: DeadlineStatus
    days_away: int | None

    @property
    def is_open(self) -> bool:
        """True while the deadline has not passed. Unknown counts as open."""
        return self.status != "passed"


def _relative_date(text: str, today: date) -> date | None:
    """Handle "tomorrow", "3 days left", "in 2 weeks" before touching dateutil."""
    if _TODAY_RE.search(text):
        return today
    if _TOMORROW_RE.search(text):
        return today + relativedelta(days=1)

    match = _RELATIVE_RE.search(text)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        return today + relativedelta(**{_RELATIVE_UNITS[unit]: amount})
    return None


def _looks_like_a_date(text: str) -> bool:
    """Guard against dateutil's fuzzy parser inventing a date from stray digits."""
    return bool(_MONTH_RE.search(text) or _NUMERIC_DATE_RE.search(text))


def _absolute_date(text: str, today: date) -> date | None:
    if not _looks_like_a_date(text):
        return None
    try:
        parsed = dateparser.parse(
            text,
            fuzzy=True,
            dayfirst=False,  # North American listings: 03/05 is March 5
            default=datetime(today.year, today.month, 1),
        ).date()
    except (ValueError, OverflowError, TypeError):
        return None

    # "March 5" with no year means the next March 5, not one in the past.
    if not _YEAR_RE.search(text) and parsed < today:
        parsed += relativedelta(years=1)
    return parsed


def parse_deadline(
    raw: str | None,
    today: date | None = None,
    soon_days: int = DEFAULT_SOON_DAYS,
) -> Deadline:
    """Resolve a deadline string to a date and a status.

    Returns status "unknown" — never a guess — when the text has no date in
    it ("Rolling", "TBD", ""), so downstream filters can decide what to do.
    """
    today = today or date.today()
    text = (raw or "").strip()
    if not text:
        return Deadline(raw=text, date=None, status="unknown", days_away=None)

    resolved = _relative_date(text, today) or _absolute_date(text, today)
    if resolved is None:
        return Deadline(raw=text, date=None, status="unknown", days_away=None)

    days_away = (resolved - today).days
    if days_away < 0:
        status: DeadlineStatus = "passed"
    elif days_away <= soon_days:
        status = "soon"
    else:
        status = "upcoming"

    return Deadline(raw=text, date=resolved, status=status, days_away=days_away)


def within_months(deadline: Deadline, months: int, today: date | None = None) -> bool:
    """True if the deadline falls inside the next N months. Backs `timeframe_months`."""
    if deadline.date is None:
        return False
    today = today or date.today()
    return today <= deadline.date <= today + relativedelta(months=months)


if __name__ == "__main__":
    import sys

    today = date.today()
    samples = sys.argv[1:] or [
        "March 5, 2026",
        "Submissions close March 5, 2026 at 5:00pm EST",
        "2026-03-05",
        "03/05/2026",
        "January 2, 2020",
        "tomorrow",
        "3 days left",
        "Rolling admission",
        "Closes at 5pm",
        "",
    ]
    print(f"Today is {today}\n")
    for sample in samples:
        d = parse_deadline(sample, today=today)
        away = f"{d.days_away:+d}d" if d.days_away is not None else "  —"
        print(f"  {d.status:<9} {away:>6}  {str(d.date or '-'):<12} {sample!r}")
