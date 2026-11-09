"""Write SitRight's icon files from the same code that draws the tray icon.

    python tools/make_icons.py            # src/sitright/assets/sitright.ico (window icon)
    python tools/make_icons.py OUTDIR     # also a PNG of every tray state, 16 to 256 px

The tray icon itself is drawn at run time by `sitright.icons.make_icon`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sitright import icons  # noqa: E402

ICO = ROOT / "src" / "sitright" / "assets" / "sitright.ico"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def app_icon(size: int):
    """The family app icon: the upright figure with the cyan signal lamp."""
    return icons.draw(icons.SIGNAL, slumped=False, size=size)


def main() -> None:
    ICO.parent.mkdir(parents=True, exist_ok=True)
    app_icon(256).save(ICO, sizes=[(s, s) for s in ICO_SIZES])
    print(ICO)
    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
        out.mkdir(parents=True, exist_ok=True)
        for state in icons.STATES:
            for size in (16, 24, 32, 64, 256):
                icons.make_icon(state, size).save(out / f"tray-{state}-{size}.png")
        print(out)


if __name__ == "__main__":
    main()
