"""The results.json contract. The dashboard and anyone reading the file
depend on this shape, so it is pinned here.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from agent.judge import rank
from core.config import Config, Filters
from core.location import GTA_MATCHER
from core.models import Candidate, FitVerdict, Judgement, ListingFacts, Reading
from core.results import LEGEND, SCHEMA_VERSION, Results, build_results
from core.screening import screen_all
from core.store import load_results, save_results

TODAY = date(2026, 9, 8)
CONFIG = Config(criteria="beginner-friendly, in-person",
                filters=Filters(include_past=True, timeframe_months=24))


def candidate(title="Alpha", location="Toronto, ON", **kw) -> Candidate:
    return Candidate(title=title, url=f"https://{title.lower()}.devpost.com/",
                     location_raw=location, dates_raw="Oct 24 - 25, 2026", **kw)


def judged(key: str, score: int = 4) -> Judgement:
    return Judgement(key=key, criteria_hash="abc123", verdict=FitVerdict(
        score=score, reason="in-person in Toronto, tagged Beginner Friendly",
        supports=["in-person"], conflicts=[], missing=["eligibility"]))


def build(candidates, judgements=None, readings=None, include_excluded=False) -> Results:
    screened = screen_all(candidates, CONFIG, GTA_MATCHER, readings or {}, TODAY)
    judgements = judgements or {}
    ranked = rank([s for s in screened if s.bucket == "primary"], judgements)
    judged_keys = {i.candidate.key for i, _ in ranked}
    kept = [s for s in screened if s.bucket != "excluded" or include_excluded]
    unranked = [s for s in kept if s.candidate.key not in judged_keys]
    return build_results(ranked, unranked, screened, CONFIG, GTA_MATCHER.name,
                         "abc123", readings or {}, TODAY)


# --- well-formed and round-trips ------------------------------------------


def test_the_file_round_trips_through_disk(tmp_path: Path):
    results = build([candidate()], {f"https://alpha.devpost.com": judged("https://alpha.devpost.com")})
    path = save_results(results, tmp_path / "results.json")
    assert json.loads(path.read_text())          # valid JSON
    assert load_results(path).schema_version == SCHEMA_VERSION


def test_dates_serialise_as_iso_strings(tmp_path: Path):
    path = save_results(build([candidate()]), tmp_path / "r.json")
    row = json.loads(path.read_text())["results"][0]
    assert row["derived"]["starts_on"] == "2026-10-24"
    assert row["derived"]["ends_on"] == "2026-10-25"


def test_a_missing_results_file_says_what_to_run(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="judge.py"):
        load_results(tmp_path / "absent.json")


# --- self-describing ------------------------------------------------------


def test_every_vocabulary_the_file_uses_is_documented_in_the_legend():
    # This is what "self-describing" has to mean: no value appears in the file
    # that the file itself does not explain.
    results = build([
        candidate("Alpha", "Toronto, ON"),
        candidate("Bravo", "Sheridan College Hazel McCallion Campus"),
        candidate("Charlie", "Waterloo, ON"),
    ], include_excluded=True)

    for row in results.results:
        assert row.derived.bucket in LEGEND["bucket"]
        assert row.derived.location_status in LEGEND["location_status"]
        assert row.derived.deadline_status in LEGEND["deadline_status"]


def test_the_legend_explains_where_each_part_came_from():
    assert set(LEGEND["provenance"]) == {"raw", "derived", "fit"}
    assert "Deterministic" in LEGEND["provenance"]["derived"]
    assert "judgement" in LEGEND["provenance"]["fit"].lower()


def test_the_run_context_can_reproduce_the_run():
    results = build([candidate()])
    run = results.run
    assert run.mode == "gta" and run.target_area == "Greater Toronto Area"
    assert run.criteria == CONFIG.criteria and run.criteria_hash == "abc123"
    assert run.today == TODAY
    assert "timeframe_months" in run.filters


# --- provenance is kept separate ------------------------------------------


def test_raw_holds_the_source_wording_and_derived_holds_code_s_verdict():
    results = build([candidate("Alpha", "UofT Mississauga - Deerfield Hall")])
    row = results.results[0]
    assert row.raw.location == "UofT Mississauga - Deerfield Hall"   # untouched
    assert row.derived.places == ("Mississauga",)                    # code's call
    assert row.derived.regions == ("Peel",)


def test_an_unread_listing_says_so_rather_than_showing_blank_facts():
    row = build([candidate()]).results[0]
    assert row.raw.was_read is False
    assert row.raw.eligibility is None


def test_a_read_listing_carries_the_buried_fields():
    c = candidate("Alpha", "Sheridan College Hazel McCallion Campus")
    reading = Reading(candidate=c, facts=ListingFacts.model_validate({
        "title": "Alpha", "location_text": "Sheridan HMC Campus (Mississauga, ON)",
        "event_format": "in-person", "format_evidence": None, "dates_text": None,
        "deadline_text": "by 12:00 PM EST on November 8, 2026",
        "eligibility_text": "18+ and enrolled in an Ontario post-secondary institution",
        "themes": [], "beginner_friendly": "unstated", "beginner_evidence": None}))
    row = build([c], readings={c.key: reading}).results[0]
    assert row.raw.was_read is True
    assert row.raw.eligibility.startswith("18+")
    assert row.derived.in_area is True          # code placed it from the read
    assert row.derived.deadline_date == date(2026, 11, 8)


def test_fit_is_absent_rather_than_zero_when_nothing_judged_it():
    # A missing judgement must not read as "scored 0".
    assert build([candidate()]).results[0].fit is None


# --- ranking and counts ---------------------------------------------------


def test_ranked_rows_are_numbered_and_come_first():
    key = "https://alpha.devpost.com"
    results = build([candidate("Alpha"), candidate("Bravo")], {key: judged(key, 5)})
    assert results.results[0].rank == 1
    assert results.results[0].title == "Alpha"
    assert results.results[1].rank is None


def test_excluded_rows_are_summarised_not_listed_by_default():
    results = build([candidate("Alpha"), candidate("Charlie", "Waterloo, ON")])
    titles = {row.title for row in results.results}
    assert "Charlie" not in titles
    assert results.run.counts["excluded"] == 1
    assert results.run.excluded_reasons == {"location: elsewhere": 1}


def test_excluded_rows_can_be_included_on_request():
    results = build([candidate("Charlie", "Waterloo, ON")], include_excluded=True)
    assert {row.title for row in results.results} == {"Charlie"}


def test_counts_describe_the_whole_run_not_just_the_rows_kept():
    results = build([candidate("Alpha"), candidate("Charlie", "Waterloo, ON")])
    assert results.run.counts["collected"] == 2
    assert results.run.counts["in_file"] == len(results.results) == 1


def test_general_mode_records_its_own_area_in_the_run_context():
    from core.location import target_matcher

    cfg = Config(mode="general", location="Waterloo, ON", criteria="x",
                 filters=Filters(include_past=True, timeframe_months=24))
    screened = screen_all([candidate("Alpha", "Waterloo, ON")], cfg,
                          target_matcher("Waterloo, ON"), {}, TODAY)
    from core.results import build_results

    results = build_results([], screened, screened, cfg, "Waterloo, ON", "h", {}, TODAY)
    assert results.run.mode == "general"
    assert results.run.location == "Waterloo, ON"
    assert results.run.target_area == "Waterloo, ON"
    assert results.results[0].derived.area == "Waterloo, ON"


def test_every_flag_code_the_run_can_emit_is_documented():
    # Same rule as the other vocabularies: nothing appears in the file that
    # the file does not explain.
    from core.flags import CATALOGUE
    from core.results import LEGEND

    assert set(LEGEND["flag"]) == set(CATALOGUE)
    for severity in ("info", "warn", "attention"):
        assert severity in LEGEND["flag_severity"]


def test_flags_and_attention_reach_the_file():
    broken = Candidate(title="Broken", url="https://broken.devpost.com/",
                       location_raw="Toronto, ON", dates_raw="whenever")
    row = build([broken]).results[0]
    assert row.derived.needs_attention is True
    assert "no_parseable_date" in {f.code for f in row.derived.flags}
    assert row.derived.starts_on is None       # not invented


def test_the_run_summarises_flags_across_everything_collected():
    results = build([
        Candidate(title="Broken", url="https://b.devpost.com/", location_raw="Toronto, ON",
                  dates_raw="whenever"),
        candidate("Alpha"),
    ])
    assert results.run.flag_counts["no_parseable_date"] == 1
    assert results.run.counts["needs_attention"] >= 1
