"""Merging the same event across sources."""

from core.merge import merge, title_key
from core.models import Candidate


def devpost(title, **kw):
    return Candidate(title=title, url=f"https://{title.lower()}.devpost.com/", source="devpost", **kw)


def mlh(title, **kw):
    return Candidate(title=title, url=f"https://www.mlh.com/events/{title.lower()}",
                     source="mlh", starts_at="2026-04-24T00:00:00Z", format_raw="in-person", **kw)


def test_titles_fold_for_comparison():
    assert title_key("Hack the 6ix!") == title_key("hack the 6ix")
    assert title_key("BearHacks 2026") != title_key("BearHacks 2027")


def test_the_same_event_from_two_sources_becomes_one_record():
    merged = merge([devpost("BearHacks"), mlh("BearHacks")])
    assert len(merged) == 1


def test_the_richer_record_wins_and_remembers_the_other_source():
    merged = merge([devpost("BearHacks", location_raw="Sheridan College"), mlh("BearHacks")])[0]
    # MLH carries ISO dates and an explicit format, so it is the better record.
    assert merged.source == "mlh"
    assert merged.also_in == ("devpost",)
    assert merged.starts_at


def test_a_source_order_swap_gives_the_same_winner():
    forward = merge([devpost("BearHacks"), mlh("BearHacks")])[0]
    backward = merge([mlh("BearHacks"), devpost("BearHacks")])[0]
    assert forward.source == backward.source == "mlh"


def test_near_miss_titles_are_kept_apart_on_purpose():
    # Fusing "DeerHacks V" with "DeerHacks V (2026)" would be a guess.
    merged = merge([devpost("DeerHacks V"), devpost("DeerHacks V (2026)")])
    assert len(merged) == 2


def test_untitled_records_are_dropped():
    assert merge([Candidate(title="   ", url="https://x.devpost.com/")]) == ()
