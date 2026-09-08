# Hackathon Finder

Finds hackathons, reads the messy listings, judges each against a one-sentence
description of what you want, and gives you a ranked, explained shortlist in a
local web page.

It runs in two modes. **`gta`** (the default) screens the Greater Toronto Area,
which the code knows as an explicit set of municipalities. **`general`** takes
any location and screens that area instead — the same engine with the location
swapped in.

## The idea: a clean AI-vs-code split

The interesting engineering here is deciding what the model is *for*.

| Job | Who does it | Why |
|---|---|---|
| Read a listing page's prose — eligibility, deadline wording | **AI** | Written differently on every page. Needs a reader, not a regex. |
| Decide whether a place is in the GTA | **code** | A place either is in a defined set or it isn't. |
| Parse deadlines and compare to today | **code** | Dates are arithmetic. Extract with AI, compare with code. |
| Dedupe, filter, rank | **code** | Pure logic. |
| Judge fuzzy fit against your criteria | **AI** | The only genuinely un-scriptable part. |

The model never decides where an event is, whether a deadline has passed, or
what order the results go in. Its prompts forbid it and tests assert those
clauses stay put. `core/screening.py` is checked by a test to contain no
reference to an LLM at all.

## Setup

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install from uv.lock
cp .env.example .env          # then add your two keys
uv run check_setup.py         # keys load, and a Steel session opens and releases
```

`.env` needs a [Steel](https://steel.dev) key for the cloud browser and an
OpenAI key for reading and judging:

```
STEEL_API_KEY=...
OPENAI_API_KEY=...
```

## Configure the scan

Everything lives in `config.yaml`. Nothing downstream is hard-coded.

```yaml
mode: gta                 # "gta" (default) or "general"
location: null            # required for "general", ignored for "gta"
nearby: []                # extra place names counted as inside the area

# The one sentence the AI judges each listing against.
criteria: "beginner-friendly, in-person, open to non-students, AI or web themed"

filters:                  # applied by code, never by the model
  timeframe_months: 12
  include_past: false
  format: any             # in-person | online | hybrid | any
  themes: []

collect:
  sources: [devpost, mlh]
  max_scrolls: 3
```

Every setting is also a flag: `--mode`, `--location`, `--criteria`, `--config`.

## Run a scan

```bash
uv run collect.py     # gather candidates from every source     (browser)
uv run judge.py       # screen, score against criteria, write results.json
uv run dashboard.py   # open the local dashboard
```

`collect.py` is the only step that needs the browser. `judge.py` re-reads the
cached candidates, so re-running it after editing `criteria` costs nothing but
the judging.

### GTA mode

```bash
uv run collect.py
uv run judge.py
uv run dashboard.py
```

### General mode

```bash
uv run collect.py --mode general --location "Waterloo, ON"
uv run judge.py   --mode general --location "Waterloo, ON"
uv run dashboard.py
```

Each run keeps its own collection under `runs/<slug>/`, so switching between
modes does not throw the other away.

## The dashboard

`uv run dashboard.py` serves a plain HTML page and opens it. If port 8000 is
busy it picks the next free one and prints the URL.

Sort by clicking any column. Filters live in the URL, so a view is a shareable
link:

```
/dashboard/index.html?minFit=4&deadline=open&sort=deadline
/dashboard/index.html?attention=1
```

Rows needing a look get a red border, and every assumption the code made is
printed under the name.

## Reading listing pages (optional)

Most listings can be placed and dated from the search results alone. Some show
only a venue — "Sheridan College Hazel McCallion Campus" — which code cannot
place. Those are marked `unresolved` and are the ones worth opening:

```bash
uv run read.py --list          # what has been collected, and its status
uv run read.py --index 3       # read one listing
uv run read_all.py --limit 5   # read the queue, resuming where it stopped
uv run read_all.py --status
```

Reading fills in eligibility and real deadlines, and often resolves the
location — that Sheridan page says "Sheridan HMC Campus (Mississauga, ON)".

## Other commands

```bash
uv run screen.py               # code's verdict on everything, with reasons
uv run screen.py --show excluded
uv run sessions.py             # any Steel sessions left running
uv run sessions.py --release
uv run pytest                  # 329 tests, no browser or network needed
```

## How it fits together

```
config.yaml
    │
    ├─ collect.py ──> runs/<mode>/candidates.json     browser, no AI
    │                   devpost (DOM) + mlh (embedded JSON), merged
    │
    ├─ read_all.py ─> readings.json                   browser + AI
    │                   only for listings code cannot place
    │
    └─ judge.py ────> results.json                    AI scores, code ranks
                        │
                        └─ dashboard.py ──> a local web page
```

| Module | Does |
|---|---|
| `core/gta.py` | the GTA as an explicit constant: 5 regions, 30 municipalities, aliases |
| `core/location.py` | classifies a raw location string against the target area |
| `core/dates.py` | parses deadlines and date ranges, compares to today |
| `core/screening.py` | code's verdict: bucket, reasons, flags |
| `core/flags.py` | everything the pipeline noticed but could not settle |
| `core/results.py` | the `results.json` schema |
| `agent/sources/` | one module per source; add one and nothing else changes |
| `agent/reader.py` | AI extraction from a listing page |
| `agent/judge.py` | AI fit scoring; ranking stays in code |

## Output

`results.json` is self-describing: it carries a `legend` explaining every
value it uses, and a `run` block with the mode, area, criteria, filters and
counts. Each listing is three blocks that mirror how the value was produced —
`raw` (verbatim from the source), `derived` (what code computed, with its
reasons) and `fit` (what the model judged).

Nothing is silently dropped or silently guessed. Listings code could not settle
stay in the output carrying a flag, and rows ruled out are summarised with
counts and reasons rather than disappearing.

## Notes

- Steel sessions are released in a `finally`, with the detach time-boxed so a
  hung browser cannot cost a session. `uv run sessions.py` checks for strays.
- `.env`, `results.json`, `readings.json`, `judgements.json` and `runs/` are
  gitignored. No keys are committed.
- Judgements are cached against a fingerprint of the criteria sentence, so
  editing `criteria` re-judges everything and re-running the same criteria is
  free.
