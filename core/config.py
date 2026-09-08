"""Typed run configuration: mode, location, criteria, deterministic filters.

Loaded from config.yaml, overridable on the command line. Every value the
screener runs on comes from here — nothing downstream is hard-coded.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core.gta import GTA_AREA_NAME

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

Mode = Literal["gta", "general"]
FormatFilter = Literal["in-person", "online", "hybrid", "any"]


class Filters(BaseModel):
    """Structured filters applied by code, never by the LLM."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe_months: int = Field(default=3, ge=1, le=24)
    format: FormatFilter = "any"
    themes: tuple[str, ...] = ()


class Collect(BaseModel):
    """How wide to cast the net when gathering candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    search_terms: tuple[str, ...] = ()  # empty means "derive from the mode"
    max_scrolls: int = Field(default=3, ge=0, le=20)


class Config(BaseModel):
    """One immutable run's worth of parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Mode = "gta"
    location: str | None = None
    criteria: str = Field(min_length=1)
    filters: Filters = Filters()
    collect: Collect = Collect()

    @model_validator(mode="after")
    def _general_mode_needs_a_location(self) -> "Config":
        if self.mode == "general" and not (self.location or "").strip():
            raise ValueError(
                'mode "general" needs a location — set it in config.yaml '
                'or pass --location "Waterloo, ON"'
            )
        return self

    @property
    def target_area(self) -> str:
        """The area the location classifier screens against.

        In gta mode this is always the GTA and `location` is ignored, so
        downstream code should read this rather than `location` directly.
        """
        if self.mode == "gta":
            return GTA_AREA_NAME
        assert self.location is not None  # guaranteed by the validator above
        return self.location.strip()


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Read and validate config.yaml. Raises on anything malformed."""
    if not path.exists():
        raise FileNotFoundError(f"No config file at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a mapping of settings, got {type(raw).__name__}")

    return Config.model_validate(raw)


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    """The shared config flags. Pass add_help=False to use as an argparse parent."""
    parser = argparse.ArgumentParser(
        description="Hackathon finder configuration.", add_help=add_help
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="path to config.yaml")
    parser.add_argument("--mode", choices=("gta", "general"), help="override config mode")
    parser.add_argument("--location", help='override config location, e.g. "Waterloo, ON"')
    parser.add_argument("--criteria", help="override the one-sentence fuzzy criteria")
    return parser


def config_from_args(args: argparse.Namespace) -> Config:
    """Config file first, command-line flags on top. Returns a new Config."""
    base = load_config(args.config)

    overrides = {
        key: value
        for key, value in (("mode", args.mode), ("location", args.location), ("criteria", args.criteria))
        if value is not None
    }
    if not overrides:
        return base

    # model_validate (not model_copy) so overrides are re-validated as a whole —
    # e.g. --mode general with no location anywhere must still fail.
    return Config.model_validate({**base.model_dump(), **overrides})


def resolve_config(argv: list[str] | None = None) -> Config:
    return config_from_args(build_parser().parse_args(argv))


def describe(config: Config) -> str:
    location_line = (
        f"  location    {config.location!r} (ignored in gta mode)"
        if config.mode == "gta"
        else f"  location    {config.location!r}"
    )
    return "\n".join(
        (
            "Resolved configuration",
            f"  mode        {config.mode}",
            location_line,
            f"  target area {config.target_area}",
            f"  criteria    {config.criteria}",
            f"  timeframe   next {config.filters.timeframe_months} month(s)",
            f"  format      {config.filters.format}",
            f"  themes      {list(config.filters.themes) or 'any'}",
        )
    )


if __name__ == "__main__":
    try:
        print(describe(resolve_config()))
    except (FileNotFoundError, ValueError, ValidationError) as exc:
        raise SystemExit(f"Config error: {exc}")
