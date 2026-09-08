"""The deterministic layer's spec. `today` is pinned; no browser, no model."""

from datetime import date

import pytest

from core.config import Config, Filters
from core.location import GTA_MATCHER, target_matcher
from core.models import Candidate, ListingFacts, Reading
from core.screening import by_bucket, screen, screen_all

TODAY = date(2026, 9, 8)
CONFIG = Config(criteria="beginner-friendly, in-person")


def candidate(**kw) -> Candidate:
    base = dict(title="Test Hack", url="https://test.devpost.com/", location_raw="Toronto, ON")
    return Candidate(**{**base, **kw})


def screened(cfg: Config = CONFIG, matcher=GTA_MATCHER, reading=None, **kw):
    return screen(candidate(**kw), cfg, matcher, reading, TODAY)


# --- buckets --------------------------------------------------------------


def test_an_in_person_gta_event_is_primary():
    item = screened(location_raw="Toronto, ON", dates_raw="Oct 24 - 25, 2026",
                    format_raw="in-person")
    assert item.bucket == "primary"


def test_an_online_event_organised_in_the_gta_is_labelled_apart():
    # The plan asks for these kept, but not mixed into the in-person shortlist.
    item = screened(location_raw="Toronto, ON", dates_raw="Oct 24 - 25, 2026",
                    format_raw="online")
    assert item.bucket == "online-gta"
    assert "runs online" in item.reasons[0]


def test_somewhere_else_is_excluded():
    assert screened(location_raw="Waterloo, ON", dates_raw="Oct 24, 2026").bucket == "excluded"


def test_an_unplaceable_venue_is_unresolved_not_excluded():
    item = screened(location_raw="Sheridan College Hazel McCallion Campus",
                    dates_raw="Oct 24, 2026")
    assert item.bucket == "unresolved"
    assert "not read yet" in item.flags


def test_online_with_no_area_link_is_excluded():
    assert screened(location_raw="Online", dates_raw="Oct 24, 2026").bucket == "excluded"


# --- code decides, not the model ------------------------------------------


def test_the_screening_layer_never_imports_a_model():
    # Structural guarantee for the AI-vs-code split: if this ever fails,
    # judgement has leaked into the deterministic layer.
    import inspect

    import core.screening

    source = inspect.getsource(core.screening)
    for forbidden in ("browser_use", "ChatOpenAI", "Agent(", "llm"):
        assert forbidden not in source


def test_a_reading_supplies_facts_but_code_still_decides():
    reading = Reading(
        candidate=candidate(location_raw="Sheridan College Hazel McCallion Campus"),
        facts=ListingFacts.model_validate({
            "title": None, "location_text": "Sheridan HMC Campus (Mississauga, ON)",
            "event_format": "in-person", "format_evidence": None,
            "dates_text": None, "deadline_text": "by 12:00 PM EST on November 8, 2026",
            "eligibility_text": None, "themes": [],
            "beginner_friendly": "unstated", "beginner_evidence": None,
        }),
    )
    item = screened(location_raw="Sheridan College Hazel McCallion Campus",
                    dates_raw="Nov 07 - 08, 2026", reading=reading)
    # The model supplied the string; core.gta made the call.
    assert item.bucket == "primary"
    assert [p.name for p in item.location.places] == ["Mississauga"]
    assert item.deadline.date == date(2026, 11, 8)


# --- dates ----------------------------------------------------------------


def test_iso_timestamps_are_preferred_over_a_display_range():
    item = screened(starts_at="2026-04-24T00:00:00Z", ends_at="2026-04-26T00:00:00Z",
                    dates_raw="APR 24 - 26")
    assert item.starts_on == date(2026, 4, 24)
    assert item.ends_on == date(2026, 4, 26)


def test_an_ended_event_keeps_its_real_year():
    item = screened(status_raw="Ended", dates_raw="JAN 30 - FEB 01")
    assert item.starts_on == date(2026, 1, 30)
    assert item.bucket == "excluded"
    assert "already happened" in item.reasons[-1]


def test_a_deadline_borrowed_from_the_end_date_is_flagged_as_such():
    item = screened(dates_raw="Oct 24 - 25, 2026")
    assert item.deadline.date == date(2026, 10, 25)
    assert "deadline assumed from the event's last day" in item.flags


def test_a_missing_date_is_flagged_not_invented():
    item = screened(dates_raw="TBD")
    assert item.starts_on is None
    assert "no parseable date" in item.flags


# --- config filters -------------------------------------------------------


def test_the_timeframe_filter_excludes_events_beyond_the_window():
    near = screened(dates_raw="Oct 24, 2026")
    far = screened(dates_raw="Jun 24, 2027")
    assert near.bucket == "primary"
    assert far.bucket == "excluded"
    assert "beyond the 3-month window" in far.reasons[-1]


def test_the_format_filter_excludes_mismatches():
    cfg = Config(criteria="x", filters=Filters(format="in-person"))
    item = screened(cfg, dates_raw="Oct 24, 2026", format_raw="online")
    assert item.bucket == "excluded"
    assert "filter wants in-person" in item.reasons[-1]


def test_the_theme_filter_needs_an_overlap():
    cfg = Config(criteria="x", filters=Filters(themes=("ai",)))
    hit = screened(cfg, dates_raw="Oct 24, 2026", themes=("Machine Learning/AI",))
    miss = screened(cfg, dates_raw="Oct 24, 2026", themes=("Design",))
    assert hit.bucket == "primary"
    assert miss.bucket == "excluded"


def test_filters_never_rescue_something_code_already_ruled_out():
    cfg = Config(criteria="x", filters=Filters(format="any", timeframe_months=24))
    assert screened(cfg, location_raw="Waterloo, ON", dates_raw="Oct 24, 2026").bucket == "excluded"


# --- general mode ---------------------------------------------------------


def test_general_mode_screens_against_its_own_area():
    matcher = target_matcher("Waterloo, ON")
    cfg = Config(mode="general", location="Waterloo, ON", criteria="x")
    here = screen(candidate(location_raw="Waterloo, ON", dates_raw="Oct 24, 2026"),
                  cfg, matcher, None, TODAY)
    there = screen(candidate(location_raw="Toronto, ON", dates_raw="Oct 24, 2026"),
                   cfg, matcher, None, TODAY)
    assert here.bucket == "primary"
    # Only the GTA matcher has a gazetteer, so Toronto is unresolved, not excluded.
    assert there.bucket == "unresolved"


def test_screen_all_attaches_readings_by_candidate_key():
    items = screen_all([candidate(dates_raw="Oct 24, 2026")], CONFIG, GTA_MATCHER, {}, TODAY)
    assert len(items) == 1
    assert by_bucket(items, "primary") == items


def test_include_past_actually_keeps_past_events():
    # The flag promised this and did not deliver until it became a real filter:
    # widening the timeframe window does nothing for an event already gone.
    past = dict(location_raw="Toronto, ON", dates_raw="Jan 10 - 11, 2026", status_raw="Ended")
    assert screened(**past).bucket == "excluded"

    keeping = Config(criteria="x", filters=Filters(include_past=True, timeframe_months=24))
    assert screened(keeping, **past).bucket == "primary"
