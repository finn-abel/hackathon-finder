"""Step 8: score every screened hackathon against your criteria and rank it.

    uv run judge.py                             # judge the shortlist
    uv run judge.py --include-past              # judge past events too
    uv run judge.py --criteria "hardware, open to professionals"
    uv run judge.py --buckets primary online-gta unresolved
    uv run judge.py --refresh                   # ignore the cache

Editing `criteria` invalidates the cache automatically: judgements are stored
against a fingerprint of the criteria sentence they were made under.

No browser is opened. The AI scores; `agent.judge.rank` does the ordering.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from agent.judge import judge_all, rank
from core.config import build_parser, config_from_args
from core.location import matcher_for
from core.models import Judgement, criteria_fingerprint
from core.results import build_results
from core.screening import Bucket, Screened, screen_all
from core.store import (
    load_all_judgements, candidates_path, load_candidates, load_judgements, load_readings,
    save_judgements, save_results,
)

BUCKETS: tuple[Bucket, ...] = ("primary", "online-gta", "unresolved", "excluded")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        parents=[build_parser(add_help=False)], description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--buckets", nargs="+", choices=BUCKETS,
                        default=["primary", "online-gta"], help="which buckets to judge")
    parser.add_argument("--include-past", action="store_true")
    parser.add_argument("--today", help="pretend today is this date (YYYY-MM-DD)")
    parser.add_argument("--refresh", action="store_true", help="re-judge even if cached")
    parser.add_argument("--model", help="override the judging model")
    parser.add_argument("--top", type=int, help="only print the top N")
    parser.add_argument("--out", default="results.json", help="where to write the results file")
    parser.add_argument("--no-save", action="store_true", help="print only, write nothing")
    parser.add_argument("--include-excluded", action="store_true",
                        help="also write rows code ruled out (they are summarised otherwise)")
    return parser.parse_args()


def print_ranked(index: int, item: Screened, judgement: Judgement) -> None:
    verdict = judgement.verdict
    if verdict is None:
        print(f"\n{index:>3}. [--] {item.candidate.title[:60]}   FAILED: {judgement.error}")
        return

    stars = "★" * verdict.score + "·" * (5 - verdict.score)
    when = item.starts_on.isoformat() if item.starts_on else "date unknown"
    print(f"\n{index:>3}. {stars}  {item.candidate.title[:58]}")
    print(f"      {when}  ·  {item.location.raw[:44] or 'location not stated'}"
          f"  ·  {item.bucket}")
    print(f"      {verdict.reason}")
    if verdict.conflicts:
        print(f"      against: {'; '.join(verdict.conflicts)}")
    if verdict.missing:
        print(f"      unknown: {', '.join(verdict.missing)}")
    print(f"      {item.candidate.url[:88]}")


async def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    if args.include_past:
        config = config.model_copy(update={
            "filters": config.filters.model_copy(
                update={"include_past": True, "timeframe_months": 24})
        })
    matcher = matcher_for(config.mode, config.location, config.nearby)
    today = date.fromisoformat(args.today) if args.today else date.today()

    candidates = load_candidates(candidates_path(config.mode, config.location))
    readings = load_readings()
    screened = screen_all(candidates, config, matcher, readings, today)
    wanted = [s for s in screened if s.bucket in args.buckets]

    fingerprint = criteria_fingerprint(config.criteria)
    cached = {} if args.refresh else load_judgements(fingerprint)
    todo = [s for s in wanted if s.candidate.key not in cached]

    print(f"Criteria  {config.criteria!r}")
    print(f"          fingerprint {fingerprint}")
    print(f"Buckets   {', '.join(args.buckets)}")
    print(f"Scope     {len(wanted)} to rank — {len(cached)} cached, {len(todo)} to judge\n")

    if todo:
        model = args.model or config.judge.model
        print(f"Judging with {model} ({config.judge.concurrency} at a time)...")

        def tick(item: Screened, judgement: Judgement) -> None:
            mark = judgement.score if judgement.ok else "!"
            print(f"  [{mark}] {item.candidate.title[:56]}")

        fresh = await judge_all(todo, config.criteria, readings,
                                model, config.judge.concurrency, on_done=tick)
        # Keep every other criteria's work alongside this run's.
        save_judgements([*load_all_judgements(), *fresh])
        cached.update({j.key: j for j in fresh})

    ranked = rank(wanted, cached)

    if not args.no_save:
        judged_keys = {item.candidate.key for item, _ in ranked}
        kept = [
            s for s in screened
            if s.bucket != "excluded" or args.include_excluded
        ]
        unranked = [s for s in kept if s.candidate.key not in judged_keys]
        results = build_results(
            ranked, unranked, screened, config, matcher.name,
            fingerprint, readings, today,
        )
        written = save_results(results, Path(args.out))
        print(f"Wrote {len(results.results)} rows to {written.name} "
              f"(schema {results.schema_version})")

    shown = ranked[: args.top] if args.top else ranked
    print(f"\n{'=' * 78}")
    print(f"Ranked by fit against your criteria ({len(ranked)})")
    print("=" * 78)
    for index, (item, judgement) in enumerate(shown, start=1):
        print_ranked(index, item, judgement)


if __name__ == "__main__":
    asyncio.run(main())
