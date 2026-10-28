"""Webcam frame -> Sample, using MediaPipe's face landmark model.

The model runs locally. Frames are never saved or sent anywhere.
"""

from __future__ import annotations

import math
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions, RunningMode

from .config import cache_dir
from .monitor import Sample

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)

# Landmark indices in MediaPipe's 478-point face mesh.
LEFT_EYE = (33, 160, 158, 133, 153, 144)  # corner, top, top, corner, bottom, bottom
RIGHT_EYE = (362, 385, 387, 263, 373, 380)
NOSE_TIP = 1
LEFT_CHEEK, RIGHT_CHEEK = 234, 454


def model_path() -> Path:
    path = cache_dir() / "face_landmarker.task"
    if not path.exists():
        print("Downloading face model (one time, ~4 MB)...")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
    return path


def eye_aspect_ratio(points: list[tuple[float, float]]) -> float:
    """Height of the eye divided by its width. Drops sharply when the eye closes."""
    p1, p2, p3, p4, p5, p6 = points
    width = math.dist(p1, p4)
    if width == 0:
        return 0.0
    return (math.dist(p2, p6) + math.dist(p3, p5)) / (2 * width)


class Detector:
    def __init__(self, camera: int = 0) -> None:
        self._cap = cv2.VideoCapture(camera)
        if not self._cap.isOpened():
            raise RuntimeError(f"Can't open camera {camera}. Is another app using it?")
        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path())),
            running_mode=RunningMode.VIDEO,
            num_faces=1,
        )
        self._landmarker = FaceLandmarker.create_from_options(options)
        self._last_ms = 0
        self.last_frame = None  # kept only in memory, for the optional preview window

    def read(self, now: float) -> Sample | None:
        ok, frame = self._cap.read()
        if not ok:
            return None
        self.last_frame = frame
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # VIDEO mode needs strictly increasing timestamps.
        ms = max(int(now * 1000), self._last_ms + 1)
        self._last_ms = ms
        result = self._landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ms
        )
        if not result.face_landmarks:
            return None
        lm = result.face_landmarks[0]

        # Landmarks are 0..1 per axis; scale x by the aspect ratio so distances are fair.
        def pt(i: int) -> tuple[float, float]:
            return lm[i].x * w / h, lm[i].y

        ear = (
            eye_aspect_ratio([pt(i) for i in LEFT_EYE])
            + eye_aspect_ratio([pt(i) for i in RIGHT_EYE])
        ) / 2
        return Sample(
            ear=ear,
            nose_y=lm[NOSE_TIP].y,
            face_width=math.dist(pt(LEFT_CHEEK), pt(RIGHT_CHEEK)),
        )

    def close(self) -> None:
        self._cap.release()
        self._landmarker.close()
