"""Reading and writing the JSON files that carry work between steps.

Candidates are cached so the reading and judging steps can be re-run without
paying for another collection pass. Location verdicts are deliberately *not*
stored — they are deterministic, so they get recomputed on load and can never
go stale against a changed GTA definition.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.config import PROJECT_ROOT
from core.models import Candidate, Reading

CANDIDATES_PATH = PROJECT_ROOT / "candidates.json"
READINGS_PATH = PROJECT_ROOT / "readings.json"


def _write_json(path: Path, payload: dict) -> Path:
    """Write via a temp file and rename, so an interrupt cannot truncate it."""
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
        raise FileNotFoundError(f"No cached candidates at {path} — run `uv run collect.py` first")
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
