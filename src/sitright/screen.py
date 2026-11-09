"""What the calibration and preview windows show, drawn with Pillow.

The camera picture sits on top; under it a panel with the status lamp, one big
reading with a small label, optional smaller readings and wrapped notes. Colours
are the A2M33 "trace" tokens, light or dark following the Windows theme.
Archivo ships in `fonts/`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

FONTS = Path(__file__).with_name("fonts")

LIGHT = {
    "cab": "#f2f5fa", "panel": "#ffffff", "inset": "#e4eaf3", "line": "#cdd6e4",
    "ink": "#0c1628", "ink-2": "#4a5a73", "amber": "#c98a0b", "fault": "#c9343a", "ok": "#1d8458",
}
DARK = {
    "cab": "#0b1222", "panel": "#111a2d", "inset": "#18233a", "line": "#22304b",
    "ink": "#e6edf7", "ink-2": "#93a3bd", "amber": "#f0b23c", "fault": "#ff6b6b", "ok": "#3fcf93",
}
LAMP_COLOURS = {"ok": "ok", "warn": "amber", "live": "amber", "fault": "fault"}

# Sizes in pixels at 100% display scaling.
PAD = 16  # around the picture and the panel
GAP = 12  # between the picture and the panel
INNER = 20  # inside the panel
PANEL_RADIUS = 8
LAMP = 18
TEXT = 15
SMALL = 13


@dataclass
class Cell:
    """A smaller reading: its value and a label under it."""

    value: str
    label: str


@dataclass
class Screen:
    lamp: str = "idle"  # idle / ok / warn / live / fault
    reading: str = ""  # the big value, e.g. "3" or "Hold still"
    label: str = ""  # small text under the reading
    cells: list[Cell] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def dark_mode() -> bool:
    """True when Windows apps are set to dark. Other systems get the light look."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


@lru_cache(maxsize=None)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Archivo-{weight}.ttf"), size)


def wrap_text(text: str, max_width: int, measure: Callable[[str], int]) -> list[str]:
    """Split `text` at spaces into lines that fit. A word too wide by itself stays whole."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        longer = f"{line} {word}" if line else word
        if line and measure(longer) > max_width:
            lines.append(line)
            line = word
        else:
            line = longer
    return lines + [line] if line else lines


def text_width(text: str, f: ImageFont.FreeTypeFont) -> int:
    return round(f.getlength(text))


def note_lines(notes: list[str], scale: float, max_width: int) -> list[str]:
    """`notes` wrapped to `max_width` pixels in the note font."""
    f = font("Regular", round(TEXT * scale))
    return [part for note in notes for part in wrap_text(note, max_width, lambda t: text_width(t, f))]


def picture_origin(scale: float) -> tuple[int, int]:
    """Where the top left corner of the camera picture lands in the rendered image."""
    pad = round(PAD * scale)
    return pad, pad


def render(picture: Image.Image, screen: Screen, scale: float = 1.0, dark: bool = False) -> Image.Image:
    """The whole window: `picture` (already sized and mirrored) above the status panel."""
    c = DARK if dark else LIGHT

    def s(v: float) -> int:
        return round(v * scale)

    pad, gap, inner = s(PAD), s(GAP), s(INNER)
    width = picture.width + 2 * pad
    content = width - 2 * pad - 2 * inner
    size = s(40) if screen.reading.isdigit() else s(31)
    big = font("Medium" if screen.reading.isdigit() else "SemiBold", size)
    label_font, note_font = font("Regular", s(TEXT)), font("Regular", s(TEXT))
    cell_value, cell_label = font("Medium", s(20)), font("Regular", s(SMALL))
    lamp = s(LAMP)
    text_left = lamp + s(14)
    labels = wrap_text(screen.label, content - text_left, lambda t: text_width(t, label_font))
    notes = note_lines(screen.notes, scale, content)

    # Heights from the cap height down to the last baseline, plus room for descenders.
    cap = round(size * 0.7)
    line = s(21)
    first_label = s(23)
    status_h = cap + (first_label + line * (len(labels) - 1) + s(5) if labels else 0)
    cell_h = s(14) + s(20) + s(4)
    notes_h = s(11) + line * (len(notes) - 1) + s(5) if notes else 0
    blocks = [status_h] + ([cell_h] if screen.cells else []) + ([notes_h] if notes else [])
    panel_h = sum(blocks) + inner * 2 * len(blocks)
    height = pad + picture.height + gap + panel_h + pad

    img = Image.new("RGB", (width, height), c["cab"])
    d = ImageDraw.Draw(img)
    radius = s(PANEL_RADIUS)

    # The picture, with rounded corners and a 1px frame.
    x0, y0 = picture_origin(scale)
    x1, y1 = x0 + picture.width, y0 + picture.height
    fill_round(img, (x0 - 1, y0 - 1, x1 + 1, y1 + 1), radius + 1, c["line"])
    img.paste(picture.convert("RGB"), (x0, y0), round_mask(picture.width, picture.height, radius))

    # The panel.
    top = y1 + gap
    fill_round(img, (pad, top, width - pad, top + panel_h), radius, c["line"])
    fill_round(img, (pad + 1, top + 1, width - pad - 1, top + panel_h - 1), radius - 1, c["panel"])
    left = pad + inner
    y = top + inner

    # Status: the lamp, the reading, its label.
    baseline = y + cap
    ly = baseline - cap // 2 - lamp // 2
    if screen.lamp in LAMP_COLOURS:
        fill_round(img, (left, ly, left + lamp, ly + lamp), lamp / 2, c[LAMP_COLOURS[screen.lamp]])
    else:  # off: an empty socket
        fill_round(img, (left, ly, left + lamp, ly + lamp), lamp / 2, c["line"])
        fill_round(img, (left + 1, ly + 1, left + lamp - 1, ly + lamp - 1), lamp / 2 - 1, c["inset"])
    d.text((left + text_left, baseline), screen.reading, font=big, fill=c["ink"], anchor="ls")
    for i, text in enumerate(labels):
        d.text((left + text_left, baseline + first_label + line * i), text,
               font=label_font, fill=c["ink-2"], anchor="ls")
    y += status_h + inner

    def rule(at: int) -> None:
        d.line((pad + 1, at, width - pad - 2, at), fill=c["line"], width=1)

    if screen.cells:
        rule(y)
        y += inner
        step = content / len(screen.cells)
        for i, cell in enumerate(screen.cells):
            x = left + round(step * i)
            d.text((x, y + s(14)), cell.value, font=cell_value, fill=c["ink"], anchor="ls")
            d.text((x, y + s(34)), cell.label, font=cell_label, fill=c["ink-2"], anchor="ls")
        y += cell_h + inner
    if notes:
        rule(y)
        y += inner
        for i, text in enumerate(notes):
            d.text((left, y + s(11) + line * i), text, font=note_font, fill=c["ink"], anchor="ls")
    return img


@lru_cache(maxsize=64)
def round_mask(width: int, height: int, radius: float) -> Image.Image:
    """An anti-aliased rounded rectangle mask. PIL draws shapes with hard edges, so draw it 4x."""
    k = 4
    big = Image.new("L", (width * k, height * k), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, width * k - 1, height * k - 1), radius=radius * k, fill=255)
    return big.resize((width, height), Image.LANCZOS)


def fill_round(img: Image.Image, box: tuple[int, int, int, int], radius: float, colour: str) -> None:
    x0, y0, x1, y1 = box
    img.paste(ImageColor.getrgb(colour), box, round_mask(x1 - x0, y1 - y0, radius))


def fit_size(width: int, height: int, box: tuple[int, int]) -> tuple[int, int]:
    """The largest size inside `box` with the proportions of width x height."""
    ratio = min(box[0] / width, box[1] / height)
    return round(width * ratio), round(height * ratio)


def fit_picture(frame, box: tuple[int, int]) -> Image.Image:
    """A BGR camera frame as a mirrored Pillow image, as large as fits in `box`."""
    pic = ImageOps.mirror(Image.fromarray(frame[:, :, ::-1]))
    size = fit_size(pic.width, pic.height, box)
    return pic if size == pic.size else pic.resize(size, Image.BILINEAR)


def to_bgr(img: Image.Image):
    import numpy

    return numpy.ascontiguousarray(numpy.asarray(img.convert("RGB"))[:, :, ::-1])
