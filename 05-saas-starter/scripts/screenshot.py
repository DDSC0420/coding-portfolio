"""Make docs/billing.png and docs/checkout.png by driving the real app (dev only: playwright, pillow, Chrome).

    python scripts/screenshot.py
"""

import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tally.web import Config, create_app  # noqa: E402


def shrink(path: Path) -> None:
    Image.open(path).convert("RGB").quantize(256).save(path, optimize=True)
    print(f"{path} ({path.stat().st_size // 1024} KB)")


def main() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    db = Path(tempfile.mkdtemp()) / "tally.db"
    server = uvicorn.Server(uvicorn.Config(create_app(Config(db_path=db)), port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1180, "height": 900}, device_scale_factor=2, base_url=base)
        page.goto("/signup")
        page.get_by_label("Email").fill("kyran@example.com")
        page.get_by_label("Password").fill("tangerine-orbit-71")
        page.get_by_role("button", name="Create account").click()
        for name in ("Website relaunch", "Mobile app", "Q3 launch"):
            page.get_by_label("New project name").fill(name)
            page.get_by_role("button", name="Add project").click()
        page.goto("/app/billing")
        page.get_by_test_id("choose-team").click()
        page.set_viewport_size({"width": 1180, "height": 520})
        page.screenshot(path=str(ROOT / "docs" / "checkout.png"))
        page.set_viewport_size({"width": 1180, "height": 900})
        page.get_by_test_id("pay").click()
        page.get_by_role("button", name="Renew: payment fails").click()
        page.screenshot(path=str(ROOT / "docs" / "billing.png"), full_page=True)
        browser.close()
    server.should_exit = True
    for name in ("checkout.png", "billing.png"):
        shrink(ROOT / "docs" / name)


if __name__ == "__main__":
    main()
