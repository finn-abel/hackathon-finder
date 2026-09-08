"""Steel sessions must always be released. A leak keeps billing after the
process exits, so every exit path is pinned here with a fake client.
"""

import asyncio

import pytest

import agent.session as session_module
from agent.session import release_session, steel_browser


class FakeSession:
    def __init__(self, session_id="sess-1"):
        self.id = session_id
        self.websocket_url = "ws://fake"
        self.session_viewer_url = "https://app.steel.dev/sessions/sess-1"
        self.status = "live"


class FakeSessions:
    def __init__(self, release_raises=False, status_after="released"):
        self.released: list[str] = []
        self.release_raises = release_raises
        self.status_after = status_after
        self.session = FakeSession()

    def create(self):
        return self.session

    def release(self, session_id):
        if self.release_raises:
            raise RuntimeError("network down")
        self.released.append(session_id)

    def retrieve(self, session_id):
        self.session.status = self.status_after
        return self.session

    def list(self, status=None):
        return [self.session]


class FakeClient:
    def __init__(self, **kw):
        self.sessions = FakeSessions(**kw)


class FakeBrowser:
    """Stands in for browser_use.Browser."""

    def __init__(self, cdp_url=None, is_local=False, stop_behaviour="ok"):
        self.stop_behaviour = stop_behaviour
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        if self.stop_behaviour == "raise":
            raise RuntimeError("detach exploded")
        if self.stop_behaviour == "hang":
            await asyncio.sleep(60)
        self.stopped = True


@pytest.fixture
def fake_browser(monkeypatch):
    """Install a fake Browser and hand back the instance that gets built."""
    made: list[FakeBrowser] = []

    def build(behaviour="ok"):
        def factory(cdp_url=None, is_local=False):
            browser = FakeBrowser(cdp_url, is_local, behaviour)
            made.append(browser)
            return browser
        monkeypatch.setattr(session_module, "Browser", factory)
        return made
    return build


# --- every exit path releases ---------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_run_releases_the_session(fake_browser):
    fake_browser()
    client = FakeClient()
    async with steel_browser(client) as (browser, viewer):
        assert browser.started
        assert viewer.startswith("https://app.steel.dev/")
    assert client.sessions.released == ["sess-1"]


@pytest.mark.asyncio
async def test_an_exception_inside_the_block_still_releases(fake_browser):
    fake_browser()
    client = FakeClient()
    with pytest.raises(ValueError):
        async with steel_browser(client):
            raise ValueError("the collector blew up")
    assert client.sessions.released == ["sess-1"]


@pytest.mark.asyncio
async def test_a_keyboard_interrupt_still_releases(fake_browser):
    # Ctrl+C is the most likely way a real run ends early.
    fake_browser()
    client = FakeClient()
    with pytest.raises(KeyboardInterrupt):
        async with steel_browser(client):
            raise KeyboardInterrupt
    assert client.sessions.released == ["sess-1"]


@pytest.mark.asyncio
async def test_a_cancelled_task_still_releases(fake_browser):
    fake_browser()
    client = FakeClient()

    async def run():
        async with steel_browser(client):
            await asyncio.sleep(30)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.sessions.released == ["sess-1"]


# --- a broken detach must never cost the session --------------------------


@pytest.mark.asyncio
async def test_a_detach_that_raises_does_not_block_the_release(fake_browser, capsys):
    fake_browser("raise")
    client = FakeClient()
    async with steel_browser(client):
        pass
    assert client.sessions.released == ["sess-1"]
    assert "detach failed" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_detach_that_hangs_is_timed_out_and_the_session_released(
    fake_browser, monkeypatch, capsys
):
    # Without the timeout this is the real leak: stop() never returns, so
    # release() is never reached and the session bills on.
    monkeypatch.setattr(session_module, "DETACH_TIMEOUT_S", 0.05)
    fake_browser("hang")
    client = FakeClient()
    async with steel_browser(client):
        pass
    assert client.sessions.released == ["sess-1"]
    assert "timed out" in capsys.readouterr().out


# --- reporting when the release itself fails ------------------------------


def test_a_failed_release_is_reported_not_raised(capsys):
    client = FakeClient(release_raises=True)
    assert release_session(client, "sess-1") is False
    out = capsys.readouterr().out
    assert "could not release" in out
    assert "sessions.py --release" in out      # tells you how to fix it


def test_a_release_that_does_not_take_is_flagged(capsys):
    client = FakeClient(status_after="live")
    assert release_session(client, "sess-1") is False
    assert "still reads as 'live'" in capsys.readouterr().out


def test_a_verified_release_reports_success():
    client = FakeClient(status_after="released")
    assert release_session(client, "sess-1") is True


@pytest.mark.asyncio
async def test_a_failing_release_does_not_mask_the_real_error(fake_browser):
    # If the body raised, that exception is what the user needs to see.
    fake_browser()
    client = FakeClient(release_raises=True)
    with pytest.raises(ValueError, match="the real problem"):
        async with steel_browser(client):
            raise ValueError("the real problem")
