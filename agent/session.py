"""Open a Steel cloud browser, hand it to the caller, always release it.

Every browser run goes through `steel_browser()` so a crash can never leak a
session — leaked sessions burn free-tier hours and keep running after the
script exits.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from browser_use import Browser
from dotenv import load_dotenv
from steel import Steel

load_dotenv()


@asynccontextmanager
async def steel_browser() -> AsyncIterator[tuple[Browser, str]]:
    """Yield a started Browser and its live-viewer URL. Releases on any exit."""
    api_key = os.getenv("STEEL_API_KEY")
    if not api_key:
        raise SystemExit("STEEL_API_KEY is missing from .env")

    client = Steel(steel_api_key=api_key)
    session = client.sessions.create()
    browser = Browser(cdp_url=session.websocket_url, is_local=False)
    try:
        await browser.start()
        yield browser, session.session_viewer_url
    finally:
        try:
            await browser.stop()
        except Exception as exc:  # a failed detach must not block the release
            print(f"  warning: browser detach failed ({exc})")
        client.sessions.release(session.id)


async def evaluate(browser: Browser, expression: str) -> Any:
    """Run JavaScript in the page and return its value.

    This is how the collector reads listing pages: the extraction is plain
    DOM querying, so hrefs and titles come back exactly as the page has them
    rather than as something a model retyped.
    """
    cdp = await browser.get_or_create_cdp_session()
    result = await cdp.cdp_client.send.Runtime.evaluate(
        params={"expression": expression, "returnByValue": True, "awaitPromise": True},
        session_id=cdp.session_id,
    )
    if "exceptionDetails" in result:
        detail = result["exceptionDetails"]
        raise RuntimeError(f"page script failed: {detail.get('text', detail)}")
    return result["result"].get("value")


async def evaluate_json(browser: Browser, expression: str) -> Any:
    """Run JavaScript that returns a JSON string, and decode it."""
    raw = await evaluate(browser, expression)
    return json.loads(raw) if isinstance(raw, str) else raw
