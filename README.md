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

## results.json

`judge.py` writes `results.json` — the artifact the dashboard reads. The shape
mirrors how each value was produced:

```json
{
  "schema_version": "1.0",
  "legend": { "provenance": {...}, "bucket": {...}, "deadline_status": {...} },
  "run":    { "mode": "gta", "target_area": "...", "criteria": "...",
              "criteria_hash": "...", "today": "...", "sources": [...],
              "filters": {...}, "counts": {...}, "excluded_reasons": {...} },
  "results": [
    { "rank": 1, "title": "...", "url": "...",
      "raw":     { "location": "UofT Mississauga - Deerfield Hall", "was_read": false },
      "derived": { "in_area": true, "places": ["Mississauga"], "regions": ["Peel"],
                   "starts_on": "2026-02-27", "deadline_status": "passed",
                   "bucket": "primary", "reasons": [...], "flags": [...] },
      "fit":     { "score": 5, "reason": "...", "supports": [...], "missing": [...] } }
  ]
}
```

- **`raw`** is verbatim from the listing. Nothing altered it.
- **`derived`** is what code computed — every value carries its `reasons`, and
  assumptions appear in `flags`.
- **`fit`** is the model's judgement, absent (not zero) when nothing judged it.

The `legend` documents every vocabulary the file uses, and a test asserts no
value can appear that the file does not explain. Rows code ruled out are
summarised in `run.excluded_reasons` rather than listed; `--include-excluded`
writes them in full.

```bash
uv run judge.py --include-past          # writes results.json
uv run judge.py --no-save               # print only
uv run judge.py --out /tmp/scan.json
```

## The dashboard

A single static HTML file — no build step, no framework, no CDN. Plain 90s
web page: Times New Roman, bordered tables, grey header cells.

```bash
uv run judge.py --include-past    # write results.json
uv run dashboard.py              # serve it and open a browser
uv run dashboard.py --port 8080 --no-open
```

The page shows the run's mode, target area, criteria and counts at the top,
then a ranked table: name (linked to the listing), date, deadline, location,
format, fit, eligibility and the reason behind the score. Deadlines are
highlighted — `soon` in yellow, `passed` rows greyed out — and code's
assumptions appear under each name, so "deadline assumed from the event's
last day" is visible rather than buried.

Sort by clicking any column header. Filters live in the URL, so a filtered
view is a shareable link:

```
/dashboard/index.html?minFit=4&deadline=open&sort=deadline
```

The server binds to `127.0.0.1` only. All text is rendered with `textContent`,
never `innerHTML` — every title and reason came off someone else's page.

## general mode

`gta` mode is the default. Pass a location and the same engine screens that
area instead — collection, extraction, judging, output and dashboard are
unchanged.

```bash
uv run collect.py --mode general --location "Waterloo, ON"
uv run judge.py   --mode general --location "Waterloo, ON" --include-past
uv run dashboard.py
```

```yaml
mode: general
location: "Waterloo, ON"
nearby: ["Kitchener", "Cambridge"]   # extra names counted as inside the area
```

Two things make it practical:

- **Each run keeps its own collection** under `runs/<slug>/candidates.json`, so
  switching between `gta` and a location does not throw the other away. Page
  readings and fit judgements stay shared — a Devpost page says the same thing
  whichever mode asked, and judgements are already keyed by criteria.
- **A structured address settles the area without a gazetteer.** Only the GTA
  matcher knows what is "elsewhere", so in `general` mode an MLH event in
  Montreal would otherwise be "unclear" and queued for an AI read. MLH
  publishes a full city/region/country, and if none of it names the target
  area, that is a fact from the source. This cut Waterloo's unresolved pile
  from 246 to 11.

## Handling the mess

Nothing is silently dropped and nothing is silently guessed. When code has to
assume something, finds two facts that contradict each other, or cannot reach
a page, it records a flag and the listing stays in the output.

Flags carry a severity — `info` (worth knowing), `warn` (thinner data than it
looks), `attention` (a person should look). `core/flags.py` holds the
catalogue; every code is documented in `results.json`'s legend, and a test
asserts the two never drift apart.

| flag | meaning |
|---|---|
| `read_failed` | the detail page could not be read |
| `no_parseable_date` | no date could be parsed |
| `assumed_deadline` | deadline taken from the event's last day, not stated |
| `online_but_placed` | tagged for a place but the listing says it runs online |
| `format_conflict` | the source and the page disagree about the format |
| `deadline_after_event` | the deadline falls after the event ends |
| `possible_duplicate` | another listing looks like the same event |
| `suspect_title` | the title itself warns the listing is stale or wrong |

Near-duplicates are **flagged, never merged**: `core/merge.py` fuses records
only on an exact title match, and `core/duplicates.py` makes the softer call
that "DeerHacks V", "DeerHacks V (2026)" and "DeerHacks 2023" are worth
looking at together. Deciding which is which is a person's job.

In the dashboard, attention rows get a red left border and their flags print
under the name. Tick **Needs attention only** to see just those, or link
straight to them:

```
/dashboard/index.html?attention=1
```
