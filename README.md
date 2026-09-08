# Hackathon Finder

A programmatic fuzzy-criteria screener for hackathons, GTA-first.

The design principle is a clean **AI-vs-code split**: the AI reads messy
listings and judges fuzzy fit against a one-sentence `criteria`; the code
classifies location, parses deadlines, dedups, ranks and filters.

## Setup

```bash
uv sync                     # install into .venv from uv.lock
uv run check_setup.py       # keys load + a Steel session opens and releases
```

Keys live in `.env` (see `.env.example`).

## Configuration

Every run parameter lives in `config.yaml` — `mode`, `location`, `criteria`
and the deterministic `filters`. Flags override the file:

```bash
uv run python -m core.config                                   # gta mode (default)
uv run python -m core.config --mode general --location "Waterloo, ON"
```

- `gta` mode screens the Greater Toronto Area and ignores `location`.
- `general` mode screens the area in `location`, which is then required.

## The GTA definition

`core/gta.py` holds the GTA as an explicit constant — five regions, 30
municipalities and districts, plus the aliases listings actually use
("Woodbridge", "Port Credit", "The 6ix"). The AI never judges geography;
this module does.

```bash
uv run python -m core.gta                          # print the whole definition
uv run python -m core.gta Mississauga Waterloo     # query names
uv run pytest                                      # the definition's spec
```

To extend it, add a `Municipality` or an alias to the right `Region`. The
index is built at import and raises on duplicate names, so a name that
collides with an existing place fails immediately.

## The deterministic backbone

`core/location.py` and `core/dates.py` are the two modules the AI hands its
raw extractions to. Neither one calls an LLM.

```bash
uv run python -m core.location                          # classify sample strings
uv run python -m core.location --area="Waterloo, ON"    # general mode
uv run python -m core.dates                             # parse sample deadlines
```

`classify()` returns an event format, the in-area places it found, and a
plain-English `reason` for the dashboard. `parse_deadline()` returns a date
and a status of `passed` / `soon` / `upcoming` / `unknown` — never a guess.

## Collecting candidates

`agent/devpost.py` drives the Steel browser to Devpost's listing pages and
reads the tiles straight off the DOM — no LLM, because a listing page is a
structured list and titles and hrefs must come back byte-exact.

```bash
uv run collect.py                                   # gta mode, from config.yaml
uv run collect.py --mode general --location "Waterloo, ON"
uv run collect.py --terms Toronto --max-scrolls 0   # a quick single-term run
uv run collect.py --all                             # show what was ruled out
```

Two things about Devpost worth knowing, both found by looking rather than
guessing:

- **The listing is an infinite scroll.** `?page=2` is silently ignored and
  re-serves page 1, so the collector scrolls until the tile count stops
  growing.
- **The tile often shows a venue, not a city** — "Sheridan College Hazel
  McCallion Campus", "Bur Oak Secondary School". Those are not rejections.
  `classify()` returns `unclear` for them, and they become the queue of
  listings the model actually needs to open.

## Reading a listing

`agent/reader.py` is the first place a model belongs. Detail pages are prose
— eligibility buried under a Rules tab, a deadline phrased as "by 12:00 PM
EST on Sunday" — and no regex reads that reliably.

```bash
uv run read.py --list                          # cached candidates
uv run read.py --index 3                       # read one of them
uv run read.py https://some.devpost.com/       # read any URL
```

The model extracts and nothing more. It does not decide whether a location
is in the GTA or whether a deadline has passed — the prompt forbids it, and
`core.location` and `core.dates` take its raw strings from there. Every field
is nullable so "the page does not say" is always available; a model with no
way to say that will invent something.

This is where the venue problem from collection gets solved. A tile reading
"Sheridan College Hazel McCallion Campus" is unplaceable by code; the page
itself says "Sheridan HMC Campus (Mississauga, ON)", which classifies cleanly.

## Sources

Candidates come from a pluggable registry in `agent/sources/`. A source is a
module with a `NAME` and a `collect(browser, request)` coroutine; everything
downstream — classification, dates, judging — is source-agnostic.

```yaml
collect:
  sources: [devpost, mlh]
```

```bash
uv run collect.py --sources mlh       # one source
uv run collect.py --sources devpost mlh
```

The two sources behave very differently, and it matters:

| | Devpost | MLH |
|---|---|---|
| Shape | client-rendered tiles, infinite scroll | one page, embedded JSON |
| Location | often a venue name | `City, Province` + structured venue address |
| Dates | a display string | ISO 8601 `startsAt`/`endsAt` |
| Format | not stated | explicit `formatType` |
| Needs an AI read | ~55% of results | ~2% |

Because MLH publishes structured data, code settles almost all of it with no
model call at all. `core/merge.py` folds events found in both sources into one
record, preferring the one carrying more structured fields — which upgrades a
Devpost venue string to MLH's clean city when both list the same event.

## The deterministic layer

`core/screening.py` runs code's verdict over everything collected. No model is
called; a test asserts the module never even imports one.

```bash
uv run screen.py                      # code's verdict, current config
uv run screen.py --include-past       # keep events that already happened
uv run screen.py --show excluded      # inspect any bucket, with reasons
uv run screen.py --today 2026-02-01   # re-bucket against a different date
```

Each candidate lands in one of four buckets:

| bucket | meaning |
|---|---|
| `primary` | in the area and physically there — the shortlist |
| `online-gta` | organised in the area but run online, kept and labelled apart |
| `unresolved` | code cannot place it; the listing still needs reading |
| `excluded` | code is confident it does not belong, with the reason attached |

Every decision carries its reason, and assumptions are flagged rather than
hidden — a deadline inferred from an event's last day says so.

## Judging

`agent/judge.py` is the one place a model makes a judgement call. It scores
each hackathon 0-5 against your `criteria` sentence and explains itself citing
specific facts. **It does not rank** — ordering a list is arithmetic, so
`agent.judge.rank` sorts by score, then by nearer deadline, then by name.

```bash
uv run judge.py                          # rank the shortlist
uv run judge.py --include-past --top 10
uv run judge.py --criteria "hardware, open to professionals"
uv run judge.py --buckets primary online-gta unresolved
```

No browser is opened — judging is text in, score out, so it runs in seconds.

The prompt forbids the model from re-deciding location or whether a deadline
has passed; `core.gta` and `core.dates` already settled those, and tests assert
those clauses stay in the prompt.

Judgements are cached against a fingerprint of the criteria sentence, so
re-running is free but **editing `criteria` re-judges everything** — and work
done under other criteria is kept, not overwritten.
