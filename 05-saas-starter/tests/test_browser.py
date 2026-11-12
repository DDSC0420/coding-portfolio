"""The whole subscription journey in a real browser against a real server.

Skipped automatically when Playwright isn't installed. Set PLAYWRIGHT_CHANNEL=chrome to use a
locally installed Chrome.
"""

import os
import socket
import threading
import time

import pytest
import uvicorn

from tally.web import Config, create_app

from .conftest import PASSWORD

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
def base_url(tmp_path_factory):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(Config(db_path=tmp_path_factory.mktemp("browser") / "tally.db"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        chrome = launch_chromium(playwright)
        yield chrome
        chrome.close()


@pytest.fixture
def page(browser, base_url):
    context = browser.new_context(accept_downloads=True, base_url=base_url)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda exc: page.errors.append(str(exc)))
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    yield page
    context.close()


def test_sign_up_upgrade_fail_a_payment_cancel(page):
    # Sign up and hit the free limit.
    page.goto("/")
    page.get_by_role("link", name="Start free").first.click()
    page.get_by_label("Email").fill("journey@example.com")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_test_id("plan-badge")).to_have_text("Free")
    for name in ("Website", "Mobile app", "Q3 launch"):
        page.get_by_label("New project name").fill(name)
        page.get_by_role("button", name="Add project").click()
    expect(page.get_by_test_id("limit")).to_contain_text("The Free plan includes 3 projects.")
    expect(page.get_by_test_id("export")).to_have_count(0)

    # Upgrade through the test checkout.
    page.get_by_role("link", name="Upgrade to Pro").click()
    page.get_by_test_id("choose-pro").click()
    expect(page.get_by_role("note")).to_contain_text("TEST MODE")
    page.get_by_test_id("pay").click()
    expect(page.get_by_role("status")).to_have_text("Payment received. Your plan is live.")
    expect(page.get_by_test_id("plan-badge")).to_have_text("Pro")
    expect(page.get_by_test_id("status")).to_have_text("active")
    expect(page.get_by_test_id("events")).to_contain_text("customer.subscription.created")

    # Pro features work.
    page.get_by_role("link", name="Projects").click()
    page.get_by_label("New project name").fill("Fourth project")
    page.get_by_role("button", name="Add project").click()
    expect(page.get_by_test_id("projects")).to_contain_text("Fourth project")
    with page.expect_download() as download:
        page.get_by_test_id("export").click()
    assert download.value.suggested_filename == "projects.csv"

    # A renewal payment fails: past due, still Pro, banner shown.
    page.get_by_role("link", name="Billing").click()
    page.get_by_role("button", name="Renew: payment fails").click()
    expect(page.get_by_test_id("status")).to_have_text("past_due")
    expect(page.get_by_test_id("past-due")).to_be_visible()
    expect(page.get_by_test_id("plan-badge")).to_have_text("Pro")

    # Replaying the last webhook changes nothing.
    page.get_by_role("button", name="Replay last webhook").click()
    expect(page.get_by_test_id("result")).to_contain_text("duplicate: already processed")

    # Retry succeeds, then cancel: Pro until the period ends, then Free.
    page.get_by_role("button", name="Renew: payment succeeds").click()
    expect(page.get_by_test_id("status")).to_have_text("active")
    page.get_by_role("button", name="Cancel subscription").click()
    expect(page.get_by_role("button", name="Resume subscription")).to_be_visible()
    expect(page.get_by_test_id("plan-badge")).to_have_text("Pro")
    page.get_by_role("button", name="Renew: payment succeeds").click()
    expect(page.get_by_test_id("status")).to_have_text("canceled")
    expect(page.get_by_test_id("plan-badge")).to_have_text("Free")

    assert page.errors == []
