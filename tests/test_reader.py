"""The extraction schema is a contract: it must allow "the page didn't say",
and must refuse anything outside the vocabulary the next steps understand.
"""

import pytest
from pydantic import ValidationError

from agent.reader import EXTRACTION_TASK, ListingFacts
from core.dates import parse_deadline
from core.location import classify

FULL = {
    "title": "Sheridan Datathon 2026",
    "location_text": "Sheridan HMC Campus (Mississauga, ON)",
    "event_format": "in-person",
    "format_evidence": "This is an in-person event at Sheridan HMC Campus.",
    "dates_text": "Nov 7 - 8, 2026",
    "deadline_text": "by 12:00 PM EST on Sunday, November 8, 2026",
    "eligibility_text": "Participants must be 18+ and enrolled in an Ontario post-secondary institution.",
    "themes": ["Machine Learning/AI"],
    "beginner_friendly": "unstated",
    "beginner_evidence": None,
}

EMPTY = {
    "title": None,
    "location_text": None,
    "event_format": "unstated",
    "format_evidence": None,
    "dates_text": None,
    "deadline_text": None,
    "eligibility_text": None,
    "themes": [],
    "beginner_friendly": "unstated",
    "beginner_evidence": None,
}


def test_a_full_extraction_validates():
    facts = ListingFacts.model_validate(FULL)
    assert facts.location_text == "Sheridan HMC Campus (Mississauga, ON)"
    assert facts.eligibility_text.startswith("Participants must be 18+")


def test_a_page_that_states_nothing_is_valid():
    # "not stated" must be expressible, or the model is pushed into inventing.
    facts = ListingFacts.model_validate(EMPTY)
    assert facts.location_text is None
    assert facts.event_format == "unstated"


@pytest.mark.parametrize("bad", ["in person", "IRL", "virtual", "", "maybe"])
def test_formats_outside_the_vocabulary_are_rejected(bad):
    with pytest.raises(ValidationError):
        ListingFacts.model_validate({**FULL, "event_format": bad})


@pytest.mark.parametrize("bad", ["true", "probably", "beginner", ""])
def test_beginner_answers_outside_the_vocabulary_are_rejected(bad):
    with pytest.raises(ValidationError):
        ListingFacts.model_validate({**FULL, "beginner_friendly": bad})


def test_the_task_names_the_page_being_read():
    task = EXTRACTION_TASK.format(url="https://example.devpost.com/")
    assert "https://example.devpost.com/" in task


def test_the_task_forbids_judging():
    # The AI-vs-code split has to survive contact with the prompt.
    task = EXTRACTION_TASK.lower()
    assert "extraction, not judgement" in task
    assert "never guess" in task
    assert "deadline has passed" in task  # explicitly handed to code


def test_extracted_facts_feed_the_deterministic_modules():
    # The whole point of the split: messy prose in, code decides.
    facts = ListingFacts.model_validate(FULL)

    verdict = classify(facts.location_text)
    assert verdict.status == "in-area"
    assert [p.name for p in verdict.places] == ["Mississauga"]

    from datetime import date

    deadline = parse_deadline(facts.deadline_text, today=date(2026, 9, 8))
    assert deadline.date == date(2026, 11, 8)
    assert deadline.status == "upcoming"


def test_a_venue_only_tile_is_what_reading_resolves():
    # Before the read, code could not place this listing.
    assert classify("Sheridan College Hazel McCallion Campus").needs_a_reader
    # After the read, it can.
    assert classify(FULL["location_text"]).in_area


# --- the deadline / date-range distinction --------------------------------


def test_a_missing_deadline_never_borrows_the_event_dates():
    # The tile's "submission period" is when the event RUNS. Presenting it as
    # a submission deadline would be a confident wrong answer.
    from core.models import Candidate, Reading

    candidate = Candidate(
        title="BearHacks 2027",
        url="https://bearhacks2027.devpost.com/",
        dates_raw="Apr 23 - 25, 2027",
    )
    reading = Reading(
        candidate=candidate,
        facts=ListingFacts.model_validate({**EMPTY, "deadline_text": None}),
    )
    assert reading.deadline_text == ""          # honest about not knowing
    assert reading.dates_text == "Apr 23 - 25, 2027"  # the range is still kept


def test_the_page_deadline_wins_when_there_is_one():
    from core.models import Candidate, Reading

    reading = Reading(
        candidate=Candidate(title="X", url="https://x.devpost.com/", dates_raw="Nov 7 - 8, 2026"),
        facts=ListingFacts.model_validate({**EMPTY, "deadline_text": "by 12:00 PM EST Nov 8"}),
    )
    assert reading.deadline_text == "by 12:00 PM EST Nov 8"


def test_tile_themes_win_over_the_page_read():
    # Devpost's own tags came off the DOM; the page read is the fallback.
    from core.models import Candidate, Reading

    candidate = Candidate(title="X", url="https://x.devpost.com/", themes=("Web", "Design"))
    reading = Reading(candidate=candidate, facts=ListingFacts.model_validate(
        {**EMPTY, "themes": ["Something Else"]}))
    assert reading.themes == ("Web", "Design")


def test_the_page_location_wins_over_the_tile_venue():
    from core.models import Candidate, Reading

    candidate = Candidate(title="X", url="https://x.devpost.com/",
                          location_raw="Sheridan College Hazel McCallion Campus")
    reading = Reading(candidate=candidate, facts=ListingFacts.model_validate(
        {**EMPTY, "location_text": "Sheridan HMC Campus (Mississauga, ON)"}))
    assert reading.location_text == "Sheridan HMC Campus (Mississauga, ON)"
    assert classify(reading.location_text).in_area


def test_the_prompt_demands_a_city_not_just_a_venue():
    from agent.reader import EXTRACTION_TASK

    task = EXTRACTION_TASK.lower()
    assert "city or municipality" in task
    assert "campus or school name alone is not enough" in task
