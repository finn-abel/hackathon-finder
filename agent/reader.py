"""Read one hackathon's detail page with the AI. Extraction only.

This is the first step where a model is the right tool. A detail page is
prose written differently every time — eligibility buried in a Rules tab,
a deadline phrased as "submissions close at 11:59pm ET on the 24th". No
regex reads that reliably; a reader does.

What the model must NOT do here is judge. It does not decide whether a
location is in the GTA, whether a deadline has passed, or whether the event
fits your criteria — `core.location`, `core.dates` and the judging step own
those. Its only job is to come back with the page's own words.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import AsyncIterator, Callable, Sequence

from browser_use import Agent, Browser, ChatOpenAI

from core.models import Candidate, ListingFacts, Reading

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_MAX_STEPS = 15
DEFAULT_TIMEOUT_S = 240.0

EXTRACTION_TASK = """You are reading ONE hackathon's page: {url}

Extract the facts the page states. This is extraction, not judgement.

Rules:
- Quote the page. Do not paraphrase, normalize or tidy up wording.
- If the page does not state something, return null for it. Never guess, and
  never fill a field from general knowledge about the event.
- Do not decide whether the location is in any particular region, whether the
  deadline has passed, or whether the event is a good fit. Other code does that.

The location field matters most, so be specific about it:
- It must name the CITY or municipality whenever the page states one anywhere —
  the header, the "Where"/venue line, an address, the About section, or Rules.
- A campus or school name alone is not enough. If the page says "Sheridan HMC
  Campus, Mississauga, ON", return all of it, not just the campus.
- Only if the page truly never names a city may you return the venue alone.
- Still verbatim: copy what the page says, do not add a city from your own
  knowledge of where that venue is.

Where things hide on these pages:
- Eligibility and team rules are usually under a "Rules" tab or heading, not
  on the landing section. Open it and read it.
- The deadline is often in a countdown, a "Submission period" line, or the
  Rules section — capture the words, including time and timezone.
- Scroll far enough to see the location, dates and theme tags.

Return the structured result when you have looked in those places."""


async def read_listing(
    browser: Browser,
    url: str,
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> ListingFacts | None:
    """Open one listing and have the model extract its stated facts."""
    # Navigate in code rather than spending an agent step on it.
    await browser.navigate_to(url)

    agent = Agent(
        task=EXTRACTION_TASK.format(url=url),
        llm=ChatOpenAI(model=model),
        browser=browser,
        output_model_schema=ListingFacts,
    )
    history = await agent.run(max_steps=max_steps)
    return history.structured_output


async def read_many(
    browser: Browser,
    candidates: Sequence[Candidate],
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    on_start: Callable[[int, Candidate], None] | None = None,
) -> AsyncIterator[Reading]:
    """Read each candidate in turn, yielding results as they land.

    Yields rather than returning a list so the caller can save after every
    listing: a run that dies on number 14 keeps the first thirteen.

    One listing can never take down the run. A crash or a hang is recorded as
    an error on that Reading and the loop moves on.
    """
    for index, candidate in enumerate(candidates):
        if on_start:
            on_start(index, candidate)
        try:
            facts = await asyncio.wait_for(
                read_listing(browser, candidate.url, model, max_steps), timeout=timeout_s
            )
            error = None if facts else "the agent returned no structured result"
        except asyncio.TimeoutError:
            facts, error = None, f"timed out after {timeout_s:.0f}s"
        except Exception as exc:
            facts, error = None, f"{type(exc).__name__}: {exc}"
        yield Reading(candidate=candidate, facts=facts, error=error)
