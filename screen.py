"""Step 7: run the deterministic layer over everything collected.

    uv run screen.py                  # apply code's verdict, current config
    uv run screen.py --include-past   # keep events that already happened
    uv run screen.py --show excluded  # inspect a bucket
    uv run screen.py --today 2026-02-01

No model is called anywhere in this step. Every line it prints is a decision
`core.gta`, `core.location` and `core.dates` made, with its reason attached.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from core.config import build_parser, config_from_args
from core.location import matcher_for
from core.screening import Bucket, Screened, by_bucket, screen_all
from core.store import candidates_path, load_candidates, load_readings

BUCKET_ORDER: tuple[Bucket, ...] = ("primary", "online-gta", "unresolved", "excluded")
BUCKET_LABEL = {
    "primary": "In the area, in person",
    "online-gta": "Area-linked but online",
    "unresolved": "Needs the listing read",
    "excluded": "Ruled out by code",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        parents=[build_parser(add_help=False)], description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--show", choices=BUCKET_ORDER, action="append",
                        help="print this bucket in full (repeatable)")
    parser.add_argument("--include-past", action="store_true",
                        help="keep events that already happened (widens the window too)")
    parser.add_argument("--today", help="pretend today is this date (YYYY-MM-DD)")
    return parser.parse_args()


def print_row(index: int, item: Screened) -> None:
    c = item.candidate
    origin = f"{c.source}{'+' + '+'.join(c.also_in) if c.also_in else ''}"
    when = item.starts_on.isoformat() if item.starts_on else "unknown date"
    deadline = item.deadline.date.isoformat() if item.deadline.date else "—"
    print(f"\n{index:>3}. {c.title[:66]}")
    print(f"     {when}  deadline {deadline} ({item.deadline.status})  [{origin}]")
    print(f"     {(c.location_raw or '(none)')[:70]}")
    for reason in item.reasons:
        print(f"     · {reason}")
    if item.flags:
        print(f"     ! {'; '.join(item.flags)}")


async def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    if args.include_past:
        config = config.model_copy(update={
            "filters": config.filters.model_copy(
                update={"include_past": True, "timeframe_months": 24}
            )
        })
    matcher = matcher_for(config.mode, config.location, config.nearby)
    today = date.fromisoformat(args.today) if args.today else date.today()

    candidates = load_candidates(candidates_path(config.mode, config.location))
    readings = load_readings()
    screened = screen_all(candidates, config, matcher, readings, today)

    print(f"Mode     {config.mode}  →  {matcher.name}")
    print(f"Today    {today}   (timeframe: next {config.filters.timeframe_months} months, "
          f"format: {config.filters.format})")
    print(f"Input    {len(candidates)} candidates, {len(readings)} of them read\n")

    print("=" * 74)
    for bucket in BUCKET_ORDER:
        items = by_bucket(screened, bucket)
        print(f"  {len(items):>4}  {BUCKET_LABEL[bucket]}")
    print("=" * 74)

    dated = [s for s in screened if s.starts_on]
    print(f"\nDates parsed for {len(dated)}/{len(screened)} candidates "
          f"({len(screened) - len(dated)} unparseable)")

    for bucket in args.show or ["primary", "online-gta"]:
        items = by_bucket(screened, bucket)
        print(f"\n\n### {BUCKET_LABEL[bucket]} ({len(items)})")
        if not items:
            print("     (none)")
        for index, item in enumerate(sorted(items, key=lambda s: s.starts_on or date.max), 1):
            print_row(index, item)


if __name__ == "__main__":
    asyncio.run(main())
