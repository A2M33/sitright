"""Render the README images from SitRight's own code (Windows, Microsoft Edge).

1. The app and tray icons come from `sitright.icons`, the code that draws the tray.
2. tools/shoot_windows.py opens the real preview window and screenshots it.
3. Playwright renders header.html and overview.html with tokens.css and Archivo; header.html is the trace banner.

    pip install playwright
    python tools/docs/render.py

Writes docs/header.png (1280x640) and docs/overview.png + overview-dark.png (1200x750).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUILD = HERE / "_build"
DOCS = ROOT / "docs"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE.parent))

from make_icons import app_icon  # noqa: E402
from sitright import icons  # noqa: E402


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    app_icon(208).save(BUILD / "app-icon.png")
    for state in icons.STATES:
        icons.make_icon(state, 96).save(BUILD / f"tray-{state}.png")
    subprocess.run([sys.executable, str(HERE.parent / "shoot_windows.py"), str(BUILD)], check=True)

    jobs = [
        ("header.html", "header.png", (1280, 640), "dark"),
        ("overview.html", "overview.png", (1200, 750), "light"),
        ("overview.html", "overview-dark.png", (1200, 750), "dark"),
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", args=["--disable-lcd-text"])
        for page_name, out, (w, h), scheme in jobs:
            page = browser.new_page(viewport={"width": w, "height": h}, color_scheme=scheme)
            page.goto((HERE / page_name).as_uri())
            page.evaluate("document.fonts.ready")
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(DOCS / out))
            page.close()
            print(DOCS / out)
        browser.close()


if __name__ == "__main__":
    main()
