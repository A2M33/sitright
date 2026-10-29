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
from .engine import CameraError
from .monitor import Sample, eye_aspect_ratio, head_pitch

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


def camera_works(index: int) -> bool:
    """Open camera `index`, grab one picture, let go."""
    cap = cv2.VideoCapture(index)
    try:
        return cap.isOpened() and cap.read()[0]
    finally:
        cap.release()


class Detector:
    def __init__(self, camera: int = 0) -> None:
        self.camera = camera
        self._cap = cv2.VideoCapture(camera)
        if not self._cap.isOpened():
            self._cap.release()
            raise CameraError(f"Can't open camera {camera}. Is another app using it?")
        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path())),
            running_mode=RunningMode.VIDEO,
            num_faces=1,
            output_facial_transformation_matrixes=True,  # head pose, for head_pitch()
        )
        try:
            self._landmarker = FaceLandmarker.create_from_options(options)
        except Exception:
            self._cap.release()
            raise
        self._last_ms = 0
        self.last_frame = None  # kept only in memory, for the optional preview window

    def read(self, now: float) -> Sample | None:
        """None means "no face in this frame". A broken camera raises CameraError instead."""
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraError(f"Camera {self.camera} stopped sending pictures.")
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
        matrices = result.facial_transformation_matrixes
        return Sample(
            ear=ear,
            nose_y=lm[NOSE_TIP].y,
            face_width=math.dist(pt(LEFT_CHEEK), pt(RIGHT_CHEEK)),
            pitch=head_pitch(matrices[0]) if matrices else None,
            nose_x=lm[NOSE_TIP].x,
        )

    def close(self) -> None:
        self._cap.release()
        self._landmarker.close()
