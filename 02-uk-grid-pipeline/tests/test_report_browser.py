"""Open the generated report in a real browser: it must render and respond to hover."""

import os
from datetime import datetime

import pytest

from gridwatch.pipeline import run

from .test_pipeline import FakeApi

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect


@pytest.mark.parametrize("width", [1200, 390])
def test_report_renders_and_tooltips_work(tmp_path, width):
    now = datetime(2026, 3, 15)
    result = run(tmp_path / "g.duckdb", tmp_path / "site", fetch=FakeApi(), now=lambda: now, say=lambda _: None)
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL") or None)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(result.report.resolve().as_uri())

        expect(page.locator(".tile")).to_have_count(4)
        expect(page.locator("#daily svg path.line")).to_have_count(1)
        expect(page.locator("#hourly svg path.mark")).to_have_count(24)
        expect(page.locator("#mix svg path.mark")).to_have_count(9)
        expect(page.locator("#checks li")).to_have_count(6)

        page.locator("#daily svg rect.hit").hover()
        expect(page.locator("#daily .tip")).to_contain_text("gCO₂/kWh")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "page scrolls sideways"
        assert errors == []
        browser.close()
