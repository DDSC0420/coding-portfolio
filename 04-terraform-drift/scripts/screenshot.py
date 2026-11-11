"""Make docs/terminal.png from real plandrift output (dev only: needs playwright, pillow and Chrome).

    python scripts/screenshot.py [examples/console-changes.json]
"""

import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLOURS = {"31": "#ff7b72", "32": "#7ee787", "33": "#e3b341", "35": "#d2a8ff", "36": "#79c0ff"}


def ansi_to_html(text: str) -> str:
    out, open_spans = [], 0
    for part in re.split(r"(\033\[[0-9;]*m)", text):
        m = re.fullmatch(r"\033\[([0-9;]*)m", part)
        if not m:
            out.append(html.escape(part))
            continue
        codes = m.group(1).split(";")
        if codes == ["0"]:
            out.append("</span>" * open_spans)
            open_spans = 0
            continue
        style = []
        for c in codes:
            if c == "1":
                style.append("font-weight:700")
            elif c == "2":
                style.append("color:#8b949e")
            elif c in COLOURS:
                style.append(f"color:{COLOURS[c]}")
        out.append(f'<span style="{";".join(style)}">')
        open_spans += 1
    return "".join(out) + "</span>" * open_spans


def main() -> None:
    plan = sys.argv[1] if len(sys.argv) > 1 else "examples/console-changes.json"
    proc = subprocess.run(
        [sys.executable, "-m", "plandrift", "check", plan, "--color", "always"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    body = ansi_to_html(proc.stdout.rstrip("\n"))
    prompt = '<span style="color:#7ee787">$</span> '
    page = f"""<!doctype html><meta charset="utf-8"><style>
      body {{ margin:0; font-family: system-ui, sans-serif; }}
      .frame {{ display:inline-block; padding:28px; background:#e6e9ef; }}
      .win {{ display:inline-block; background:#0d1117; border-radius:10px; box-shadow:0 12px 32px rgba(0,0,0,.28);
              overflow:hidden; }}
      .bar {{ height:34px; background:#161b22; display:flex; align-items:center; padding:0 14px; gap:8px; }}
      .dot {{ width:12px; height:12px; border-radius:50%; }}
      .title {{ color:#8b949e; font-size:13px; margin-left:12px; }}
      pre {{ margin:0; padding:18px 22px 22px; color:#e6edf3; font: 15px/1.5 "DejaVu Sans Mono", Menlo, monospace; }}
    </style>
    <div class="frame"><div class="win"><div class="bar"><span class="dot" style="background:#ff5f56"></span>
    <span class="dot" style="background:#ffbd2e"></span><span class="dot" style="background:#27c93f"></span>
    <span class="title">refresh-only plan, after someone "fixed" prod in the console</span></div>
    <pre>{prompt}plandrift check plan.json
{body}
{prompt}echo $?
<span style="color:#ff7b72;font-weight:700">2</span></pre></div></div>"""

    from PIL import Image
    from playwright.sync_api import sync_playwright

    target = ROOT / "docs" / "terminal.png"
    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "t.html"
        page_file.write_text(page, encoding="utf-8")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            tab = browser.new_page(device_scale_factor=2)
            tab.goto(page_file.as_uri())
            tab.locator(".frame").screenshot(path=str(target))
            browser.close()
    Image.open(target).convert("RGB").quantize(256).save(target, optimize=True)
    print(f"{target} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
