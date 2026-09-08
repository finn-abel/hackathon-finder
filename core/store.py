"""Reading and writing the JSON files that carry work between steps.

Candidates are cached so the reading and judging steps can be re-run without
paying for another collection pass. Location verdicts are deliberately *not*
stored — they are deterministic, so they get recomputed on load and can never
go stale against a changed GTA definition.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterable
from pathlib import Path

from core.config import PROJECT_ROOT
from core.models import Candidate, Judgement, Reading

RUNS_DIR = PROJECT_ROOT / "runs"

#: Readings and judgements are keyed by candidate URL and are the same fact
#: whichever mode asked for them — a Devpost page says what it says. Only the
#: *collection* is mode-specific, so only that is filed per run.
READINGS_PATH = PROJECT_ROOT / "readings.json"


def run_slug(mode: str, location: str | None = None) -> str:
    """A filesystem-safe name for one mode+location combination."""
    if mode == "gta":
        return "gta"
    cleaned = re.sub(r"[^a-z0-9]+", "-", (location or "").casefold()).strip("-")
    return f"general-{cleaned}" if cleaned else "general"


def candidates_path(mode: str = "gta", location: str | None = None) -> Path:
    """Where one run's collected candidates live.

    Filed per run so switching modes does not throw away the other mode's
    collection — `general` for Waterloo and `gta` can both stay warm.
    """
    return RUNS_DIR / run_slug(mode, location) / "candidates.json"


CANDIDATES_PATH = candidates_path()


def _write_json(path: Path, payload: dict) -> Path:
    """Write via a temp file and rename, so an interrupt cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    return path




def save_candidates(candidates: tuple[Candidate, ...], path: Path = CANDIDATES_PATH) -> Path:
    return _write_json(path, {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(candidates),
        "candidates": [c.model_dump() for c in candidates],
    })


def load_candidates(path: Path = CANDIDATES_PATH) -> tuple[Candidate, ...]:
    if not path.exists():
        raise FileNotFoundError(
            f"No cached candidates at {path.relative_to(PROJECT_ROOT)} — "
            f"run `uv run collect.py` for this mode first"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Candidate.model_validate(row) for row in payload.get("candidates", ()))


def save_readings(readings: dict[str, Reading], path: Path = READINGS_PATH) -> Path:
    return _write_json(path, {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(readings),
        "readings": [r.model_dump() for r in readings.values()],
    })


def load_readings(path: Path = READINGS_PATH) -> dict[str, Reading]:
    """Everything read so far, keyed by candidate URL. Empty if nothing yet."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    readings = (Reading.model_validate(row) for row in payload.get("readings", ()))
    return {r.candidate.key: r for r in readings}


JUDGEMENTS_PATH = PROJECT_ROOT / "judgements.json"


def _composite_key(judgement: Judgement) -> str:
    """A judgement is identified by BOTH the candidate and the criteria.

    Keying on the candidate alone loses work: judge under one criteria
    sentence, switch to another, and the first set is silently overwritten.
    """
    return f"{judgement.criteria_hash}:{judgement.key}"


def save_judgements(judgements: Iterable[Judgement], path: Path = JUDGEMENTS_PATH) -> Path:
    """Save every judgement, keeping one per (criteria, candidate) pair."""
    unique = {_composite_key(j): j for j in judgements}
    return _write_json(path, {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(unique),
        "judgements": [j.model_dump() for j in unique.values()],
    })


def load_all_judgements(path: Path = JUDGEMENTS_PATH) -> tuple[Judgement, ...]:
    """Every judgement on disk, across all criteria."""
    if not path.exists():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Judgement.model_validate(row) for row in payload.get("judgements", ()))


def load_judgements(
    criteria_hash: str, path: Path = JUDGEMENTS_PATH
) -> dict[str, Judgement]:
    """Judgements made under one criteria sentence, keyed by candidate URL.

    Judgements for any other criteria are stale by definition and excluded.
    """
    return {
        j.key: j for j in load_all_judgements(path) if j.criteria_hash == criteria_hash
    }


RESULTS_PATH = PROJECT_ROOT / "results.json"


def save_results(results, path: Path = RESULTS_PATH) -> Path:
    """Write results.json. `results` is a core.results.Results."""
    return _write_json(path, results.model_dump(mode="json"))


def load_results(path: Path = RESULTS_PATH):
    from core.results import Results

    if not path.exists():
        raise FileNotFoundError(f"No results at {path} — run `uv run judge.py` first")
    return Results.model_validate_json(path.read_text(encoding="utf-8"))
