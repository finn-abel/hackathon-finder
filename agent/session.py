"""Open a Steel cloud browser, hand it to the caller, always release it.

Every browser run goes through `steel_browser()`. A leaked session keeps
running after the script exits and burns free-tier hours, so the release path
is deliberately paranoid:

  - the release runs in `finally`, so an exception, a `SystemExit` or a
    Ctrl+C still releases;
  - detaching the browser is time-boxed, because a detach that hangs would
    otherwise stop the release ever being reached;
  - a failed detach is reported but never blocks the release;
  - the release is verified, and if it did not take, the session id is
    printed with the command to clean it up.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from browser_use import Browser
from dotenv import load_dotenv
from steel import Steel

load_dotenv()

#: A detach that hangs must not cost the session. 15s is far longer than a
#: healthy CDP detach and still short enough to keep the release prompt.
DETACH_TIMEOUT_S = 15.0
RELEASED = "released"


def steel_client(api_key: str | None = None) -> Steel:
    key = api_key or os.getenv("STEEL_API_KEY")
    if not key:
        raise SystemExit("STEEL_API_KEY is missing from .env")
    return Steel(steel_api_key=key)


async def _detach(browser: Browser) -> None:
    """Let go of the browser, whatever it takes. Never raises."""
    try:
        await asyncio.wait_for(browser.stop(), timeout=DETACH_TIMEOUT_S)
    except asyncio.TimeoutError:
        print(f"  warning: browser detach timed out after {DETACH_TIMEOUT_S:.0f}s "
              f"— releasing the session anyway")
    except Exception as exc:
        print(f"  warning: browser detach failed ({exc}) — releasing the session anyway")


def release_session(client: Steel, session_id: str) -> bool:
    """Release one session and confirm it took. Never raises."""
    try:
        client.sessions.release(session_id)
    except Exception as exc:
        print(f"  ERROR: could not release Steel session {session_id}: {exc}")
        print(f"  Clean it up with:  uv run sessions.py --release")
        return False

    try:
        status = client.sessions.retrieve(session_id).status
    except Exception:
        return True  # the release call succeeded; treat an unverifiable read as fine

    if status != RELEASED:
        print(f"  WARNING: session {session_id} still reads as {status!r} after release.")
        print(f"  Check it with:  uv run sessions.py")
        return False
    return True


@asynccontextmanager
async def steel_browser(client: Steel | None = None) -> AsyncIterator[tuple[Browser, str]]:
    """Yield a started Browser and its live-viewer URL. Releases on any exit."""
    client = client or steel_client()
    session = client.sessions.create()
    browser = Browser(cdp_url=session.websocket_url, is_local=False)
    try:
        await browser.start()
        yield browser, session.session_viewer_url
    finally:
        await _detach(browser)
        release_session(client, session.id)


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
