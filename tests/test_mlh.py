"""MLH extraction, tested against a saved page. No browser, no network."""

from pathlib import Path

import pytest

from agent.sources.mlh import (
    NAME,
    events_url,
    extract_events,
    to_candidates,
)
from core.location import classify

FIXTURE = Path(__file__).parent / "fixtures" / "mlh_season.html"
EVENTS = extract_events(FIXTURE.read_text(encoding="utf-8"))
BY_NAME = {c.title: c for c in to_candidates(EVENTS)}


def test_events_are_pulled_out_of_the_embedded_json():
    assert len(EVENTS) == 4
    assert {e["name"] for e in EVENTS} == {
        "Hack the 6ix", "MakeUofT 2026", "Hack the North", "HackPrix Season 3"
    }


def test_the_season_url_is_well_formed():
    assert events_url("2026") == "https://www.mlh.com/seasons/2026/events"


def test_a_truncated_page_yields_nothing_rather_than_raising():
    assert extract_events('{"id":"019cfd94-9ee5-01a0-405f-860fdd615be4","slug":"broken"') == ()
    assert extract_events("") == ()


def test_candidates_carry_the_structured_fields_devpost_never_gives():
    event = BY_NAME["MakeUofT 2026"]
    assert event.source == NAME
    assert event.starts_at.startswith("2026-")   # ISO, parseable by code
    assert event.format_raw == "in-person"       # explicit, not inferred
    assert event.url.startswith("https://www.mlh.com/events/")


def test_the_structured_venue_address_is_preferred_over_the_display_string():
    # MLH's own city field is messy here ("Toronto, ON"), but the country code
    # is expanded so the string is unambiguously Canadian.
    assert BY_NAME["Hack the 6ix"].location_raw == "Toronto, ON, Ontario, Canada"
    assert BY_NAME["MakeUofT 2026"].location_raw == "Toronto, Ontario, Canada"


def test_a_foreign_country_code_becomes_a_name_the_classifier_knows():
    # "IN" cannot be matched in prose; "India" can. Without this, a Hyderabad
    # event reads as "unclear" and gets queued for an AI read it never needs.
    assert BY_NAME["HackPrix Season 3"].location_raw.endswith("India")
    assert classify(BY_NAME["HackPrix Season 3"].location_raw).status == "elsewhere"


@pytest.mark.parametrize(
    "title,in_gta",
    [
        ("Hack the 6ix", True),
        ("MakeUofT 2026", True),
        ("Hack the North", False),      # Waterloo
        ("HackPrix Season 3", False),   # Hyderabad
    ],
)
def test_the_existing_classifier_places_mlh_events_with_no_ai(title, in_gta):
    # The whole point: MLH's location is clean enough that code settles it.
    assert classify(BY_NAME[title].location_raw).in_area is in_gta


def test_mlh_events_never_need_a_reader():
    # Every MLH location resolves one way or the other, so none go to the AI.
    for candidate in BY_NAME.values():
        assert not classify(candidate.location_raw).needs_a_reader


def test_events_with_no_name_or_no_link_are_dropped():
    assert to_candidates([{"name": "", "url": "/events/x/prizes"}]) == ()
    assert to_candidates([{"name": "X", "url": "", "websiteUrl": ""}]) == ()


# --- the season must come from the calendar, not a constant ---------------


from datetime import date

from agent.sources.mlh import current_season, seasons_to_fetch


@pytest.mark.parametrize(
    "today,season",
    [
        (date(2026, 9, 8), "2027"),    # September 2026 is the 2027 season
        (date(2026, 8, 1), "2027"),    # the rollover month
        (date(2026, 7, 31), "2026"),   # the day before it
        (date(2027, 1, 10), "2027"),
        (date(2027, 6, 30), "2027"),
    ],
)
def test_the_season_is_worked_out_from_today(today, season):
    # Hardcoding this scraped an archive: in September 2026 the 2026 season
    # was 252/254 ended, so almost nothing current reached the shortlist.
    assert current_season(today) == season


def test_the_previous_season_is_fetched_too():
    # Events early in a new season are often still filed under the old one.
    assert seasons_to_fetch(date(2026, 9, 8)) == ("2027", "2026")


def test_the_url_follows_the_season():
    assert events_url("2027").endswith("/seasons/2027/events")
