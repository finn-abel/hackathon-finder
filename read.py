"""Step 5: open one hackathon's page and have the AI extract its raw facts.

    uv run read.py https://larphacks.devpost.com/     # read a specific URL
    uv run read.py --index 3                          # read a cached candidate
    uv run read.py --list                             # show the cached candidates

Extraction only. Nothing here classifies a location, compares a date, or
judges fit — those stay in code, in the steps that follow.
"""

from __future__ import annotations

import argparse
import asyncio
import textwrap

from agent.reader import ListingFacts, read_listing
from agent.session import steel_browser
from core.config import build_parser, config_from_args
from core.location import classify, matcher_for
from core.models import Candidate
from core.store import load_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        parents=[build_parser(add_help=False)], description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="the listing to read")
    parser.add_argument("--index", type=int, help="read this cached candidate instead")
    parser.add_argument("--list", action="store_true", help="list cached candidates and exit")
    parser.add_argument("--model", help="override the extraction model")
    return parser.parse_args()


def show_cached(candidates: tuple[Candidate, ...], matcher) -> None:
    print(f"{len(candidates)} cached candidates:\n")
    for index, candidate in enumerate(candidates):
        status = classify(candidate.location_raw, matcher).status
        location = candidate.location_raw or "(none)"
        print(f"  {index:>3}  [{status:<11}] {candidate.title[:44]:<44} {location[:30]}")
    print("\nRead one with:  uv run read.py --index N")


def field(label: str, value: object) -> None:
    if value in (None, "", [], ()):
        print(f"  {label:<18} —")
        return
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    wrapped = textwrap.fill(str(value), width=88, subsequent_indent=" " * 21)
    print(f"  {label:<18} {wrapped}")


def print_facts(url: str, facts: ListingFacts | None) -> None:
    print(f"\n{'=' * 92}")
    if facts is None:
        print("The agent finished without returning a structured result.")
        print("Re-run and watch the live viewer to see where it got stuck.")
        return

    print(f"{facts.title or '(no title found)'}")
    print(f"{url}")
    print("=" * 92)

    print("\nWhat the page says")
    field("location", facts.location_text)
    field("format", facts.event_format)
    field("  evidence", facts.format_evidence)
    field("dates", facts.dates_text)
    field("deadline", facts.deadline_text)
    field("themes", facts.themes)
    field("beginner", facts.beginner_friendly)
    field("  evidence", facts.beginner_evidence)
    field("eligibility", facts.eligibility_text)

    missing = [
        name for name, value in (
            ("location", facts.location_text),
            ("dates", facts.dates_text),
            ("deadline", facts.deadline_text),
            ("eligibility", facts.eligibility_text),
        ) if not value
    ]
    print(f"\n  not stated on the page: {', '.join(missing) if missing else 'nothing — full read'}")


async def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    matcher = matcher_for(config.mode, config.location)

    if args.list or (not args.url and args.index is None):
        show_cached(load_candidates(), matcher)
        return

    if args.url:
        url = args.url
    else:
        candidates = load_candidates()
        if not 0 <= args.index < len(candidates):
            raise SystemExit(f"--index must be 0..{len(candidates) - 1}")
        chosen = candidates[args.index]
        url = chosen.url
        print(f"Reading: {chosen.title}")

    model = args.model or config.read.model
    print(f"Model:   {model}  (max {config.read.max_steps} steps)\n")

    async with steel_browser() as (browser, viewer_url):
        print(f"Watch it: {viewer_url}\n")
        facts = await read_listing(browser, url, model, config.read.max_steps)

    print_facts(url, facts)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
