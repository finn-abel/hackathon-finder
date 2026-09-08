"""The classifier's spec: messy strings in, deterministic verdicts out."""

import pytest

from core.location import (
    GTA_MATCHER,
    classify,
    detect_format,
    find_places,
    matcher_for,
    target_matcher,
)


@pytest.mark.parametrize(
    "raw,in_area,event_format",
    [
        ("Toronto, ON (in-person)", True, "in-person"),
        ("hybrid — online + Vaughan", True, "hybrid"),
        ("Mississauga, Ontario, Canada", True, "in-person"),
        ("In-person @ North York", True, "in-person"),
        ("Scarborough Town Centre", True, "in-person"),
        ("Online", False, "online"),
        ("Virtual / Remote", False, "online"),
        ("Waterloo, ON", False, "unknown"),
        ("Ottawa, Canada (in-person)", False, "in-person"),
        ("", False, "unknown"),
    ],
)
def test_messy_strings_classify_correctly(raw, in_area, event_format):
    verdict = classify(raw)
    assert verdict.in_area is in_area
    assert verdict.event_format == event_format


def test_hybrid_with_a_gta_place_is_in_area():
    # The point of the "hybrid — online + Vaughan" case: online-ness must not
    # cancel out a real GTA location.
    verdict = classify("hybrid — online + Vaughan")
    assert verdict.in_area
    assert [p.name for p in verdict.places] == ["Vaughan"]
    assert verdict.event_format == "hybrid"


def test_online_plus_a_place_reads_as_hybrid_without_the_word():
    assert detect_format("Online and in Toronto", has_place=True) == "hybrid"


def test_longer_place_names_win_over_the_names_inside_them():
    assert [p.name for p in find_places("North York, Toronto")] == ["North York", "Toronto"]
    assert [p.name for p in find_places("East York")] == ["East York"]


def test_places_are_returned_in_the_order_they_appear():
    assert [p.name for p in find_places("Oshawa, Ajax and Whitby")] == ["Oshawa", "Ajax", "Whitby"]


def test_a_place_named_twice_is_only_counted_once():
    assert [p.name for p in find_places("Toronto — downtown Toronto")] == ["Toronto"]


@pytest.mark.parametrize(
    "raw",
    [
        "Brock University, St. Catharines",
        "Milton Keynes, UK",
        "York University Student Centre",
        "123 King Street, Hamilton",
    ],
)
def test_ambiguous_names_followed_by_an_institution_or_street_are_not_places(raw):
    assert not classify(raw).in_area


def test_the_ambiguous_guard_does_not_block_the_real_municipality():
    assert classify("Milton, ON").in_area
    assert classify("King City, Ontario").in_area
    assert classify("Brock Township").in_area


def test_verdict_explains_itself():
    assert "Toronto" in classify("Toronto, ON").reason
    assert "online only" in classify("Fully remote").reason


# --- general mode ---------------------------------------------------------


def test_general_mode_matches_its_own_target_area():
    waterloo = target_matcher("Waterloo, ON")
    assert classify("Waterloo, ON (in-person)", waterloo).in_area
    assert classify("University of Waterloo", waterloo).in_area
    assert not classify("Toronto, ON", waterloo).in_area


def test_general_mode_ignores_province_and_country_qualifiers():
    # "ON" must not become a matchable name, or everything in Ontario matches.
    matcher = target_matcher("Waterloo, ON")
    assert "on" not in matcher.index
    assert not classify("London, ON", matcher).in_area


def test_general_mode_can_widen_with_nearby_names():
    matcher = target_matcher("Waterloo, ON", nearby=("Kitchener", "Cambridge"))
    assert classify("Kitchener, Ontario", matcher).in_area


def test_a_location_of_only_qualifiers_is_rejected():
    with pytest.raises(ValueError):
        target_matcher("ON, Canada")


def test_matcher_for_picks_the_right_engine():
    assert matcher_for("gta") is GTA_MATCHER
    assert matcher_for("general", "Vancouver, BC").name == "Vancouver, BC"
    with pytest.raises(ValueError):
        matcher_for("general")
    with pytest.raises(ValueError):
        matcher_for("nonsense")


# --- foreign namesakes ----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Milton Keynes, UK",
        "Toronto, Ohio",
        "London, England",
        "Cambridge, Massachusetts",
        "Aurora, Colorado",
        "Newmarket, Suffolk, United Kingdom",
    ],
)
def test_foreign_places_sharing_a_gta_name_are_rejected(raw):
    assert not classify(raw).in_area


def test_a_canadian_marker_beats_the_foreign_guard():
    # "Georgia" is a US state, but this listing says Ontario.
    assert classify("Georgina, Ontario").in_area
    assert classify("Toronto, ON, Canada").in_area


def test_general_mode_does_not_apply_the_foreign_guard():
    # The GTA is Canadian; a user-supplied area can be anywhere.
    austin = target_matcher("Austin, TX")
    assert classify("Austin, Texas (in-person)", austin).in_area


# --- the three-way verdict ------------------------------------------------


@pytest.mark.parametrize(
    "raw,status",
    [
        ("Toronto, ON", "in-area"),
        ("Bramalea City Centre", "in-area"),   # an alias, still confident
        ("Waterloo, ON", "elsewhere"),
        ("Hamilton", "elsewhere"),
        ("Milton Keynes, UK", "elsewhere"),
        ("Online", "online-only"),
        ("Fully remote", "online-only"),
        ("Sheridan College Hazel McCallion Campus", "unclear"),
        ("Bur Oak Secondary School", "unclear"),
        ("Ontario Tech University", "unclear"),
        ("Stackt Marketplace", "unclear"),
        ("", "unclear"),
    ],
)
def test_a_venue_is_unclear_not_a_rejection(raw, status):
    assert classify(raw).status == status


def test_unclear_listings_are_the_ones_worth_reading():
    # These are real Devpost tiles for GTA hackathons whose city never appears.
    assert classify("Sheridan College Hazel McCallion Campus").needs_a_reader
    assert classify("UTM").needs_a_reader
    # A confident verdict either way does not need the AI.
    assert not classify("Toronto, ON").needs_a_reader
    assert not classify("Waterloo, ON").needs_a_reader
    assert not classify("Online").needs_a_reader


def test_general_mode_never_claims_elsewhere():
    # Only the GTA matcher has a gazetteer. A user-supplied area does not know
    # what is next door, so an unrecognised place must stay "unclear".
    matcher = target_matcher("Waterloo, ON")
    assert classify("Toronto, ON", matcher).status == "unclear"
    assert classify("Waterloo, ON", matcher).status == "in-area"


def test_in_area_still_means_what_it_did():
    assert classify("Toronto, ON").in_area
    for raw in ("Waterloo, ON", "Online", "Sheridan College"):
        assert not classify(raw).in_area
