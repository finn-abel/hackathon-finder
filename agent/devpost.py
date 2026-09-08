"""Collect hackathon candidates from Devpost's listing pages.

Deliberately no LLM here. A listing page is a structured list, and reading a
structured list is something code does perfectly — so the browser navigates
and the DOM is queried directly. Every title and href comes back byte-exact.

The model's turn comes at the next step, reading the messy detail pages.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Sequence
from urllib.parse import urlencode

from browser_use import Browser

from agent.session import evaluate, evaluate_json
from core.models import Candidate

LISTING_URL = "https://devpost.com/hackathons"

TILE_SELECTOR = ".hackathon-tile"
TILE_RENDER_TIMEOUT_S = 20.0
TILE_POLL_INTERVAL_S = 0.5

#: Devpost's listing is an infinite scroll — there are no page links and a
#: ?page= parameter is ignored. More results only arrive by scrolling down.
SCROLL_GROWTH_TIMEOUT_S = 8.0

#: Devpost's search matches titles as well as locations, so a broad term like
#: "GTA" returns noise. These are the municipalities worth querying directly;
#: `core.location` does the actual area screening afterwards.
DEFAULT_GTA_SEARCH_TERMS = (
    "Toronto",
    "Mississauga",
    "Brampton",
    "Markham",
    "Vaughan",
    "Oshawa",
)

_COUNT_TILES_JS = f"document.querySelectorAll({TILE_SELECTOR!r}).length"

_EXTRACT_TILES_JS = f"""
(() => {{
  const text = (el) => (el ? el.textContent.trim().replace(/\\s+/g, ' ') : '');
  const tiles = Array.from(document.querySelectorAll({TILE_SELECTOR!r}));
  return JSON.stringify(tiles.map((tile) => {{
    const anchor = tile.querySelector('a.tile-anchor');
    const marker = tile.querySelector('.fa-map-marker-alt');
    const markerBox = marker ? marker.closest('.info-with-icon') : null;
    return {{
      title: text(tile.querySelector('h3')),
      url: anchor ? anchor.href : '',
      location: markerBox ? text(markerBox.querySelector('.info')) : '',
      status: text(tile.querySelector('.status-label')),
      dates: text(tile.querySelector('.submission-period')),
      host: text(tile.querySelector('.host-label')),
      themes: Array.from(tile.querySelectorAll('.theme-label')).map(text),
    }};
  }}));
}})()
"""


def listing_url(term: str) -> str:
    """The Devpost listing URL for one search term."""
    return f"{LISTING_URL}?{urlencode({'search': term})}"


def search_terms_for(mode: str, location: str | None = None) -> tuple[str, ...]:
    """What to search Devpost for, given the run's mode."""
    if mode == "gta":
        return DEFAULT_GTA_SEARCH_TERMS
    if not (location or "").strip():
        raise ValueError("general mode needs a location to search for")
    # The bare place name searches better than "Waterloo, ON".
    return (location.split(",")[0].strip(),)  # type: ignore[union-attr]


def parse_tiles(tiles: Iterable[dict[str, Any]], found_via: str = "") -> tuple[Candidate, ...]:
    """Turn raw tile dicts into Candidates, dropping anything unusable."""
    parsed: list[Candidate] = []
    for tile in tiles:
        title = (tile.get("title") or "").strip()
        url = (tile.get("url") or "").strip()
        if not title or not url:
            continue  # a tile with no name or no link is not a candidate
        parsed.append(
            Candidate(
                title=title,
                url=url,
                location_raw=(tile.get("location") or "").strip(),
                deadline_raw=(tile.get("dates") or "").strip(),
                status_raw=(tile.get("status") or "").strip(),
                host=(tile.get("host") or "").strip(),
                themes=tuple(t.strip() for t in tile.get("themes") or () if t.strip()),
                found_via=found_via,
            )
        )
    return tuple(parsed)


def dedupe(candidates: Iterable[Candidate]) -> tuple[Candidate, ...]:
    """Collapse the same event found by several searches. First win keeps."""
    seen: dict[str, Candidate] = {}
    for candidate in candidates:
        seen.setdefault(candidate.key, candidate)
    return tuple(seen.values())


async def _count_tiles(browser: Browser) -> int:
    return int(await evaluate_json(browser, _COUNT_TILES_JS) or 0)


async def _wait_for_tiles(browser: Browser) -> int:
    """Devpost renders its listing client-side, so poll until tiles appear."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + TILE_RENDER_TIMEOUT_S
    while loop.time() < deadline:
        count = await _count_tiles(browser)
        if count:
            return count
        await asyncio.sleep(TILE_POLL_INTERVAL_S)
    return 0


async def _scroll_for_more(browser: Browser, current: int) -> int:
    """Scroll to the bottom and wait for the tile count to grow.

    Returns the count unchanged if nothing more loads, which is how the
    caller knows it has reached the end of the results.
    """
    await evaluate(browser, "window.scrollTo(0, document.body.scrollHeight)")
    loop = asyncio.get_event_loop()
    deadline = loop.time() + SCROLL_GROWTH_TIMEOUT_S
    while loop.time() < deadline:
        await asyncio.sleep(TILE_POLL_INTERVAL_S)
        count = await _count_tiles(browser)
        if count > current:
            return count
    return current


async def collect_term(browser: Browser, term: str, max_scrolls: int = 3) -> tuple[Candidate, ...]:
    """Load one search term's listing, scroll it out, then read every tile."""
    await browser.navigate_to(listing_url(term))
    count = await _wait_for_tiles(browser)
    if not count:
        return ()

    for _ in range(max_scrolls):
        grown = await _scroll_for_more(browser, count)
        if grown == count:
            break  # nothing more to load
        count = grown

    tiles = await evaluate_json(browser, _EXTRACT_TILES_JS)
    return parse_tiles(tiles, found_via=term)


async def collect(
    browser: Browser,
    terms: Sequence[str],
    max_scrolls: int = 3,
    on_term: Any = None,
) -> tuple[Candidate, ...]:
    """Collect every search term's results and return them deduped."""
    found: list[Candidate] = []
    for term in terms:
        candidates = await collect_term(browser, term, max_scrolls)
        if on_term:
            on_term(term, len(candidates))
        found.extend(candidates)
    return dedupe(found)
