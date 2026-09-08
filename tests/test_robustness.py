"""Step 12's contract: nothing crashes, nothing is silently dropped, and
anything the pipeline could not settle carries a flag.
"""

from datetime import date

import pytest

from core.config import Config, Filters
from core.duplicates import is_suspect_title, near_duplicates, series_stem
from core.flags import CATALOGUE, flag, needs_attention
from core.location import GTA_MATCHER
from core.models import Candidate, ListingFacts, Reading
from core.screening import screen, screen_all

TODAY = date(2026, 9, 8)
CONFIG = Config(criteria="x", filters=Filters(include_past=True, timeframe_months=24))


def codes(item) -> set[str]:
    return {f.code for f in item.flags}


def one(candidate: Candidate, reading=None, duplicates=None):
    return screen(candidate, CONFIG, GTA_MATCHER, reading, TODAY, duplicates)


# --- pathological input does not crash ------------------------------------

BROKEN = [
    Candidate(title="Empty", url="https://empty.devpost.com/"),
    Candidate(title="ZW", url="https://zw.devpost.com/", location_raw="​​",
              dates_raw="​"),
    Candidate(title="Emoji 🎉🎉", url="https://e.devpost.com/", location_raw="🎉 Toronto 🎉",
              dates_raw="🎉"),
    Candidate(title="Junk dates", url="https://j.devpost.com/", location_raw="Toronto",
              dates_raw="sometime next Michaelmas"),
    Candidate(title="Bad ISO", url="https://b.devpost.com/", location_raw="Toronto",
              starts_at="not-a-timestamp", ends_at="also-not"),
    Candidate(title="Huge", url="https://h.devpost.com/", location_raw="Toronto " * 400,
              dates_raw="Oct 24, 2026"),
    Candidate(title="  ", url="https://blank.devpost.com/"),
    Candidate(title="Nulls", url="https://n.devpost.com/", location_raw="",
              dates_raw="", status_raw="", themes=()),
]


@pytest.mark.parametrize("candidate", BROKEN, ids=lambda c: c.title.strip() or "blank")
def test_a_broken_listing_is_screened_without_raising(candidate):
    item = one(candidate)
    assert item.bucket in ("primary", "online-gta", "unresolved", "excluded")


def test_broken_listings_are_kept_not_dropped():
    assert len(screen_all(BROKEN, CONFIG, GTA_MATCHER, {}, TODAY)) == len(BROKEN)


def test_a_listing_with_no_dates_is_flagged_rather_than_dated():
    item = one(Candidate(title="Empty", url="https://empty.devpost.com/", location_raw="Toronto"))
    assert item.starts_on is None
    assert "no_parseable_date" in codes(item)
    assert item.needs_attention


def test_an_unparseable_ISO_timestamp_falls_back_instead_of_raising():
    item = one(Candidate(title="Bad ISO", url="https://b.devpost.com/", location_raw="Toronto",
                         starts_at="not-a-timestamp", dates_raw="Oct 24, 2026"))
    assert item.starts_on == date(2026, 10, 24)


# --- the specific messes the plan names -----------------------------------


def test_a_page_that_fails_to_load_is_flagged_not_lost():
    candidate = Candidate(title="X", url="https://x.devpost.com/",
                          location_raw="Sheridan College", dates_raw="Oct 24, 2026")
    reading = Reading(candidate=candidate, facts=None, error="timed out after 240s")
    item = one(candidate, reading)
    assert "read_failed" in codes(item)
    assert item.needs_attention
    assert "timed out" in [f.detail for f in item.flags if f.code == "read_failed"][0]


def test_tagged_for_a_city_but_actually_online_is_flagged():
    # The plan calls this one out by name.
    item = one(Candidate(title="X", url="https://x.devpost.com/", location_raw="Toronto, ON",
                         dates_raw="Oct 24, 2026", format_raw="online"))
    assert item.bucket == "online-gta"          # kept, labelled apart
    assert "online_but_placed" in codes(item)
    assert item.needs_attention


def test_a_source_and_page_disagreeing_about_format_is_flagged():
    candidate = Candidate(title="X", url="https://x.devpost.com/", location_raw="Toronto, ON",
                          dates_raw="Oct 24, 2026", format_raw="in-person")
    reading = Reading(candidate=candidate, facts=ListingFacts.model_validate({
        "title": None, "location_text": "Toronto, ON", "event_format": "online",
        "format_evidence": "fully virtual", "dates_text": None, "deadline_text": None,
        "eligibility_text": "anyone", "themes": [], "beginner_friendly": "unstated",
        "beginner_evidence": None}))
    item = one(candidate, reading)
    assert "format_conflict" in codes(item)


def test_a_deadline_after_the_event_ends_is_flagged():
    candidate = Candidate(title="X", url="https://x.devpost.com/", location_raw="Toronto, ON",
                          dates_raw="Oct 24 - 25, 2026")
    reading = Reading(candidate=candidate, facts=ListingFacts.model_validate({
        "title": None, "location_text": "Toronto, ON", "event_format": "in-person",
        "format_evidence": None, "dates_text": None,
        "deadline_text": "December 1, 2026", "eligibility_text": "anyone",
        "themes": [], "beginner_friendly": "unstated", "beginner_evidence": None}))
    assert "deadline_after_event" in codes(one(candidate, reading))


def test_a_read_page_stating_no_eligibility_is_flagged():
    candidate = Candidate(title="X", url="https://x.devpost.com/", location_raw="Toronto, ON",
                          dates_raw="Oct 24, 2026")
    reading = Reading(candidate=candidate, facts=ListingFacts.model_validate({
        "title": None, "location_text": "Toronto, ON", "event_format": "in-person",
        "format_evidence": None, "dates_text": None, "deadline_text": None,
        "eligibility_text": None, "themes": [], "beginner_friendly": "unstated",
        "beginner_evidence": None}))
    assert "missing_eligibility" in codes(one(candidate, reading))


# --- duplicates across runs -----------------------------------------------


@pytest.mark.parametrize(
    "title,stem",
    [("DeerHacks V (2026)", "deerhacks"), ("DeerHacks 2023", "deerhacks"),
     ("DeerHacks", "deerhacks"), ("BearHacks 2027", "bearhacks"),
     ("Hack the 6ix", "hack the 6ix")],
)
def test_a_series_reduces_to_one_stem(title, stem):
    assert series_stem(title) == stem


def test_listings_in_the_same_series_flag_each_other():
    series = [
        Candidate(title="DeerHacks V (2026)", url="https://a.devpost.com/", location_raw="Toronto"),
        Candidate(title="DeerHacks 2023", url="https://b.devpost.com/", location_raw="Toronto"),
    ]
    found = near_duplicates(series)
    assert set(found) == {c.key for c in series}
    item = one(series[0], duplicates=found)
    assert "possible_duplicate" in codes(item)


def test_near_duplicates_are_flagged_never_merged():
    series = [
        Candidate(title="DeerHacks V", url="https://a.devpost.com/", location_raw="Toronto",
                  dates_raw="Oct 24, 2026"),
        Candidate(title="DeerHacks V (2026)", url="https://b.devpost.com/", location_raw="Toronto",
                  dates_raw="Oct 24, 2026"),
    ]
    screened = screen_all(series, CONFIG, GTA_MATCHER, {}, TODAY)
    assert len(screened) == 2                       # both survive
    assert all("possible_duplicate" in codes(i) for i in screened)


def test_a_unique_title_is_not_flagged_as_a_duplicate():
    solo = [Candidate(title="LarpHacks", url="https://a.devpost.com/", location_raw="Toronto")]
    assert near_duplicates(solo) == {}


def test_a_title_that_warns_about_itself_is_flagged():
    # A real Devpost listing: "NOT DeerHacks V (OLD DEVPOST; DO NOT USE)".
    assert is_suspect_title("NOT DeerHacks V (OLD DEVPOST; DO NOT USE)")
    item = one(Candidate(title="NOT DeerHacks V (OLD DEVPOST; DO NOT USE)",
                         url="https://x.devpost.com/", location_raw="Toronto",
                         dates_raw="Oct 24, 2026"))
    assert "suspect_title" in codes(item)
    assert item.needs_attention


# --- the flag catalogue itself --------------------------------------------


def test_every_catalogued_flag_has_a_severity_and_wording():
    for code, (severity, description) in CATALOGUE.items():
        assert severity in ("info", "warn", "attention")
        assert description.strip()


def test_an_uncatalogued_code_degrades_to_a_warning_rather_than_raising():
    assert flag("something_new_and_unknown").severity == "warn"


def test_needs_attention_is_only_true_for_attention_severity():
    assert not needs_attention((flag("assumed_deadline"), flag("not_read")))
    assert needs_attention((flag("assumed_deadline"), flag("read_failed")))
