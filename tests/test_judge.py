"""The judging layer. Ranking and caching are code, so they are tested here;
the scoring itself is the model's job and is not asserted on.
"""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.judge import SYSTEM_PROMPT, _facts_for, rank
from core.config import Config
from core.location import GTA_MATCHER
from core.models import Candidate, FitVerdict, Judgement, criteria_fingerprint
from core.screening import screen
from core.store import load_all_judgements, load_judgements, save_judgements

TODAY = date(2026, 9, 8)
CONFIG = Config(criteria="beginner-friendly, in-person")


def verdict(score: int, **kw) -> FitVerdict:
    base = dict(reason="r", supports=[], conflicts=[], missing=[])
    return FitVerdict(score=score, **{**base, **kw})


def item(title="Test Hack", url=None, dates="Oct 24 - 25, 2026", **kw):
    candidate = Candidate(title=title, url=url or f"https://{title.replace(' ', '')}.devpost.com/",
                          location_raw="Toronto, ON", dates_raw=dates, **kw)
    return screen(candidate, CONFIG, GTA_MATCHER, None, TODAY)


# --- the criteria fingerprint ---------------------------------------------


def test_the_fingerprint_ignores_case_and_spacing():
    assert criteria_fingerprint("Beginner-Friendly, In Person") == \
           criteria_fingerprint("beginner-friendly,  in person")


def test_a_different_criteria_sentence_gets_a_different_fingerprint():
    assert criteria_fingerprint("beginner-friendly") != criteria_fingerprint("hardware focused")


# --- the cache ------------------------------------------------------------


def test_judgements_for_two_criteria_coexist(tmp_path: Path):
    # Keying on the candidate alone silently discarded the first criteria's
    # work the moment you judged under a second one.
    path = tmp_path / "j.json"
    same_url = "https://x.devpost.com"
    save_judgements([
        Judgement(key=same_url, criteria_hash="aaa", verdict=verdict(5)),
        Judgement(key=same_url, criteria_hash="bbb", verdict=verdict(1)),
    ], path)
    assert len(load_all_judgements(path)) == 2


def test_loading_returns_only_the_criteria_you_asked_for(tmp_path: Path):
    path = tmp_path / "j.json"
    save_judgements([
        Judgement(key="https://a.devpost.com", criteria_hash="aaa", verdict=verdict(5)),
        Judgement(key="https://b.devpost.com", criteria_hash="bbb", verdict=verdict(2)),
    ], path)
    assert list(load_judgements("aaa", path)) == ["https://a.devpost.com"]
    assert load_judgements("nope", path) == {}


def test_re_judging_the_same_pair_replaces_rather_than_duplicates(tmp_path: Path):
    path = tmp_path / "j.json"
    old = Judgement(key="https://a.devpost.com", criteria_hash="aaa", verdict=verdict(2))
    new = Judgement(key="https://a.devpost.com", criteria_hash="aaa", verdict=verdict(5))
    save_judgements([old, new], path)
    stored = load_all_judgements(path)
    assert len(stored) == 1 and stored[0].score == 5


def test_a_missing_cache_file_is_empty_not_an_error(tmp_path: Path):
    assert load_all_judgements(tmp_path / "absent.json") == ()


# --- ranking is code ------------------------------------------------------


def test_higher_scores_rank_first():
    a, b, c = item("Alpha"), item("Bravo"), item("Charlie")
    judgements = {
        a.candidate.key: Judgement(key=a.candidate.key, criteria_hash="h", verdict=verdict(2)),
        b.candidate.key: Judgement(key=b.candidate.key, criteria_hash="h", verdict=verdict(5)),
        c.candidate.key: Judgement(key=c.candidate.key, criteria_hash="h", verdict=verdict(3)),
    }
    assert [s.title for s, _ in rank([a, b, c], judgements)] == ["Bravo", "Charlie", "Alpha"]


def test_ties_break_on_the_nearer_deadline():
    soon, later = item("Soon", dates="Sep 20, 2026"), item("Later", dates="Dec 20, 2026")
    judgements = {
        later.candidate.key: Judgement(key=later.candidate.key, criteria_hash="h", verdict=verdict(4)),
        soon.candidate.key: Judgement(key=soon.candidate.key, criteria_hash="h", verdict=verdict(4)),
    }
    assert [s.title for s, _ in rank([later, soon], judgements)] == ["Soon", "Later"]


def test_ranking_is_stable_regardless_of_input_order():
    a, b = item("Alpha"), item("Bravo")
    judgements = {
        a.candidate.key: Judgement(key=a.candidate.key, criteria_hash="h", verdict=verdict(4)),
        b.candidate.key: Judgement(key=b.candidate.key, criteria_hash="h", verdict=verdict(4)),
    }
    assert rank([a, b], judgements)[0][0].title == rank([b, a], judgements)[0][0].title


def test_unjudged_items_are_left_out_rather_than_ranked_last():
    a, b = item("Alpha"), item("Bravo")
    judgements = {a.candidate.key: Judgement(key=a.candidate.key, criteria_hash="h", verdict=verdict(3))}
    assert [s.title for s, _ in rank([a, b], judgements)] == ["Alpha"]


# --- the schema and the prompt --------------------------------------------


@pytest.mark.parametrize("bad", [-1, 6, 100])
def test_scores_outside_the_rubric_are_rejected(bad):
    with pytest.raises(ValidationError):
        verdict(bad)


def test_the_prompt_forbids_re_judging_location_and_deadlines():
    # Those belong to core.gta and core.dates. If this drifts, the split has.
    # Whitespace is collapsed so re-wrapping the prompt cannot break this.
    lowered = " ".join(SYSTEM_PROMPT.lower().split())
    assert "do not re-litigate" in lowered
    assert "already been confirmed" in lowered
    assert "already been checked" in lowered


def test_the_prompt_forbids_rewarding_an_empty_listing():
    lowered = " ".join(SYSTEM_PROMPT.lower().split())
    assert "should not score highly just because" in lowered


def test_the_facts_block_carries_the_criteria_and_the_listing():
    text = _facts_for(item("Alpha", themes=("Web",)), None, "beginner-friendly, in-person")
    assert "beginner-friendly, in-person" in text
    assert "Alpha" in text and "Toronto, ON" in text and "Web" in text


def test_unread_listings_say_so_instead_of_looking_empty():
    # "not stated" and "not read in full" mean different things to a judge.
    text = _facts_for(item("Alpha"), None, "x")
    assert "listing not read in full" in text
