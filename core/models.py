"""The shapes that move between steps: a candidate, and a screened candidate."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict


def canonical_url(url: str) -> str:
    """Strip tracking params and trailing slashes so the same event dedups."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))


class Candidate(BaseModel):
    """One hackathon as it appears on a listing page, before any judging.

    Everything here was read straight off the DOM — no model wrote any of it.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    url: str
    source: str = "devpost"
    location_raw: str = ""
    deadline_raw: str = ""
    status_raw: str = ""
    host: str = ""
    themes: tuple[str, ...] = ()
    found_via: str = ""

    @property
    def key(self) -> str:
        """Identity for dedup: the canonical URL."""
        return canonical_url(self.url)
