"""The sources candidates are collected from.

Adding a source means adding a module here with a NAME and a `collect`
coroutine, then listing it in config. Everything downstream — the location
classifier, the date parser, the judging — is source-agnostic and needs no
change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Protocol

from browser_use import Browser

from core.models import Candidate


@dataclass(frozen=True, slots=True)
class CollectRequest:
    """What one collection run is looking for.

    Sources take what they need and ignore the rest: Devpost searches by term,
    MLH publishes one global list and lets the classifier filter it.
    """

    mode: str
    location: str | None = None
    terms: tuple[str, ...] = ()
    max_scrolls: int = 3
    seasons: tuple[str, ...] = ()  # MLH seasons; empty means "work it out from today"


class Source(Protocol):
    NAME: str

    async def collect(self, browser: Browser, request: CollectRequest) -> tuple[Candidate, ...]:
        ...


def registry() -> Mapping[str, Callable[[Browser, CollectRequest], Awaitable[tuple[Candidate, ...]]]]:
    """Imported lazily so a broken source cannot stop the others loading."""
    from agent.sources import devpost, mlh

    return {devpost.NAME: devpost.collect, mlh.NAME: mlh.collect}


def source_names() -> tuple[str, ...]:
    return tuple(registry())
