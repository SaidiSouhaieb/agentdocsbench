#!/usr/bin/env python3
"""Render the LinkedIn carousel and the GitHub social preview.

The approved logo and cover are inputs. This script does not redraw them.

    python assets/carousel/render.py

Optional tool, not a package dependency: Google Chrome.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "assets" / "brand"
CAROUSEL = ROOT / "assets" / "carousel"
OUT = CAROUSEL / "output"
HTML = CAROUSEL / "index.html"

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
)

PAGES = (
    ("p1", "01-cover.png"),
    ("p2", "02-shift.png"),
    ("p3", "03-verify.png"),
    ("p4", "04-docs-change.png"),
    ("p5", "05-workflows.png"),
    ("p6", "06-capabilities.png"),
    ("p7", "07-cta.png"),
)


def chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    print("Google Chrome is required to render the carousel.", file=sys.stderr)
    raise SystemExit(1)


def icon_mark() -> None:
    """Crop the icon from the supplied logo. The artwork is not redrawn."""
    logo = Image.open(BRAND / "logo.png").convert("RGBA")
    pix = logo.load()
    width, height = logo.size
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        for x in range(width):
            red, green, blue, _alpha = pix[x, y]
            if red + green + blue > 40 and y < 700:
                xs.append(x)
                ys.append(y)
    center_x = (min(xs) + max(xs)) // 2
    center_y = (min(ys) + max(ys)) // 2
    half = max(max(xs) - min(xs), max(ys) - min(ys)) // 2 + 36
    mark = logo.crop((center_x - half, center_y - half, center_x + half, center_y + half))
    pixels = mark.load()
    mark_w, mark_h = mark.size
    for y in range(mark_h):
        for x in range(mark_w):
            red, green, blue, alpha = pixels[x, y]
            if red < 12 and green < 12 and blue < 12:
                pixels[x, y] = (red, green, blue, 0)
    mark.save(BRAND / "logo-mark.png", "PNG")


def social_preview() -> None:
    """Place the full approved cover on a 1280x640 canvas of its own background."""
    cover = Image.open(BRAND / "cover.png").convert("RGB")
    background = cover.getpixel((4, 4))
    canvas = Image.new("RGB", (1280, 640), background)
    fitted = cover.resize(
        (1280, round(1280 * cover.height / cover.width)),
        Image.Resampling.LANCZOS,
    )
    canvas.paste(fitted, (0, (640 - fitted.height) // 2))
    canvas.save(BRAND / "social-preview.png", "PNG")


def screenshot(binary: str, page_id: str, png: Path) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            binary,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--default-background-color=0004080b",
            "--virtual-time-budget=4000",
            f"--window-size=1080,1350",
            f"--screenshot={png}",
            HTML.as_uri() + f"#{page_id}",
        ],
        check=True,
        cwd=CAROUSEL,
    )


def pdf(binary: str, destination: Path) -> None:
    subprocess.run(
        [
            binary,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--virtual-time-budget=4000",
            f"--print-to-pdf={destination}",
            HTML.as_uri(),
        ],
        check=True,
        cwd=CAROUSEL,
    )


def main() -> None:
    if not (BRAND / "logo.png").is_file() or not (BRAND / "cover.png").is_file():
        print("assets/brand/logo.png and assets/brand/cover.png are required.", file=sys.stderr)
        raise SystemExit(1)
    icon_mark()
    social_preview()
    binary = chrome()
    for page_id, name in PAGES:
        screenshot(binary, page_id, OUT / name)
        image = Image.open(OUT / name)
        if image.size != (1080, 1350):
            print(f"{name} is {image.size}, expected 1080x1350", file=sys.stderr)
            raise SystemExit(1)
    pdf(binary, CAROUSEL / "agentdocsbench-linkedin.pdf")
    print(CAROUSEL / "agentdocsbench-linkedin.pdf")


if __name__ == "__main__":
    main()
