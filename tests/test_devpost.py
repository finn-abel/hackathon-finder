"""The collector's pure parts: URL building, tile parsing, dedup.

These run without a browser. A recorded tile matching Devpost's real DOM
output stands in for the page.
"""

import pytest

from agent.sources.devpost import (
    DEFAULT_GTA_SEARCH_TERMS,
    dedupe,
    listing_url,
    parse_tiles,
    search_terms_for,
)
from core.models import Candidate, canonical_url

# Shaped exactly like what the extraction script returns.
RAW_TILE = {
    "title": "LarpHacks",
    "url": "https://larphacks.devpost.com/?ref_feature=challenge&ref_medium=discover",
    "location": "Toronto",
    "status": "Upcoming",
    "dates": "Oct 24 - 25, 2026",
    "host": "larphacks",
    "themes": ["Beginner Friendly", "Machine Learning/AI", "Web"],
}


def test_listing_url_builds_a_search_page():
    assert listing_url("Toronto") == "https://devpost.com/hackathons?search=Toronto"
    assert listing_url("Richmond Hill").endswith("search=Richmond+Hill")


def test_listing_url_carries_no_page_param():
    # Devpost ignores ?page= — the listing is an infinite scroll. Sending one
    # just re-fetches page 1 and doubles the browser time for nothing.
    assert "page=" not in listing_url("Toronto")


def test_gta_mode_searches_the_municipalities():
    assert search_terms_for("gta") == DEFAULT_GTA_SEARCH_TERMS
    assert "Toronto" in search_terms_for("gta")


def test_general_mode_searches_the_bare_place_name():
    # "Waterloo, ON" as a search term returns nothing useful; "Waterloo" does.
    assert search_terms_for("general", "Waterloo, ON") == ("Waterloo",)


def test_general_mode_needs_a_location():
    with pytest.raises(ValueError):
        search_terms_for("general")


def test_a_tile_becomes_a_candidate_with_every_field_kept():
    candidate = parse_tiles([RAW_TILE], found_via="Toronto")[0]
    assert candidate.title == "LarpHacks"
    assert candidate.location_raw == "Toronto"
    assert candidate.dates_raw == "Oct 24 - 25, 2026"
    assert candidate.status_raw == "Upcoming"
    assert candidate.themes == ("Beginner Friendly", "Machine Learning/AI", "Web")
    assert candidate.found_via == "Toronto"
    assert candidate.source == "devpost"


@pytest.mark.parametrize(
    "broken",
    [
        {**RAW_TILE, "title": ""},
        {**RAW_TILE, "url": ""},
        {**RAW_TILE, "title": "   "},
        {},
    ],
)
def test_tiles_with_no_name_or_no_link_are_dropped(broken):
    assert parse_tiles([broken]) == ()


def test_missing_optional_fields_do_not_break_parsing():
    candidate = parse_tiles([{"title": "X", "url": "https://x.devpost.com/"}])[0]
    assert candidate.location_raw == ""
    assert candidate.themes == ()


def test_tracking_params_are_stripped_for_identity():
    assert canonical_url(RAW_TILE["url"]) == "https://larphacks.devpost.com"
    assert canonical_url("https://A.devpost.com/path/") == "https://a.devpost.com/path"


def test_the_same_event_found_by_two_searches_dedups():
    from_toronto = parse_tiles([RAW_TILE], found_via="Toronto")
    from_markham = parse_tiles([{**RAW_TILE, "url": "https://larphacks.devpost.com/"}], "Markham")

    deduped = dedupe([*from_toronto, *from_markham])
    assert len(deduped) == 1
    assert deduped[0].found_via == "Toronto"  # first search to find it wins


def test_dedup_keeps_genuinely_different_events():
    other = {**RAW_TILE, "title": "Other", "url": "https://other.devpost.com/"}
    assert len(dedupe(parse_tiles([RAW_TILE, other]))) == 2


def test_candidates_are_immutable():
    candidate = parse_tiles([RAW_TILE])[0]
    with pytest.raises(Exception):
        candidate.title = "changed"


def test_candidate_key_is_the_canonical_url():
    assert Candidate(title="X", url="https://x.devpost.com/?a=1").key == "https://x.devpost.com"
