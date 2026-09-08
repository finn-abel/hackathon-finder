"""Step 4: collect hackathon candidates from Devpost and print them.

    uv run collect.py                                  # gta mode, from config.yaml
    uv run collect.py --mode general --location "Waterloo, ON"
    uv run collect.py --terms Toronto --max-scrolls 0  # a quick single-term run
    uv run collect.py --all                            # show rejects too

Names and links come off the DOM, not out of a model. The GTA screening is
`core.location` doing exactly what it did in the offline tests.
"""

from __future__ import annotations

import argparse
import asyncio

from agent.devpost import collect, search_terms_for
from agent.session import steel_browser
from core.config import Config, build_parser, config_from_args
from core.location import classify, matcher_for
from core.models import Candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        parents=[build_parser(add_help=False)], description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--terms", nargs="+", help="override the Devpost search terms")
    parser.add_argument("--max-scrolls", type=int, help="how far to scroll each listing")
    parser.add_argument("--all", action="store_true", help="also list what was screened out")
    return parser.parse_args()


def terms_for(config: Config, override: list[str] | None) -> tuple[str, ...]:
    if override:
        return tuple(override)
    if config.collect.search_terms:
        return config.collect.search_terms
    return search_terms_for(config.mode, config.location)


def print_candidate(index: int, candidate: Candidate, reason: str) -> None:
    print(f"\n{index:>2}. {candidate.title}")
    print(f"    {candidate.url}")
    location = candidate.location_raw or "(no location given)"
    print(f"    {location}  —  {reason}")
    details = " | ".join(filter(None, (candidate.status_raw, candidate.deadline_raw)))
    if details:
        print(f"    {details}")
    if candidate.themes:
        print(f"    themes: {', '.join(candidate.themes)}")


async def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    matcher = matcher_for(config.mode, config.location)
    terms = terms_for(config, args.terms)
    max_scrolls = config.collect.max_scrolls if args.max_scrolls is None else args.max_scrolls

    print(f"Mode      {config.mode}  →  {matcher.name}")
    print(f"Searching {', '.join(terms)}  (scrolling up to {max_scrolls}x each)\n")

    async with steel_browser() as (browser, viewer_url):
        print(f"Watch it:  {viewer_url}\n")

        def progress(term: str, count: int) -> None:
            print(f"  {term:<14} {count} tiles")

        candidates = await collect(browser, terms, max_scrolls, on_term=progress)

    verdicts = {c.key: classify(c.location_raw, matcher) for c in candidates}

    def bucket(*statuses: str) -> list[Candidate]:
        return [c for c in candidates if verdicts[c.key].status in statuses]

    confirmed, unclear = bucket("in-area"), bucket("unclear")
    dropped = bucket("elsewhere", "online-only")

    print(f"\n{'=' * 72}")
    print(f"{len(candidates)} unique candidates for {matcher.name}")
    print(f"  {len(confirmed):>3} confirmed by the listing location")
    print(f"  {len(unclear):>3} unresolved — the tile shows a venue, not a city")
    print(f"  {len(dropped):>3} ruled out (elsewhere or online-only)")
    print("=" * 72)

    print(f"\n### Confirmed in {matcher.name} ({len(confirmed)})")
    for index, candidate in enumerate(confirmed, start=1):
        print_candidate(index, candidate, verdicts[candidate.key].reason)

    print(f"\n\n### Needs the listing read ({len(unclear)})")
    print("    Code cannot place these. Step 5 opens them and lets the model read.")
    for index, candidate in enumerate(unclear, start=1):
        print_candidate(index, candidate, verdicts[candidate.key].reason)

    if args.all and dropped:
        print(f"\n\n### Ruled out ({len(dropped)})")
        for index, candidate in enumerate(dropped, start=1):
            print_candidate(index, candidate, verdicts[candidate.key].reason)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
