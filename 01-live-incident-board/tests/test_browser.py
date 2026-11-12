"""End to end: two real browser tabs against a real server must stay in sync.

Skipped automatically when Playwright isn't installed, so the unit tests still
run anywhere. Set PLAYWRIGHT_CHANNEL=chrome to use a locally installed Chrome.
"""

import asyncio
import os
import socket
import threading
import time

import pytest
import uvicorn

from app.main import create_app

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect


def launch_chromium(playwright):
    """Start Chromium. If Playwright's own browser isn't downloaded yet, skip with the fix rather
    than erroring, except on CI, where a missing browser must fail the build."""
    try:
        return playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
    except sync_api.Error as exc:
        if os.environ.get("CI") or "Executable doesn't exist" not in str(exc):
            raise
        pytest.skip("Playwright's browser isn't installed: run `python -m playwright install chromium`, "
                    "or set PLAYWRIGHT_CHANNEL=chrome to use your own Chrome")


@pytest.fixture(scope="module")
def server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(drill_delay=0.05)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def base_url(server):
    return server[0]


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        chrome = launch_chromium(playwright)
        yield chrome
        chrome.close()


def test_two_tabs_stay_in_sync(base_url, browser):
    ana = browser.new_context().new_page()
    ben = browser.new_context().new_page()
    errors = []
    for page in (ana, ben):
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    ana.goto(base_url)  # redirects to a fresh board
    expect(ana.locator("#conn-text")).to_have_text("Live")
    ben.goto(ana.url)
    expect(ben.locator("#conn-text")).to_have_text("Live")
    expect(ana.locator("#people .avatar")).to_have_count(2)

    # Ana declares an incident; Ben sees it without touching anything.
    ana.fill("#title", "Payments timing out")
    ana.select_option("#severity", "sev1")
    ana.click("text=Declare incident")
    card = ben.locator(".column.investigating .card", has_text="Payments timing out")
    expect(card).to_be_visible()

    # Ben advances it; Ana's board moves the card.
    card.locator(".advance").click()
    expect(ana.locator(".column.identified .card", has_text="Payments timing out")).to_be_visible()

    # Ben posts an update from the detail drawer; it shows on Ana's card.
    ben.locator(".card", has_text="Payments timing out").click()
    ben.fill("#note", "Failing over to the secondary provider")
    ben.click("#note-form button")
    expect(ana.locator(".card", has_text="Payments timing out").locator(".last")).to_contain_text("Failing over")

    assert errors == []


def test_user_text_is_never_rendered_as_html(base_url, browser):
    page = browser.new_page()
    page.goto(base_url)
    expect(page.locator("#conn-text")).to_have_text("Live")
    page.fill("#title", "<img src=x onerror=window.pwned=1><b>bold</b>")
    page.click("text=Declare incident")
    expect(page.locator(".card h3", has_text="<b>bold</b>")).to_be_visible()  # shown literally
    assert page.locator(".card img").count() == 0
    assert page.evaluate("window.pwned") is None


def test_drill_runs_an_incident_through_to_resolved(base_url, browser):
    page = browser.new_page()
    page.goto(base_url)
    expect(page.locator("#conn-text")).to_have_text("Live")
    page.click("#drill")
    expect(page.locator(".column.resolved .card", has_text="Login failures")).to_be_visible()
    expect(page.locator("#drill")).to_have_text("Run drill")


def test_dropped_connection_reconnects_and_catches_up(server, browser):
    base_url, app = server
    page = browser.new_page()
    page.goto(base_url)
    expect(page.locator("#conn-text")).to_have_text("Live")
    room_id = page.url.rsplit("/", 1)[-1]

    async def cut_every_connection():
        for ws in list(app.state.sockets.get(room_id, {}).values()):
            await ws.close(code=1012)

    asyncio.run_coroutine_threadsafe(cut_every_connection(), app.state.loop).result(timeout=5)
    # Something happens on the board while this browser is disconnected.
    app.state.registry.rooms[room_id].create("Declared while you were offline", "sev2", "Ana")

    # No reload: the page must notice, reconnect and show what it missed.
    expect(page.locator(".card", has_text="Declared while you were offline")).to_be_visible()
    expect(page.locator("#conn-text")).to_have_text("Live")
