"""Screenshot the real calibration and preview windows, light and dark (Windows only).

The windows are SitRight's own code; only the camera is replaced by a drawn
stand-in picture (tools/stand_in.py) and fixed face measurements.

    python tools/shoot_windows.py OUTDIR
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2  # noqa: E402

from sitright import app, config  # noqa: E402
from sitright.monitor import Baseline, Sample  # noqa: E402
from stand_in import stand_in_frame  # noqa: E402

GOOD = Sample(ear=0.3, nose_y=0.5, face_width=0.3, pitch=0.0, nose_x=0.5)
SLOUCHED = Sample(ear=0.3, nose_y=0.62, face_width=0.3, pitch=0.0, nose_x=0.5)
CLOSE = Sample(ear=0.3, nose_y=0.5, face_width=0.42, pitch=0.0, nose_x=0.5)


# Keep the last image each window was given, so the shot is the window's own pixels.
# Grabbing the screen instead picks up whatever covers the window, which can be private.
SHOWN: dict[str, object] = {}
_imshow = cv2.imshow


def _remember(title, img):
    SHOWN[title] = img.copy()
    _imshow(title, img)


cv2.imshow = _remember


def grab(title: str, path: Path) -> None:
    shot = Image.fromarray(cv2.cvtColor(SHOWN[title], cv2.COLOR_BGR2RGB))
    # Round the corners like a Windows 11 window.
    k, radius = 4, round(7 * app.display_scale())
    mask = Image.new("L", (shot.width * k, shot.height * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width * k - 1, shot.height * k - 1), radius * k, fill=255)
    shot.putalpha(mask.resize(shot.size, Image.LANCZOS))
    shot.save(path)
    print(path)


def shoot_calibration(out: Path, theme: str) -> None:
    for name, lines, slouch in (
        ("calibration-countdown", ["3", "Sit the way you want to sit",
                                   "You're very close to the camera. Sit back to your normal distance."], False),
        ("calibration-capture", ["Hold still", "Look at the screen"], False),
    ):
        view = app.CvCalibrationView()
        frame = stand_in_frame(slouch)
        for _ in range(20):
            view.show(frame, lines)
            time.sleep(0.03)
        grab(view.TITLE, out / f"{name}-{theme}.png")
        view.close()


class FakeDetector:
    def __init__(self, sample: Sample, slouch: bool, close: bool) -> None:
        self.sample = sample
        self.last_frame = stand_in_frame(slouch, close)

    def read(self, now: float) -> Sample:
        return self.sample

    def close(self) -> None:
        pass


def shoot_preview(out: Path, theme: str) -> None:
    settings = config.Settings(baseline=Baseline.from_samples([GOOD] * 10))
    app.config.read = lambda: (settings, [])
    real_wait = cv2.waitKey
    for name, sample, slouch, close in (
        ("preview-good", GOOD, False, False),
        ("preview-slouching", SLOUCHED, True, False),
        ("preview-close", CLOSE, False, True),
    ):
        app.open_detector = lambda camera, s=sample, sl=slouch, c=close: FakeDetector(s, sl, c)
        frames = {"n": 0}

        def wait(delay: int, name=name) -> int:
            real_wait(delay)
            frames["n"] += 1
            if frames["n"] == 3:
                cv2.setWindowProperty(app.PREVIEW_TITLE, cv2.WND_PROP_TOPMOST, 1)
            if frames["n"] == 25:
                grab(app.PREVIEW_TITLE, out / f"{name}-{theme}.png")
                return ord("q")
            return -1

        cv2.waitKey = wait
        try:
            app.run_preview(camera=0)
        finally:
            cv2.waitKey = real_wait


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
    out.mkdir(parents=True, exist_ok=True)
    app.enable_high_dpi()
    for theme in ("light", "dark"):
        app.dark_mode = lambda theme=theme: theme == "dark"
        shoot_calibration(out, theme)
        shoot_preview(out, theme)


if __name__ == "__main__":
    main()
