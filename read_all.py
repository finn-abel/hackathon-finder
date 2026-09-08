"""Step 6: read every candidate's page, one at a time, with progress.

    uv run read_all.py                  # read what still needs reading
    uv run read_all.py --limit 5        # a cheap trial run
    uv run read_all.py --status         # what has been read so far
    uv run read_all.py --refresh        # re-read everything from scratch
    uv run read_all.py --include-ruled-out

Results are saved after every listing, so an interrupted run resumes where it
stopped instead of paying for the same pages twice.
"""

from __future__ import annotations

import argparse
import asyncio

from agent.reader import read_many
from agent.session import steel_browser
from core.config import build_parser, config_from_args
from core.location import classify, matcher_for
from core.models import Candidate, Reading
from core.store import load_candidates, load_readings, save_readings

RULED_OUT = ("elsewhere", "online-only")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        parents=[build_parser(add_help=False)], description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--limit", type=int, help="read at most this many this run")
    parser.add_argument("--refresh", action="store_true", help="ignore saved readings")
    parser.add_argument("--status", action="store_true", help="report progress and exit")
    parser.add_argument("--include-ruled-out", action="store_true",
                        help="also read listings code already placed outside the area")
    parser.add_argument("--model", help="override the extraction model")
    return parser.parse_args()


def worth_reading(candidates: tuple[Candidate, ...], matcher, include_ruled_out: bool) -> list[Candidate]:
    """Skip listings code has already settled as outside the area.

    Reading those costs a model call to learn something already known.
    """
    if include_ruled_out:
        return list(candidates)
    return [c for c in candidates if classify(c.location_raw, matcher).status not in RULED_OUT]


def summarise(reading: Reading) -> str:
    if not reading.ok:
        return f"failed — {reading.error}"
    facts = reading.facts
    parts = [
        reading.location_text or "no location",
        facts.event_format,
        f"deadline: {reading.deadline_text[:38]}" if reading.deadline_text
        else f"dates: {reading.dates_text[:30]}" if reading.dates_text
        else "no dates",
        "eligibility ✓" if facts.eligibility_text else "eligibility —",
    ]
    return " | ".join(parts)


def report_status(candidates: tuple[Candidate, ...], readings: dict[str, Reading], pending: list[Candidate]) -> None:
    done = [r for r in readings.values() if r.ok]
    failed = [r for r in readings.values() if not r.ok]
    print(f"{len(candidates)} candidates cached")
    print(f"  {len(done):>3} read successfully")
    print(f"  {len(failed):>3} failed")
    print(f"  {len(pending):>3} still to read")
    if failed:
        print("\nFailures:")
        for reading in failed:
            print(f"  - {reading.candidate.title[:50]:<50} {reading.error}")


async def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    matcher = matcher_for(config.mode, config.location)

    candidates = load_candidates()
    readings = {} if args.refresh else load_readings()

    queue = [c for c in worth_reading(candidates, matcher, args.include_ruled_out)
             if c.key not in readings]

    if args.status:
        report_status(candidates, readings, queue)
        return

    skipped = len(candidates) - len(worth_reading(candidates, matcher, args.include_ruled_out))
    if args.limit:
        queue = queue[: args.limit]

    print(f"{len(candidates)} candidates — {len(readings)} already read, "
          f"{skipped} ruled out by code, {len(queue)} to read now\n")
    if not queue:
        print("Nothing to do. `--refresh` re-reads everything.")
        return

    model = args.model or config.read.model
    print(f"Model: {model}  (max {config.read.max_steps} steps, "
          f"{config.read.timeout_s:.0f}s each)\n")

    async with steel_browser() as (browser, viewer_url):
        print(f"Watch it: {viewer_url}\n")

        def announce(index: int, candidate: Candidate) -> None:
            print(f"[{index + 1:>2}/{len(queue)}] {candidate.title[:60]}")

        async for reading in read_many(
            browser, queue, model, config.read.max_steps, config.read.timeout_s, announce
        ):
            readings[reading.candidate.key] = reading
            save_readings(readings)  # after every listing, not at the end
            mark = "  ok " if reading.ok else "  !! "
            print(f"{mark}{summarise(reading)}\n")

    print("=" * 78)
    ok = [r for r in readings.values() if r.ok]
    resolved = [
        r for r in ok
        if classify(r.candidate.location_raw, matcher).needs_a_reader
        and classify(r.location_text, matcher).in_area
    ]
    print(f"{len(ok)}/{len(readings)} listings read successfully")
    print(f"{len(resolved)} were unplaceable from the tile and the read put them in {matcher.name}")
    print("Saved to readings.json")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted — progress up to the last listing is saved.")
