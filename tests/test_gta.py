"""The GTA definition is a constant, so these tests are the spec for it."""

import pytest

from core.gta import (
    GTA_AREA_NAME,
    GTA_REGIONS,
    PLACE_KEYS,
    is_in_gta,
    lookup,
    municipalities,
    normalize,
    region_of,
)

IN_GTA = [
    ("Mississauga", "Peel"),
    ("Scarborough", "Toronto"),
    ("Oshawa", "Durham"),
    ("Toronto", "Toronto"),
    ("North York", "Toronto"),
    ("Etobicoke", "Toronto"),
    ("East York", "Toronto"),
    ("Brampton", "Peel"),
    ("Markham", "York Region"),
    ("Vaughan", "York Region"),
    ("Richmond Hill", "York Region"),
    ("Newmarket", "York Region"),
    ("Aurora", "York Region"),
    ("Pickering", "Durham"),
    ("Ajax", "Durham"),
    ("Whitby", "Durham"),
    ("Oakville", "Halton"),
    ("Burlington", "Halton"),
    ("Milton", "Halton"),
    ("Halton Hills", "Halton"),
]

OUTSIDE_GTA = [
    "Waterloo",
    "Ottawa",
    "Hamilton",
    "Kitchener",
    "Guelph",
    "Barrie",
    "London",
    "Montreal",
    "Vancouver",
    "Boston",
]


@pytest.mark.parametrize("name,region", IN_GTA)
def test_known_gta_places_resolve_to_their_region(name, region):
    assert is_in_gta(name)
    assert region_of(name) == region


@pytest.mark.parametrize("name", OUTSIDE_GTA)
def test_places_outside_the_gta_are_rejected(name):
    assert not is_in_gta(name)
    assert lookup(name) is None
    assert region_of(name) is None


def test_hamilton_is_excluded_even_though_it_is_next_door():
    # The GTHA includes Hamilton; the GTA does not. Easy thing to get wrong.
    assert not is_in_gta("Hamilton")


@pytest.mark.parametrize(
    "written,canonical",
    [
        ("mississauga", "Mississauga"),
        ("MISSISSAUGA", "Mississauga"),
        ("  North   York  ", "North York"),
        ("City of Toronto", "Toronto"),
        ("Town of Oakville", "Oakville"),
        ("Whitchurch Stouffville", "Whitchurch-Stouffville"),
        ("whitchurch-stouffville", "Whitchurch-Stouffville"),
        ("Montréal", None),
    ],
)
def test_normalization_handles_case_spacing_punctuation_and_qualifiers(written, canonical):
    place = lookup(written)
    assert (place.name if place else None) == canonical


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("Woodbridge", "Vaughan"),
        ("Thornhill", "Markham"),
        ("Port Credit", "Mississauga"),
        ("Bolton", "Caledon"),
        ("Georgetown", "Halton Hills"),
        ("Bowmanville", "Clarington"),
        ("Port Perry", "Scugog"),
        ("Stouffville", "Whitchurch-Stouffville"),
        ("Oak Ridges", "Richmond Hill"),
        ("The 6ix", "Toronto"),
        ("T.O.", "Toronto"),
    ],
)
def test_aliases_resolve_to_their_canonical_municipality(alias, canonical):
    assert lookup(alias).name == canonical


def test_region_names_resolve_as_regions():
    assert lookup("Peel Region").kind == "region"
    assert lookup("Durham").kind == "region"
    assert lookup("Regional Municipality of Halton").name == "Halton"


def test_york_is_ambiguous_and_resolves_to_the_toronto_district():
    # "York" is both a Toronto district and a region. The district wins the
    # bare name; "York Region" is unambiguous. Either way it is in the GTA.
    assert lookup("York").kind == "district"
    assert lookup("York").region == "Toronto"
    assert lookup("York Region").kind == "region"


def test_the_area_itself_is_recognised():
    for name in ("GTA", "gta", "Greater Toronto Area"):
        place = lookup(name)
        assert place.kind == "area"
        assert place.name == GTA_AREA_NAME


def test_lookup_records_what_the_caller_actually_wrote():
    assert lookup("  MISSISSAUGA ").matched_as == "  MISSISSAUGA "


def test_empty_and_junk_input_is_not_in_the_gta():
    for junk in ("", "   ", "!!!", "online", "TBD"):
        assert not is_in_gta(junk)


def test_every_indexed_key_is_normalized():
    # Guards the index build: a raw name leaking in would never match a lookup.
    for key in PLACE_KEYS:
        assert key == normalize(key)


def test_the_definition_covers_the_five_regions():
    assert [r.name for r in GTA_REGIONS] == ["Toronto", "Peel", "York Region", "Durham", "Halton"]
    assert len(municipalities()) == sum(len(r.municipalities) for r in GTA_REGIONS)
