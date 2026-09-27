# Camaloop - one camera in, any camera out.
# Copyright (c) 2026 Khavish Auckaloo.
#
# This program is free software: you may redistribute it and/or modify it
# under the terms of version 3 of the GNU General Public License as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY - without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.

"""Finding faces, and keeping them steady between frames.

Two trackers, and the better one is used when it is there:

* MediaPipe Face Landmarker, when installed, gives a dense mesh and so a
  precise eye line, face width and tilt.
* OpenCV's Haar cascades otherwise. Far rougher, but they need nothing
  installed beyond the detection data, a copy of which travels with the app.

Whichever is used, a detection is eased towards its new position rather than
jumping to it, so anything that follows a face does not shake.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

# On a typical face the pupils sit about this far apart, measured against
# the width of the face. It is how a face width is worked out from an eye line.
EYE_GAP_TO_FACE = 0.34 / 0.78


# --------------------------------------------------------------------------
# Finding OpenCV's face detection data
# --------------------------------------------------------------------------
#
# OpenCV ships its face and eye detectors as XML files, but where they end up
# depends entirely on how OpenCV was installed. The wheels from pip put them
# inside the package, where cv2.data.haarcascades points. Debian and Ubuntu
# put the Python bindings in python3-opencv and the data in a separate
# opencv-data package that nothing depends on - so a perfectly ordinary
# install can leave OpenCV working and face detection with nothing to load.
#
# When that happened every effect that looks for a face quietly did nothing:
# no error, no message, just a picture that never changed. So the search
# covers every place the files are actually kept, ends with a copy shipped
# alongside this file, and if it somehow still comes up empty the effects say
# so rather than pretending to work.

BUNDLED_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


def _cascade_directories() -> List[str]:
    places = []
    override = os.environ.get("CAMALOOP_CASCADES")
    if override:
        places.append(override)
    try:
        packaged = getattr(cv2.data, "haarcascades", "")
        if packaged:
            places.append(packaged)
    except Exception:
        pass
    places += [
        "/usr/share/opencv4/haarcascades",
        "/usr/share/opencv/haarcascades",
        "/usr/share/OpenCV/haarcascades",
        "/usr/local/share/opencv4/haarcascades",
        "/usr/local/share/opencv/haarcascades",
        "/usr/share/opencv4/lbpcascades",
    ]
    try:
        beside_cv2 = os.path.join(os.path.dirname(cv2.__file__), "data")
        places.append(beside_cv2)
    except Exception:
        pass
    places.append(BUNDLED_DATA)
    return places


def cascade_file(name: str) -> str:
    """The full path to one of OpenCV's cascade files, or "" if it is lost."""
    for directory in _cascade_directories():
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    return ""


FACE_CASCADE = "haarcascade_frontalface_default.xml"
EYE_CASCADE = "haarcascade_eye.xml"


def cascades_supported() -> bool:
    """Whether this OpenCV can still run the detector the app uses.

    OpenCV 5.0 dropped CascadeClassifier and stopped shipping the detection
    data with the wheel. This has to be asked separately from whether the
    data file is on disk, because Camaloop carries its own copy: the file is
    always there, so its presence on its own would look like working face
    detection right up until no face was ever found.
    """
    return hasattr(cv2, "CascadeClassifier")


def load_cascade(name: str):
    """A ready classifier, or None when the data cannot be found or read."""
    if not cascades_supported():
        return None
    path = cascade_file(name)
    if not path:
        return None
    try:
        classifier = cv2.CascadeClassifier(path)
    except Exception:
        return None
    return None if classifier.empty() else classifier


def face_detection_ready():
    """(usable, what to do about it) - for an effect's availability().

    Whenever this says no it also says why, in a form someone can act on.
    An empty reason would reach the activity log as a blank line and leave
    the effect looking simply broken.
    """
    if not cascades_supported():
        return False, (
            "This version of OpenCV cannot find faces. Version 5 removed the\n"
            "face finder Camaloop uses, and version 4 is the one to have.\n"
            "With pip:             pip install \"opencv-python<5\"\n"
            "On Debian or Ubuntu:  sudo apt install python3-opencv\n"
            "Your distribution's own package is still version 4."
        )
    if cascade_file(FACE_CASCADE):
        return True, ""
    return False, (
        "OpenCV's face detection data is missing, so no face can be found.\n"
        "On Debian or Ubuntu:  sudo apt install opencv-data\n"
        "On Fedora:            sudo dnf install opencv-data\n"
        "Or with pip:          pip install --force-reinstall opencv-python\n"
        "Re-running the app's setup.sh installs it too."
    )


@dataclass
class Face:
    """Where a face is, in the frame."""

    centre: Tuple[float, float]      # between the eyes
    width: float                     # of the face, in pixels
    angle: float                     # head tilt, degrees, clockwise positive
    box: Tuple[int, int, int, int] = (0, 0, 0, 0)
    landmarks: Optional[np.ndarray] = None    # dense mesh, when available

    @property
    def area(self) -> float:
        return self.width * self.width


def _smooth(previous, current, factor):
    """Ease between two numbers so tracking does not jitter frame to frame."""
    if previous is None:
        return current
    return previous + (current - previous) * factor


class FaceTracker:
    """Finds faces, and keeps them steady between detections."""

    def __init__(self):
        self._cascade = None
        self._eyes = None
        self._mesh = None
        self._mesh_state = "unknown"     # unknown | ready | unavailable
        self._tracked: List[Face] = []
        self._raw: List[Face] = []

    # -- the rough tracker, always available ---------------------------

    def _cascades(self):
        if self._cascade is None:
            self._cascade = load_cascade(FACE_CASCADE)
            self._eyes = load_cascade(EYE_CASCADE)
        return self._cascade, self._eyes

    def _detect_haar(self, frame) -> List[Face]:
        faces, eyes = self._cascades()
        if faces is None:
            return []
        height, width = frame.shape[:2]
        if height < 24 or width < 24:
            # Halving a frame this small leaves nothing to look at, and
            # cv2.resize refuses an empty size outright.
            return []
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Detection runs at half size for speed, so every box found comes
        # back doubled.
        scale = 2 if min(height, width) >= 120 else 1
        small = grey if scale == 1 else cv2.resize(
            grey, (width // 2, height // 2), interpolation=cv2.INTER_AREA)
        found = faces.detectMultiScale(small, 1.2, 5, minSize=(24, 24))

        results = []
        for (x, y, w, h) in found:
            x, y, w, h = x * scale, y * scale, w * scale, h * scale
            centre = (x + w / 2.0, y + h * 0.42)
            angle = 0.0

            # Eyes, when they can be found, give the tilt and a better centre.
            upper = grey[y:y + int(h * 0.6), x:x + w]
            if upper.size and eyes is not None:
                spotted = eyes.detectMultiScale(upper, 1.15, 6, minSize=(14, 14))
                if len(spotted) >= 2:
                    spotted = sorted(spotted, key=lambda e: -e[2] * e[3])[:2]
                    points = [
                        (x + ex + ew / 2.0, y + ey + eh / 2.0)
                        for (ex, ey, ew, eh) in spotted
                    ]
                    points.sort(key=lambda p: p[0])
                    (lx, ly), (rx, ry) = points
                    centre = ((lx + rx) / 2.0, (ly + ry) / 2.0)
                    angle = math.degrees(math.atan2(ry - ly, rx - lx))
                    if abs(angle) > 35:          # almost certainly a mis-detection
                        angle = 0.0
            results.append(Face(centre=centre, width=float(w), angle=angle,
                                box=(x, y, w, h)))
        return results

    # -- the precise tracker, when mediapipe is installed ---------------

    def _mesh_tracker(self):
        if self._mesh_state == "unavailable":
            return None
        if self._mesh is not None:
            return self._mesh
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            model = self._model_path()
            if not model:
                self._mesh_state = "unavailable"
                return None
            options = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=4,
            )
            self._mesh = vision.FaceLandmarker.create_from_options(options)
            self._mesh_state = "ready"
            return self._mesh
        except Exception:
            self._mesh_state = "unavailable"
            return None

    @staticmethod
    def _model_path() -> str:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        candidates = [
            os.path.join(base, "camaloop", "face_landmarker.task"),
            "/usr/share/camaloop/face_landmarker.task",
        ]
        return next((p for p in candidates if os.path.isfile(p)), "")

    def _detect_mesh(self, frame, timestamp_ms) -> Optional[List[Face]]:
        tracker = self._mesh_tracker()
        if tracker is None:
            return None
        try:
            import mediapipe as mp

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB,
                             data=np.ascontiguousarray(rgb))
            result = tracker.detect_for_video(image, int(timestamp_ms))
        except Exception:
            self._mesh_state = "unavailable"
            return None

        h, w = frame.shape[:2]
        faces = []
        for marks in getattr(result, "face_landmarks", []) or []:
            points = np.array([[m.x * w, m.y * h] for m in marks], dtype=np.float32)
            # 33 and 263 are the outer eye corners in the standard mesh.
            left, right = points[33], points[263]
            centre = ((left[0] + right[0]) / 2.0, (left[1] + right[1]) / 2.0)
            angle = math.degrees(math.atan2(right[1] - left[1], right[0] - left[0]))
            span = float(np.linalg.norm(right - left))
            width = span / EYE_GAP_TO_FACE * 0.62
            x0, y0 = points.min(axis=0)
            x1, y1 = points.max(axis=0)
            faces.append(Face(
                centre=centre, width=width, angle=angle,
                box=(int(x0), int(y0), int(x1 - x0), int(y1 - y0)),
                landmarks=points,
            ))
        return faces

    # -- what callers use ----------------------------------------------

    def detect(self, frame, frame_index: int, every: int = 3,
               smoothing: float = 0.45) -> List[Face]:
        if frame_index % max(1, every) == 0:
            found = self._detect_mesh(frame, frame_index * 33.0)
            if found is None:
                found = self._detect_haar(frame)
            self._raw = found

        # Ease the tracked faces towards the newly found ones.
        tracked = []
        for face in self._raw:
            match = None
            for previous in self._tracked:
                distance = math.hypot(previous.centre[0] - face.centre[0],
                                      previous.centre[1] - face.centre[1])
                if distance < max(40.0, face.width * 0.6):
                    match = previous
                    break
            if match is None:
                tracked.append(face)
                continue
            tracked.append(Face(
                centre=(_smooth(match.centre[0], face.centre[0], smoothing),
                        _smooth(match.centre[1], face.centre[1], smoothing)),
                width=_smooth(match.width, face.width, smoothing),
                angle=_smooth(match.angle, face.angle, smoothing),
                box=face.box,
                landmarks=face.landmarks,
            ))
        self._tracked = tracked
        return tracked
