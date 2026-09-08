"""Major League Hacking — the season's global event list.

MLH is a far better-behaved source than Devpost: one page load returns every
event of the season as embedded JSON, with a clean "City, Province" location,
a structured venue address, ISO start/end timestamps and an explicit format.

That means MLH events need no AI reading at all. Code extracts, code
classifies, and the model is never asked a geography question.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Iterator

from browser_use import Browser

from agent.session import evaluate_json
from agent.sources import CollectRequest
from core.models import Candidate

NAME = "mlh"

EVENTS_URL = "https://www.mlh.com/seasons/{season}/events"

#: MLH seasons run roughly August to July and are named for the year they end
#: in, so the 2027 season starts in August 2026. Hardcoding a season silently
#: scrapes an archive: in September 2026 the 2026 season is 252/254 ended.
SEASON_ROLLS_OVER_IN_MONTH = 8

#: Every event object in the payload starts with a uuid id and a slug.
_EVENT_START_RE = re.compile(r'\{"id":"[0-9a-f-]{36}","slug":"')

_PAGE_HTML_JS = "JSON.stringify({html: document.documentElement.innerHTML})"

#: MLH's own format vocabulary.
_FORMATS = {"physical": "in-person", "digital": "online", "hybrid": "hybrid"}

#: venueAddress.country is an ISO alpha-2 code. Two-letter codes are hopeless
#: to match in prose ("IN" is also a word), so expand them to country names
#: that `core.location` can recognise as foreign.
_COUNTRY_NAMES = {
    "CA": "Canada", "US": "United States", "GB": "United Kingdom", "IE": "Ireland",
    "IN": "India", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka",
    "NP": "Nepal", "SG": "Singapore", "MY": "Malaysia", "ID": "Indonesia",
    "PH": "Philippines", "VN": "Vietnam", "TH": "Thailand", "JP": "Japan",
    "CN": "China", "KR": "Korea", "TW": "Taiwan", "HK": "Hong Kong",
    "AU": "Australia", "NZ": "New Zealand", "DE": "Germany", "FR": "France",
    "ES": "Spain", "IT": "Italy", "PT": "Portugal", "NL": "Netherlands",
    "BE": "Belgium", "CH": "Switzerland", "AT": "Austria", "PL": "Poland",
    "CZ": "Czechia", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
    "FI": "Finland", "GR": "Greece", "HU": "Hungary", "RO": "Romania",
    "UA": "Ukraine", "RS": "Serbia", "HR": "Croatia", "TR": "Turkey",
    "IL": "Israel", "AE": "United Arab Emirates", "SA": "Saudi Arabia",
    "EG": "Egypt", "NG": "Nigeria", "KE": "Kenya", "GH": "Ghana",
    "ZA": "South Africa", "MA": "Morocco", "TN": "Tunisia",
    "BR": "Brazil", "MX": "Mexico", "AR": "Argentina", "CL": "Chile",
    "CO": "Colombia", "PE": "Peru",
}


def current_season(today: date | None = None) -> str:
    """The season MLH is currently listing, from the calendar."""
    today = today or date.today()
    year = today.year + 1 if today.month >= SEASON_ROLLS_OVER_IN_MONTH else today.year
    return str(year)


def seasons_to_fetch(today: date | None = None) -> tuple[str, ...]:
    """The current season, plus the one before it.

    The previous season still holds events running in the first weeks of the
    new one, and near a rollover the current list can be sparse.
    """
    current = int(current_season(today))
    return (str(current), str(current - 1))


def events_url(season: str | None = None) -> str:
    return EVENTS_URL.format(season=season or current_season())


def _balanced_object(text: str, start: int) -> str | None:
    """Read one JSON object from `start`, counting braces, respecting strings."""
    depth, index, in_string, escaped = 0, start, False, False
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
        index += 1
    return None


def extract_events(html: str) -> tuple[dict[str, Any], ...]:
    """Pull every event object out of the page's embedded JSON.

    Pure string work, so it is testable against a saved page with no browser.
    """
    events: list[dict[str, Any]] = []
    for match in _EVENT_START_RE.finditer(html):
        raw = _balanced_object(html, match.start())
        if raw is None:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue  # a partial or escaped copy; the real one appears elsewhere
    return tuple(events)


def _location_of(event: dict[str, Any]) -> str:
    """Prefer the structured venue address over the display string.

    The country code is expanded to a name so a non-Canadian event reads as
    definitively elsewhere instead of merely unrecognised — an "unclear"
    verdict would queue it for an AI read it does not need.
    """
    venue = event.get("venueAddress") or {}
    code = (venue.get("country") or "").strip().upper()
    country = _COUNTRY_NAMES.get(code, code)
    parts = [venue.get("city"), venue.get("state"), country]
    structured = ", ".join(str(part).strip() for part in parts if part)
    return structured or (event.get("location") or "")


def _event_page(event: dict[str, Any]) -> str:
    url = (event.get("url") or "").replace("/prizes", "")
    if url.startswith("/"):
        return f"https://www.mlh.com{url}"
    return url or (event.get("websiteUrl") or "")


def to_candidates(events: Iterator[dict[str, Any]] | tuple[dict[str, Any], ...]) -> tuple[Candidate, ...]:
    """Turn MLH event objects into Candidates, dropping unusable ones."""
    candidates: list[Candidate] = []
    for event in events:
        title = (event.get("name") or "").strip()
        url = _event_page(event)
        if not title or not url:
            continue
        underserved = (event.get("customFields") or {}).get("underserved_types") or []
        candidates.append(
            Candidate(
                title=title,
                url=url,
                source=NAME,
                location_raw=_location_of(event),
                dates_raw=(event.get("dateRange") or "").strip(),
                status_raw=(event.get("status") or "").strip(),
                themes=tuple(str(t).strip() for t in underserved if str(t).strip()),
                starts_at=(event.get("startsAt") or "").strip(),
                ends_at=(event.get("endsAt") or "").strip(),
                format_raw=_FORMATS.get(event.get("formatType") or "", event.get("formatType") or ""),
                website_url=(event.get("websiteUrl") or "").strip(),
                location_structured=bool((event.get("venueAddress") or {}).get("city")),
                found_via="season list",
            )
        )
    return tuple(candidates)


async def collect(browser: Browser, request: CollectRequest) -> tuple[Candidate, ...]:
    """Load each season's list and return every event on it.

    No search terms and no scrolling: MLH publishes a whole season in one
    payload, and `core.location` does the area filtering afterwards.
    """
    seasons = request.seasons or seasons_to_fetch()
    found: list[Candidate] = []
    seen: set[str] = set()
    for season in seasons:
        await browser.navigate_to(events_url(season))
        html = (await evaluate_json(browser, _PAGE_HTML_JS))["html"]
        for candidate in to_candidates(extract_events(html)):
            if candidate.key not in seen:
                seen.add(candidate.key)
                found.append(candidate)
    return tuple(found)
