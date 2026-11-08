"""The tray icon, drawn from the A2M33 icon family.

A slate tile with a light line glyph (a head over a straight back on a seat) and
a status lamp in the top right corner. The lamp carries the state:

    ok     green lamp: you're at the desk and sitting well
    warn   amber lamp, and the figure slumps forward, so the state doesn't rest
           on telling two colours apart
    idle   grey lamp: paused, away, locked, calibrating or not calibrated yet
    fault  red lamp: the camera or the face model isn't working

Coordinates are in the family's 512-unit grid (see notes/design/icons). The lamp
is larger than on the family app icon so it still reads at the 16-pixel tray size.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

GRID = 512
TILE = "#242b27"
EDGE = "#3d4741"
INK = "#e2e6e0"
LAMPS = {
    "ok": "#6fb282",
    "warn": "#f0ab2a",
    "idle": "#56605a",
    "fault": "#e2584d",
}
STATES = tuple(LAMPS)
STROKE = 30
CHAIR_ALPHA = 0.45

LAMP_CENTER = (396, 116)
LAMP_RADIUS = 52
LAMP_GAP = 10  # a ring of tile colour keeps the lamp apart from the glyph

HIP = (220, 320)
SEAT_FRONT = (330, 320)
FOOT = (330, 396)
UPRIGHT = {"head": (220, 140), "shoulder": (220, 214)}
SLUMPED = {"head": (306, 192), "shoulder": (252, 228)}
HEAD_RADIUS = 38
CHAIR = [((168, 250), (168, 400)), ((168, 344), (290, 344))]


def make_icon(state: str, size: int = 64) -> Image.Image:
    """The tray icon for `state` at `size` pixels."""
    return draw(LAMPS[state], slumped=state == "warn", size=size)


def draw(lamp: str, slumped: bool = False, size: int = 64) -> Image.Image:
    """The glyph on its tile with a `lamp` coloured lamp. Drawn large, scaled down for smooth edges."""
    big = max(size * 4, GRID)
    k = big / GRID

    def px(*values: float) -> list[float]:
        return [v * k for v in values]

    def stroke(draw: ImageDraw.ImageDraw, points, fill) -> None:
        width = round(STROKE * k)
        flat = [c for p in points for c in px(*p)]
        draw.line(flat, fill=fill, width=width, joint="curve")
        r = STROKE / 2
        for x, y in (points[0], points[-1]):  # round caps, which PIL lines don't have
            draw.ellipse(px(x - r, y - r, x + r, y + r), fill=fill)

    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(px(8, 8, 504, 504), radius=112 * k, fill=TILE, outline=EDGE, width=round(8 * k))

    chair = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    cd = ImageDraw.Draw(chair)
    for line in CHAIR:
        stroke(cd, line, INK)
    alpha = chair.getchannel("A").point(lambda a: round(a * CHAIR_ALPHA))
    chair.putalpha(alpha)
    img.alpha_composite(chair)

    pose = SLUMPED if slumped else UPRIGHT
    stroke(d, [pose["shoulder"], HIP, SEAT_FRONT, FOOT], INK)
    hx, hy = pose["head"]
    d.ellipse(px(hx - HEAD_RADIUS, hy - HEAD_RADIUS, hx + HEAD_RADIUS, hy + HEAD_RADIUS), fill=INK)

    lx, ly = LAMP_CENTER
    ring = LAMP_RADIUS + LAMP_GAP
    d.ellipse(px(lx - ring, ly - ring, lx + ring, ly + ring), fill=TILE)
    d.ellipse(px(lx - LAMP_RADIUS, ly - LAMP_RADIUS, lx + LAMP_RADIUS, ly + LAMP_RADIUS), fill=lamp)
    return img.resize((size, size), Image.LANCZOS)
