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
