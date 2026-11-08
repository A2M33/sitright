"""A drawn stand-in for a webcam picture, so the docs show the windows without a real face.

The figure's head drops when `slouch` is True, like a real slouch seen from the screen.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter


def stand_in_frame(slouch: bool = False, close: bool = False, size: tuple[int, int] = (640, 480)):
    """A BGR numpy frame like the one the detector keeps in `last_frame`.

    `close` zooms in, the way a face grows when you lean toward the screen.
    """
    import numpy

    w, h = size
    img = Image.new("RGB", size, "#b9b4a8")  # a painted wall
    d = ImageDraw.Draw(img)
    d.rectangle((0, round(h * 0.82), w, h), fill="#8c7a66")  # chair back and desk edge
    d.rectangle((round(w * 0.06), round(h * 0.12), round(w * 0.30), round(h * 0.48)), fill="#cfcbc1")
    d.rectangle((round(w * 0.06), round(h * 0.48), round(w * 0.30), round(h * 0.50)), fill="#9b958a")

    drop = round(h * 0.09) if slouch else 0
    cx = w // 2
    # Shoulders and body in a dark sweater.
    d.ellipse((cx - 190, round(h * 0.70) + drop, cx + 190, round(h * 1.30) + drop), fill="#3f4a55")
    # Neck, head and hair.
    d.rectangle((cx - 34, round(h * 0.52) + drop, cx + 34, round(h * 0.74) + drop), fill="#c79d82")
    d.ellipse((cx - 82, round(h * 0.20) + drop, cx + 82, round(h * 0.62) + drop), fill="#d4aa8d")
    d.chord((cx - 86, round(h * 0.17) + drop, cx + 86, round(h * 0.47) + drop), 180, 360, fill="#3b2f28")
    if close:
        zw, zh = round(w / 1.3), round(h / 1.3)
        left, top = (w - zw) // 2, round((h - zh) * 0.35)
        img = img.crop((left, top, left + zw, top + zh)).resize(size, Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(2.2))  # a webcam is never sharp
    return numpy.ascontiguousarray(numpy.asarray(img)[:, :, ::-1])
