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

from agent.session import steel_browser
from agent.sources import CollectRequest, registry, source_names
from agent.sources.devpost import search_terms_for
from core.config import Config, build_parser, config_from_args
from core.location import matcher_for
from core.screening import place
from core.merge import merge
from core.models import Candidate
from core.store import candidates_path, save_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        parents=[build_parser(add_help=False)], description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sources", nargs="+", choices=source_names(),
                        help="which sources to collect from")
    parser.add_argument("--terms", nargs="+", help="override the search terms")
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
    origin = f"[{candidate.source}{'+' + '+'.join(candidate.also_in) if candidate.also_in else ''}]"
    print(f"    {origin} {location}  —  {reason}")
    details = " | ".join(filter(None, (candidate.status_raw, candidate.dates_raw)))
    if details:
        print(f"    {details}")
    if candidate.themes:
        print(f"    themes: {', '.join(candidate.themes)}")


async def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    matcher = matcher_for(config.mode, config.location, config.nearby)
    terms = terms_for(config, args.terms)
    max_scrolls = config.collect.max_scrolls if args.max_scrolls is None else args.max_scrolls
    chosen = tuple(args.sources or config.collect.sources)
    sources = registry()

    request = CollectRequest(
        mode=config.mode, location=config.location, terms=terms,
        max_scrolls=max_scrolls, seasons=config.collect.seasons,
    )

    print(f"Mode     {config.mode}  →  {matcher.name}")
    print(f"Sources  {', '.join(chosen)}")
    print(f"Terms    {', '.join(terms)}  (Devpost only)\n")

    collected: list[Candidate] = []
    async with steel_browser() as (browser, viewer_url):
        print(f"Watch it:  {viewer_url}\n")
        for name in chosen:
            try:
                found = await sources[name](browser, request)
            except Exception as exc:
                # One broken source must not lose the others' results.
                print(f"  {name:<9} FAILED — {type(exc).__name__}: {exc}")
                continue
            in_area = sum(1 for c in found if place(c, matcher)[1] == "primary")
            print(f"  {name:<9} {len(found):>3} found, {in_area} already placed in {matcher.name}")
            collected.extend(found)

    candidates = merge(collected)

    placed = {c.key: place(c, matcher) for c in candidates}

    def bucket(*buckets: str) -> list[Candidate]:
        return [c for c in candidates if placed[c.key][1] in buckets]

    confirmed, unclear = bucket("primary", "online-gta"), bucket("unresolved")
    dropped = bucket("excluded")

    print(f"\n{'=' * 72}")
    print(f"{len(collected)} collected → {len(candidates)} unique candidates for {matcher.name}")
    print(f"  {len(confirmed):>3} confirmed by the listing location")
    print(f"  {len(unclear):>3} unresolved — the tile shows a venue, not a city")
    print(f"  {len(dropped):>3} ruled out (elsewhere or online-only)")
    print("=" * 72)

    print(f"\n### Confirmed in {matcher.name} ({len(confirmed)})")
    for index, candidate in enumerate(confirmed, start=1):
        print_candidate(index, candidate, placed[candidate.key][2])

    print(f"\n\n### Needs the listing read ({len(unclear)})")
    print("    Code cannot place these. Step 5 opens them and lets the model read.")
    for index, candidate in enumerate(unclear, start=1):
        print_candidate(index, candidate, placed[candidate.key][2])

    saved = save_candidates(candidates, candidates_path(config.mode, config.location))
    print(f"\n\nCached {len(candidates)} candidates to {saved.name} — "
          f"`uv run read.py` works from this file, no browser needed.")

    if args.all and dropped:
        print(f"\n\n### Ruled out ({len(dropped)})")
        for index, candidate in enumerate(dropped, start=1):
            print_candidate(index, candidate, placed[candidate.key][2])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
