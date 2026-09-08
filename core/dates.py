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

#: Range separators as listings actually write them: hyphen, en/em dash, "to".
_RANGE_RE = re.compile(r"\s*[\u2010-\u2015\u2212]\s*|\s+-\s*|\s+to\s+", re.I)
_DAY_ONLY_RE = re.compile(r"^\s*(\d{1,2})\b")

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


def parse_iso_date(text: str | None) -> date | None:
    """Read an ISO 8601 timestamp. Sources that publish these need no guessing."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _one_end(text: str, year: int) -> date | None:
    """Parse one end of a range, with `year` supplied when the text omits it."""
    if not _looks_like_a_date(text):
        return None
    try:
        return dateparser.parse(
            text, fuzzy=True, dayfirst=False, default=datetime(year, 1, 1)
        ).date()
    except (ValueError, OverflowError, TypeError):
        return None


def parse_date_range(
    text: str | None,
    today: date | None = None,
    assume_future: bool = True,
) -> tuple[date | None, date | None]:
    """Parse "Oct 24 - 25, 2026" or "Feb 27 - Mar 01, 2026" into two dates.

    Listings write the year once, at the end, and often omit the month on the
    second date. Both are filled in from the first half.

    `assume_future=False` turns off the roll-forward for sources that already
    told us the event has ended — inventing a next-year date for something
    labelled "Ended" would be worse than reporting the year as written.
    """
    today = today or date.today()
    cleaned = (text or "").strip()
    if not cleaned:
        return (None, None)

    year_match = _YEAR_RE.search(cleaned)
    year = int(year_match.group()) if year_match else today.year

    parts = _RANGE_RE.split(cleaned, maxsplit=1)
    start = _one_end(parts[0], year)
    if start is None:
        return (None, None)

    if len(parts) == 1:
        end = start
    elif _MONTH_RE.search(parts[1]):
        end = _one_end(parts[1], year) or start
    else:
        # "Oct 24 - 25, 2026": the second half is a bare day in the same month.
        day = _DAY_ONLY_RE.match(parts[1])
        try:
            end = start.replace(day=int(day.group(1))) if day else start
        except ValueError:
            end = start

    if end < start:
        end += relativedelta(years=1)  # a range crossing New Year

    if assume_future and not year_match and start < today:
        start, end = start + relativedelta(years=1), end + relativedelta(years=1)

    return (start, end)


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
