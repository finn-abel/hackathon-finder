"""The date module's spec. `today` is pinned so these never rot."""

from datetime import date

import pytest

from core.dates import parse_deadline, within_months

TODAY = date(2026, 3, 1)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("March 5, 2026", date(2026, 3, 5)),
        ("Mar 5 2026", date(2026, 3, 5)),
        ("5 March 2026", date(2026, 3, 5)),
        ("2026-03-05", date(2026, 3, 5)),
        ("03/05/2026", date(2026, 3, 5)),  # North American order
        ("Submissions close March 5, 2026 at 5:00pm EST", date(2026, 3, 5)),
        ("Deadline: March 5th, 2026", date(2026, 3, 5)),
        ("Registration ends on 2026-03-05T23:59:00Z", date(2026, 3, 5)),
    ],
)
def test_absolute_dates_parse_out_of_messy_text(raw, expected):
    assert parse_deadline(raw, today=TODAY).date == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("today", TODAY),
        ("Ends today", TODAY),
        ("tomorrow", date(2026, 3, 2)),
        ("3 days left", date(2026, 3, 4)),
        ("in 2 weeks", date(2026, 3, 15)),
        ("closes in 1 month", date(2026, 4, 1)),
    ],
)
def test_relative_phrases_resolve_against_today(raw, expected):
    assert parse_deadline(raw, today=TODAY).date == expected


@pytest.mark.parametrize(
    "raw,status",
    [
        ("January 2, 2020", "passed"),
        ("February 28, 2026", "passed"),
        ("March 1, 2026", "soon"),      # today
        ("March 5, 2026", "soon"),      # inside the 7-day window
        ("March 8, 2026", "soon"),      # the window's edge
        ("March 9, 2026", "upcoming"),  # just past it
        ("December 1, 2026", "upcoming"),
        ("Rolling admission", "unknown"),
        ("TBD", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_status_compares_the_date_to_today(raw, status):
    assert parse_deadline(raw, today=TODAY).status == status


def test_a_past_date_reads_as_passed_with_a_negative_distance():
    deadline = parse_deadline("January 2, 2026", today=TODAY)
    assert deadline.status == "passed"
    assert deadline.days_away == -58
    assert not deadline.is_open


def test_a_year_less_date_rolls_forward_rather_than_reading_as_passed():
    # "February 20" in March means next February, not a deadline 9 days gone.
    assert parse_deadline("February 20", today=TODAY).date == date(2027, 2, 20)
    assert parse_deadline("March 20", today=TODAY).date == date(2026, 3, 20)


def test_an_explicit_past_year_is_respected_not_rolled_forward():
    assert parse_deadline("March 20, 2024", today=TODAY).date == date(2024, 3, 20)


@pytest.mark.parametrize("raw", ["Closes at 5pm", "Top 3 teams win", "24 hour event", "Rolling"])
def test_stray_numbers_do_not_become_dates(raw):
    # dateutil's fuzzy parser will happily turn "5pm" into today. It must not.
    assert parse_deadline(raw, today=TODAY).date is None


def test_the_soon_window_is_configurable():
    assert parse_deadline("March 20, 2026", today=TODAY, soon_days=30).status == "soon"
    assert parse_deadline("March 20, 2026", today=TODAY, soon_days=7).status == "upcoming"


def test_unknown_deadlines_count_as_open_so_they_are_not_silently_dropped():
    assert parse_deadline("Rolling admission", today=TODAY).is_open


@pytest.mark.parametrize(
    "raw,months,expected",
    [
        ("March 20, 2026", 3, True),
        ("May 31, 2026", 3, True),
        ("July 1, 2026", 3, False),   # outside the 3-month window
        ("January 2, 2026", 3, False),  # already passed
        ("Rolling", 3, False),          # no date to compare
    ],
)
def test_within_months_backs_the_timeframe_filter(raw, months, expected):
    assert within_months(parse_deadline(raw, today=TODAY), months, today=TODAY) is expected
