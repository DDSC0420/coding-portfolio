"""End to end in a real browser against a real server.

Skipped automatically when Playwright isn't installed. Set PLAYWRIGHT_CHANNEL=chrome to use a
locally installed Chrome.
"""

import json
import os
import socket
import threading
import time

import pytest
import uvicorn

from intake.web import create_app

from .conftest import SAMPLES, TODAY

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
def base_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(today=lambda: TODAY), host="127.0.0.1", port=port,
                                           log_level="warning"))
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
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda exc: page.errors.append(str(exc)))
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.goto(base_url)
    yield page
    context.close()


def test_a_sample_shows_its_review_items_and_evidence(page):
    page.click("button[data-name='rowan-hale.txt']")
    expect(page.locator("#cand-name")).to_have_text("Rowan Hale")
    expect(page.locator("#verdict")).to_have_text("5 things to check")
    expect(page.locator(".notice.review li")).to_have_count(5)

    # Clicking a review item lights up its card and the exact text in the CV.
    page.locator(".notice.review li", has_text="Ends before it starts").click()
    expect(page.locator(".card.on .card-title")).to_have_text("Contact Centre Supervisor")
    lit = page.locator("#cv mark.on")
    expect(lit).to_have_count(3)
    assert lit.all_inner_texts() == ["Contact Centre Supervisor", "Clifton Telecom", "06/2016 - 03/2015"]
    assert page.errors == []


def test_a_clean_cv_is_ready_and_a_skill_points_at_the_skills_list(page):
    page.click("button[data-name='amara-osei.pdf']")
    expect(page.locator("#verdict")).to_have_text("Ready to import")
    expect(page.locator(".row.found")).to_have_count(7)  # five contact fields, links, years
    page.locator(".chip", has_text="Terraform").click()
    mark = page.locator("#cv mark.on")
    expect(mark).to_have_text("Terraform")
    text_before = page.evaluate("""() => {
        const m = document.querySelector('#cv mark.on');
        const r = document.createRange(); r.setStart(document.getElementById('cv'), 0); r.setEndBefore(m);
        return r.toString(); }""")
    assert text_before.rstrip().endswith("Infrastructure as code:")


def test_uploading_a_file_and_downloading_the_record(page):
    page.set_input_files("#file", str(SAMPLES / "tom-brennan.docx"))
    expect(page.locator("#cand-name")).to_have_text("Tom Brennan")
    expect(page.locator("#source-meta")).to_contain_text("tom-brennan.docx")
    with page.expect_download() as info:
        page.click("#download")
    record = json.loads(open(info.value.path()).read())
    assert record["candidate"]["phone"]["value"] == "+447700900456" and "text" not in record


def test_an_unreadable_file_shows_the_reason(page, tmp_path):
    bad = tmp_path / "old.doc"
    bad.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 64)
    page.set_input_files("#file", str(bad))
    expect(page.locator("#error")).to_contain_text("Old Word .doc files aren't supported")


def test_cv_text_can_never_run_as_code(page, tmp_path):
    hostile = tmp_path / "cv.txt"
    hostile.write_text("Alex <img src=x onerror=window.pwned=1> Example\nalex@example.com\n\nSKILLS\n"
                       "<script>window.pwned=2</script>, Excel\n")
    page.set_input_files("#file", str(hostile))
    expect(page.locator("#cv")).to_contain_text("<img src=x onerror=window.pwned=1>")
    assert page.evaluate("window.pwned") is None
    assert page.locator("#cv img, #fields img, script:not([src])").count() == 0


def test_works_on_a_phone_sized_screen(browser, base_url):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(base_url)
    page.click("button[data-name='sofia-marlowe.txt']")
    expect(page.locator("#cand-name")).to_have_text("Sofia Marlowe")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "page scrolls sideways"
    context.close()
