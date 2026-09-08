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
