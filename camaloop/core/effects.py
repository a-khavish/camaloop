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

"""The effect library and the pipeline that runs it.

Every effect takes a BGR uint8 frame (OpenCV's native layout) and returns one.
Effects are applied in the order they appear in ``EFFECTS``: geometry, then
background work, then colour, then stylising, then anything drawn on top.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np


# --------------------------------------------------------------------------
# Parameter description
# --------------------------------------------------------------------------


@dataclass
class Param:
    key: str
    label: str
    kind: str  # float | int | bool | choice | color | file | text
    default: Any
    minimum: float = 0.0
    maximum: float = 1.0
    step: float = 0.01
    choices: Sequence[str] = field(default_factory=tuple)
    suffix: str = ""
    hint: str = ""
    filters: str = ""


class Effect:
    id = ""
    name = ""
    group = ""
    blurb = ""
    params: Sequence[Param] = ()

    def defaults(self) -> Dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def availability(self) -> Tuple[bool, str]:
        """(usable, reason shown in the interface when not)."""
        return True, ""

    def apply(self, frame: np.ndarray, p: Dict[str, Any], ctx: Dict[str, Any]) -> np.ndarray:
        return frame


def _blend(base: np.ndarray, other: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """base where the mask is 0, other where it is 255, mixed in between.

    Done with OpenCV rather than numpy: at 1080p the difference between the
    two is the difference between a smooth preview and a stuttering one.
    """
    if mask.ndim == 2:
        mask = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    inverse = cv2.bitwise_not(mask)
    return cv2.add(cv2.multiply(base, inverse, scale=1 / 255.0),
                   cv2.multiply(other, mask, scale=1 / 255.0))


def _screen(base: np.ndarray, light: np.ndarray) -> np.ndarray:
    """Screen blending, which is how light actually adds up."""
    return cv2.bitwise_not(
        cv2.multiply(cv2.bitwise_not(base), cv2.bitwise_not(light),
                     scale=1 / 255.0)
    )


def _flat(frame: np.ndarray, colour) -> np.ndarray:
    out = np.empty_like(frame)
    out[:] = colour
    return out


def _odd(value: int) -> int:
    value = int(value)
    return value + 1 if value % 2 == 0 else value


def _hex_to_bgr(value: str) -> Tuple[int, int, int]:
    value = (value or "#000000").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        r = g = b = 0
    return b, g, r


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


class Framing(Effect):
    id = "framing"
    name = "Framing"
    group = "Camera"
    blurb = "Mirror, flip and rotate the incoming picture."
    params = (
        Param("mirror", "Mirror horizontally", "bool", True),
        Param("flip", "Flip vertically", "bool", False),
        Param("rotate", "Rotate", "choice", "0°", choices=("0°", "90°", "180°", "270°")),
        Param("angle", "Fine angle", "float", 0.0, -45.0, 45.0, 0.5, suffix="°",
              hint="Straighten a tilted camera, or tilt it deliberately."),
        Param("fill", "Corners", "choice", "Zoom to fill",
              choices=("Zoom to fill", "Leave empty", "Stretch edges")),
    )

    def apply(self, frame, p, ctx):
        if p["mirror"]:
            frame = cv2.flip(frame, 1)
        if p["flip"]:
            frame = cv2.flip(frame, 0)
        rotate = p["rotate"]
        if rotate == "90°":
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif rotate == "180°":
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif rotate == "270°":
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        angle = float(p.get("angle", 0.0))
        if abs(angle) > 0.05:
            frame = self._rotate_free(frame, angle, p.get("fill", "Zoom to fill"))
        return frame

    @staticmethod
    def _rotate_free(frame, angle, fill):
        """Rotate by any angle. Either keep the frame size and zoom in far
        enough to hide the corners, or letterbox them."""
        h, w = frame.shape[:2]
        centre = (w / 2, h / 2)

        scale = 1.0
        if fill == "Zoom to fill":
            # Smallest zoom that keeps the rotated rectangle covering the frame.
            radians = np.deg2rad(abs(angle) % 180)
            cos, sin = abs(np.cos(radians)), abs(np.sin(radians))
            if w * h > 0:
                scale = max(
                    (w * cos + h * sin) / w,
                    (w * sin + h * cos) / h,
                )
        matrix = cv2.getRotationMatrix2D(centre, angle, scale)
        border = cv2.BORDER_REPLICATE if fill == "Stretch edges" else cv2.BORDER_CONSTANT
        return cv2.warpAffine(
            frame, matrix, (w, h), flags=cv2.INTER_LINEAR,
            borderMode=border, borderValue=(16, 18, 23),
        )


class ZoomPan(Effect):
    id = "zoom"
    name = "Zoom and reframe"
    group = "Camera"
    blurb = "Crop into the sensor to tighten the shot."
    params = (
        Param("zoom", "Zoom", "float", 1.2, 1.0, 4.0, 0.05, suffix="x"),
        Param("pan_x", "Move sideways", "float", 0.0, -1.0, 1.0, 0.02),
        Param("pan_y", "Move up and down", "float", 0.0, -1.0, 1.0, 0.02),
    )

    def apply(self, frame, p, ctx):
        zoom = max(1.0, float(p["zoom"]))
        if zoom <= 1.001:
            return frame
        h, w = frame.shape[:2]
        cw, ch = int(w / zoom), int(h / zoom)
        max_x, max_y = w - cw, h - ch
        x = int((max_x / 2) * (1 + float(p["pan_x"])))
        y = int((max_y / 2) * (1 + float(p["pan_y"])))
        x = max(0, min(max_x, x))
        y = max(0, min(max_y, y))
        crop = frame[y : y + ch, x : x + cw]
        return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)


class Crop(Effect):
    id = "crop"
    name = "Crop"
    group = "Camera"
    blurb = "Trim the edges, or cut the picture to a shape like 1:1 or 9:16."
    params = (
        Param(
            "aspect", "Shape", "choice", "Free",
            choices=("Free", "16:9", "4:3", "1:1", "9:16 (phone)", "21:9"),
        ),
        Param("left", "Trim left", "float", 0.0, 0.0, 0.45, 0.01),
        Param("right", "Trim right", "float", 0.0, 0.0, 0.45, 0.01),
        Param("top", "Trim top", "float", 0.0, 0.0, 0.45, 0.01),
        Param("bottom", "Trim bottom", "float", 0.0, 0.0, 0.45, 0.01),
        Param(
            "after", "Then", "choice", "Stretch back to full size",
            choices=("Stretch back to full size", "Keep the cropped size",
                     "Pad with bars"),
        ),
        Param("pad_colour", "Bar colour", "color", "#101318"),
    )

    _RATIOS = {"16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0,
               "9:16 (phone)": 9 / 16, "21:9": 21 / 9}

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]

        x0 = int(w * float(p["left"]))
        x1 = w - int(w * float(p["right"]))
        y0 = int(h * float(p["top"]))
        y1 = h - int(h * float(p["bottom"]))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return frame

        ratio = self._RATIOS.get(p["aspect"])
        if ratio:
            # Take the largest window of that shape inside what is left.
            cw, ch = x1 - x0, y1 - y0
            if cw / ch > ratio:
                new_w = int(ch * ratio)
                x0 += (cw - new_w) // 2
                x1 = x0 + new_w
            else:
                new_h = int(cw / ratio)
                y0 += (ch - new_h) // 2
                y1 = y0 + new_h

        cropped = frame[y0:y1, x0:x1]
        if cropped.size == 0:
            return frame

        after = p["after"]
        if after == "Keep the cropped size":
            return np.ascontiguousarray(cropped)
        if after == "Stretch back to full size":
            return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

        # Pad with bars: fit the crop inside the original frame, centred.
        ch, cw = cropped.shape[:2]
        scale = min(w / cw, h / ch)
        fitted = cv2.resize(
            cropped, (max(1, int(cw * scale)), max(1, int(ch * scale))),
            interpolation=cv2.INTER_LINEAR,
        )
        canvas = np.full_like(frame, _hex_to_bgr(p["pad_colour"]))
        fh, fw = fitted.shape[:2]
        ox, oy = (w - fw) // 2, (h - fh) // 2
        canvas[oy:oy + fh, ox:ox + fw] = fitted
        return canvas


# --------------------------------------------------------------------------
# Background
# --------------------------------------------------------------------------


class _BackgroundMixin:
    _bg_cache: Dict[str, np.ndarray] = {}

    def background_for(self, frame, mode, colour, image_path, blur_amount):
        h, w = frame.shape[:2]
        if mode == "Blur the real background":
            return self._fast_blur(frame, int(blur_amount))
        if mode == "Image":
            img = self._load_image(image_path)
            if img is None:
                return np.zeros_like(frame)
            return self._cover(img, w, h)
        b, g, r = _hex_to_bgr(colour)
        # A flat backdrop does not change frame to frame, so it is filled
        # once and handed back until the size or the colour does change.
        key = (frame.shape, b, g, r)
        if getattr(self, "_flat_key", None) != key:
            self._flat_colour = _flat(frame, (b, g, r))
            self._flat_key = key
        return self._flat_colour

    @staticmethod
    def _fast_blur(frame: np.ndarray, amount: int) -> np.ndarray:
        """A heavy blur costs the same as a light one if it is done small."""
        h, w = frame.shape[:2]
        amount = max(3, int(amount))
        factor = 4 if amount >= 20 else 2
        small = cv2.resize(
            frame, (max(8, w // factor), max(8, h // factor)), interpolation=cv2.INTER_AREA
        )
        k = _odd(max(3, amount // factor))
        small = cv2.GaussianBlur(small, (k, k), 0)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)

    def _load_image(self, path) -> Optional[np.ndarray]:
        if not path or not os.path.isfile(path):
            return None
        cached = self._bg_cache.get(path)
        if cached is None:
            cached = cv2.imread(path, cv2.IMREAD_COLOR)
            if cached is None:
                return None
            self._bg_cache[path] = cached
        return cached

    @staticmethod
    def _cover(img, w, h):
        ih, iw = img.shape[:2]
        scale = max(w / iw, h / ih)
        resized = cv2.resize(img, (int(iw * scale) + 1, int(ih * scale) + 1))
        y = (resized.shape[0] - h) // 2
        x = (resized.shape[1] - w) // 2
        return resized[y : y + h, x : x + w]


class ChromaKey(Effect, _BackgroundMixin):
    id = "chroma"
    name = "Green screen"
    group = "Background"
    blurb = "Key out a backdrop colour and put something else behind you."
    params = (
        Param("key", "Colour to remove", "color", "#00B140"),
        Param("tolerance", "Tolerance", "int", 30, 1, 90, 1),
        Param("softness", "Edge softness", "int", 5, 0, 40, 1),
        Param("spill", "Remove colour spill", "float", 0.5, 0.0, 1.0, 0.05),
        Param(
            "mode",
            "Replace with",
            "choice",
            "Colour",
            choices=("Colour", "Image", "Blur the real background"),
        ),
        Param("colour", "Backdrop colour", "color", "#101318"),
        Param("image", "Backdrop image", "file", ""),
        Param("blur", "Backdrop blur", "int", 45, 3, 99, 2),
    )

    def apply(self, frame, p, ctx):
        key_b, key_g, key_r = _hex_to_bgr(p["key"])
        key_hsv = cv2.cvtColor(
            np.uint8([[[key_b, key_g, key_r]]]), cv2.COLOR_BGR2HSV
        )[0][0]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        tol = int(p["tolerance"])
        lower = np.array(
            [max(0, int(key_hsv[0]) - tol // 2), max(40, 255 - tol * 6), max(40, 255 - tol * 6)],
            dtype=np.uint8,
        )
        upper = np.array(
            [min(179, int(key_hsv[0]) + tol // 2), 255, 255], dtype=np.uint8
        )
        mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        soft = int(p["softness"])
        if soft > 0:
            mask = cv2.GaussianBlur(mask, (_odd(soft * 2 + 1), _odd(soft * 2 + 1)), 0)

        subject = frame
        spill = float(p["spill"])
        if spill > 0.01:
            # Green spilling onto the subject is pulled back towards
            # whichever of the other two channels is stronger. Only green
            # changes, so the frame is copied once and that one channel is
            # written back into it.
            blue, green, red = cv2.split(frame)
            pulled = cv2.min(green, cv2.max(blue, red))
            green = cv2.addWeighted(green, 1 - spill, pulled, spill, 0)
            subject = frame.copy()
            cv2.insertChannel(green, subject, 1)

        bg = self.background_for(frame, p["mode"], p["colour"], p["image"], p["blur"])

        # With a hard edge the backdrop can simply be stamped through the
        # mask, which is one pass instead of three. A soft edge has to be
        # mixed, and _blend does that with OpenCV rather than in float NumPy.
        if soft <= 0:
            out = subject if subject is not frame else frame.copy()
            cv2.copyTo(bg, mask, out)
            return out
        return _blend(subject, bg, mask)


class PortraitBackground(Effect, _BackgroundMixin):
    id = "portrait"
    name = "Background without a green screen"
    group = "Background"
    blurb = "Separates you from the room using on-device segmentation."
    params = (
        Param(
            "mode",
            "Replace with",
            "choice",
            "Blur the real background",
            choices=("Blur the real background", "Colour", "Image"),
        ),
        Param("blur", "Background blur", "int", 45, 3, 99, 2),
        Param("colour", "Backdrop colour", "color", "#101318"),
        Param("image", "Backdrop image", "file", ""),
        Param("edge", "Edge softness", "int", 7, 1, 35, 2),
        Param("threshold", "Cut-off", "float", 0.55, 0.05, 0.95, 0.05),
        Param(
            "invert",
            "Swap what is kept",
            "bool",
            False,
            hint="Use this if the room stays and you disappear.",
        ),
        Param(
            "model", "Model file", "file", "",
            filters="Segmentation model (*.tflite);;All files (*)",
            hint="Only needed if the app cannot download it itself.",
        ),
    )

    MODEL_URL = (
        "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
        "selfie_segmenter/float16/latest/selfie_segmenter.tflite"
    )

    def __init__(self):
        self._segmenter = None
        self._backend = None  # "solutions" | "tasks"
        self._checked = False
        self._reason = ""
        self._model_state = "unknown"  # unknown | fetching | ready | failed
        self._model_path = ""
        self._supplied_model = ""
        self._announced = False

    # -- backend discovery -------------------------------------------------

    def availability(self):
        if self._checked:
            return (not self._reason), self._reason
        self._checked = True
        try:
            import mediapipe as mp
        except Exception:
            # sys.executable, not a bare "pip": the app may be running from a
            # virtual environment of its own, and a plain pip would put
            # mediapipe somewhere this app never looks.
            self._reason = (
                "Needs mediapipe. Install it with:\n"
                f"{sys.executable} -m pip install mediapipe"
            )
            return False, self._reason

        # mediapipe 0.10.x still ships the old Solutions API in some builds and
        # only the newer Tasks API in others, so handle whichever is present.
        if hasattr(mp, "solutions") and hasattr(mp.solutions, "selfie_segmentation"):
            self._backend = "solutions"
        elif hasattr(mp, "tasks"):
            self._backend = "tasks"
        else:
            self._reason = "This build of mediapipe has no segmentation support."
            return False, self._reason
        return True, ""

    def _cache_dir(self) -> str:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        path = os.path.join(base, "camaloop")
        os.makedirs(path, exist_ok=True)
        return path

    _SYSTEM_MODEL_PATHS = (
        "/usr/share/mediapipe/selfie_segmenter.tflite",
        "/usr/local/share/mediapipe/selfie_segmenter.tflite",
    )

    def _ensure_model(self, ctx, supplied: str = "") -> bool:
        """Find the ~250 kB segmentation model, or fetch it once."""
        if supplied and os.path.isfile(supplied):
            self._model_path = supplied
            self._model_state = "ready"
            return True
        if self._model_state == "ready":
            return True
        if self._model_state == "fetching":
            return False
        if self._model_state == "failed":
            if not self._announced:
                self._announced = True
                ctx.setdefault("errors", []).append(
                    "Background segmentation needs a model file. Save it yourself to "
                    f"{self._model_path} from {self.MODEL_URL}"
                )
            return False

        path = os.path.join(self._cache_dir(), "selfie_segmenter.tflite")
        self._model_path = path
        if os.path.isfile(path) and os.path.getsize(path) > 1024:
            self._model_state = "ready"
            return True
        for candidate in self._SYSTEM_MODEL_PATHS:
            if os.path.isfile(candidate):
                self._model_path = candidate
                self._model_state = "ready"
                return True

        self._model_state = "fetching"
        ctx.setdefault("errors", []).append(
            "Fetching the background segmentation model (about 250 kB), one time only..."
        )

        def download():
            import urllib.request

            try:
                temporary = path + ".part"
                with urllib.request.urlopen(self.MODEL_URL, timeout=30) as response:
                    data = response.read()
                with open(temporary, "wb") as fh:
                    fh.write(data)
                os.replace(temporary, path)
                self._model_state = "ready"
            except Exception:
                self._model_state = "failed"

        threading.Thread(target=download, daemon=True).start()
        return False

    def _get(self, ctx):
        if self._segmenter is not None:
            return self._segmenter
        import mediapipe as mp

        if self._backend == "solutions":
            self._segmenter = mp.solutions.selfie_segmentation.SelfieSegmentation(
                model_selection=1
            )
            return self._segmenter

        if not self._ensure_model(ctx, self._supplied_model):
            return None
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        options = vision.ImageSegmenterOptions(
            base_options=mp_python.BaseOptions(model_asset_path=self._model_path),
            running_mode=vision.RunningMode.VIDEO,
            output_confidence_masks=True,
        )
        self._segmenter = vision.ImageSegmenter.create_from_options(options)
        return self._segmenter

    # -- processing --------------------------------------------------------

    def _mask_for(self, frame, ctx, supplied=""):
        self._supplied_model = supplied
        import mediapipe as mp

        segmenter = self._get(ctx)
        if segmenter is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if self._backend == "solutions":
            return segmenter.process(rgb).segmentation_mask

        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        timestamp = int(ctx.get("frame_index", 0) * (1000 / 30)) + 1
        result = segmenter.segment_for_video(image, timestamp)
        masks = result.confidence_masks
        if not masks:
            return None
        # Two masks means background first, person second; one mask is the person.
        chosen = masks[1] if len(masks) > 1 else masks[0]
        return np.array(chosen.numpy_view(), copy=True)

    def apply(self, frame, p, ctx):
        ok, _ = self.availability()
        if not ok:
            return frame
        try:
            mask = self._mask_for(frame, ctx, p.get("model", ""))
        except Exception as exc:
            if not self._announced:
                self._announced = True
                ctx.setdefault("errors", []).append(f"Segmentation unavailable: {exc}")
            return frame
        if mask is None:
            return frame
        if mask.shape[:2] != frame.shape[:2]:
            mask = cv2.resize(mask, (frame.shape[1], frame.shape[0]))
        if p.get("invert"):
            mask = 1.0 - mask
        mask = np.clip((mask - float(p["threshold"])) * 6 + 0.5, 0, 1).astype(np.float32)
        k = _odd(int(p["edge"]))
        mask = cv2.GaussianBlur(mask, (k, k), 0)[:, :, None]
        bg = self.background_for(frame, p["mode"], p["colour"], p["image"], p["blur"])
        out = frame.astype(np.float32) * mask + bg.astype(np.float32) * (1 - mask)
        return np.clip(out, 0, 255).astype(np.uint8)


class FacePrivacy(Effect):
    id = "faceprivacy"
    name = "Hide faces"
    group = "Background"
    blurb = "Finds faces and covers them. Useful when other people walk through frame."
    params = (
        Param(
            "who", "Cover", "choice", "Everyone",
            choices=("Everyone", "Everyone except me", "Only me"),
            hint="\"Me\" is taken to be the largest face, which is normally "
                 "whoever is closest to the camera.",
        ),
        Param("style", "Cover with", "choice", "Blur", choices=("Blur", "Pixels", "Block")),
        Param("strength", "Strength", "int", 25, 5, 80, 1),
        Param("padding", "Area around the face", "float", 0.15, 0.0, 0.8, 0.05),
        Param("every", "Detect every N frames", "int", 3, 1, 15, 1),
    )

    def __init__(self):
        self._cascade = None
        self._looked = False
        self._boxes: List[Tuple[int, int, int, int]] = []

    def availability(self):
        from .faces import face_detection_ready

        return face_detection_ready()

    def _detector(self):
        if not self._looked:
            from .faces import FACE_CASCADE, load_cascade

            self._cascade = load_cascade(FACE_CASCADE)
            self._looked = True
        return self._cascade

    def apply(self, frame, p, ctx):
        det = self._detector()
        if det is None:
            # Nothing to detect with. Say so rather than handing back an
            # unchanged picture and leaving it looking as though the effect
            # simply does not work.
            ctx.setdefault("errors", []).append(self.availability()[1])
            return frame
        height, width = frame.shape[:2]
        if height < 24 or width < 24:
            return frame
        every = max(1, int(p["every"]))
        if ctx.get("frame_index", 0) % every == 0:
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            scale = 2 if min(height, width) >= 120 else 1
            small = grey if scale == 1 else cv2.resize(
                grey, (width // 2, height // 2), interpolation=cv2.INTER_AREA)
            found = det.detectMultiScale(small, 1.2, 5, minSize=(24, 24))
            self._boxes = [
                (x * scale, y * scale, w * scale, h * scale)
                for (x, y, w, h) in found
            ]

        boxes = list(self._boxes)
        who = p.get("who", "Everyone")
        if boxes and who != "Everyone":
            # The nearest person has the biggest face in frame.
            mine = max(boxes, key=lambda b: b[2] * b[3])
            if who == "Only me":
                boxes = [mine]
            else:
                boxes = [b for b in boxes if b is not mine]

        pad = float(p["padding"])
        strength = int(p["strength"])
        h, w = frame.shape[:2]
        for (x, y, bw, bh) in boxes:
            px, py = int(bw * pad), int(bh * pad)
            x0, y0 = max(0, x - px), max(0, y - py)
            x1, y1 = min(w, x + bw + px), min(h, y + bh + py)
            if x1 <= x0 or y1 <= y0:
                continue
            roi = frame[y0:y1, x0:x1]
            if p["style"] == "Blur":
                k = _odd(strength * 2 + 1)
                frame[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (k, k), 0)
            elif p["style"] == "Pixels":
                size = max(2, (x1 - x0) // max(2, strength // 2))
                small = cv2.resize(roi, (size, size), interpolation=cv2.INTER_LINEAR)
                frame[y0:y1, x0:x1] = cv2.resize(
                    small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST
                )
            else:
                frame[y0:y1, x0:x1] = (18, 20, 25)
        return frame


class CoverRegion(Effect):
    id = "region"
    name = "Cover an area"
    group = "Background"
    blurb = (
        "Hide a fixed part of the picture - a doorway, a whiteboard, a window "
        "behind you. Set it once and it stays put."
    )
    params = (
        Param("left", "Left edge", "float", 0.6, 0.0, 1.0, 0.01),
        Param("top", "Top edge", "float", 0.0, 0.0, 1.0, 0.01),
        Param("width", "Width", "float", 0.4, 0.02, 1.0, 0.01),
        Param("height", "Height", "float", 0.5, 0.02, 1.0, 0.01),
        Param(
            "style", "Cover with", "choice", "Blur",
            choices=("Blur", "Pixels", "Solid colour"),
        ),
        Param("strength", "Strength", "int", 30, 5, 90, 1),
        Param("colour", "Colour", "color", "#101318"),
        Param("feather", "Soften the edges", "int", 0, 0, 60, 2),
        Param("invert", "Cover everything except this", "bool", False),
    )

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        x0 = int(w * float(p["left"]))
        y0 = int(h * float(p["top"]))
        x1 = min(w, x0 + int(w * float(p["width"])))
        y1 = min(h, y0 + int(h * float(p["height"])))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return frame

        covered = self._cover(frame, p)

        mask = np.zeros((h, w), dtype=np.uint8)
        mask[y0:y1, x0:x1] = 255
        if p["invert"]:
            mask = cv2.bitwise_not(mask)

        feather = int(p["feather"])
        if feather:
            k = _odd(feather * 2 + 1)
            mask = cv2.GaussianBlur(mask, (k, k), 0)

        mask3 = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        inverse = cv2.bitwise_not(mask3)
        return cv2.add(
            cv2.multiply(frame, inverse, scale=1 / 255.0),
            cv2.multiply(covered, mask3, scale=1 / 255.0),
        )

    @staticmethod
    def _cover(frame, p):
        style = p["style"]
        strength = int(p["strength"])
        if style == "Solid colour":
            return np.full_like(frame, _hex_to_bgr(p["colour"]))
        h, w = frame.shape[:2]
        if style == "Pixels":
            block = max(2, strength // 3)
            small = cv2.resize(
                frame, (max(1, w // block), max(1, h // block)),
                interpolation=cv2.INTER_LINEAR,
            )
            return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        factor = 4 if strength >= 30 else 2
        small = cv2.resize(
            frame, (max(8, w // factor), max(8, h // factor)), interpolation=cv2.INTER_AREA
        )
        k = _odd(max(3, strength // factor))
        return cv2.resize(
            cv2.GaussianBlur(small, (k, k), 0), (w, h), interpolation=cv2.INTER_LINEAR
        )


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------


class Exposure(Effect):
    id = "exposure"
    name = "Exposure"
    group = "Colour"
    blurb = "Lift a dark room or tame a bright window."
    params = (
        Param("brightness", "Brightness", "int", 0, -100, 100, 1),
        Param("contrast", "Contrast", "float", 1.0, 0.3, 2.5, 0.05),
        Param("gamma", "Shadow lift", "float", 1.0, 0.3, 2.5, 0.05),
    )

    def __init__(self):
        self._lut_gamma = None
        self._lut = None

    def apply(self, frame, p, ctx):
        frame = cv2.convertScaleAbs(
            frame, alpha=float(p["contrast"]), beta=float(p["brightness"])
        )
        gamma = float(p["gamma"])
        if abs(gamma - 1.0) > 0.01:
            if self._lut_gamma != gamma:
                inv = 1.0 / max(0.05, gamma)
                self._lut = np.array(
                    [((i / 255.0) ** inv) * 255 for i in range(256)], dtype=np.uint8
                )
                self._lut_gamma = gamma
            frame = cv2.LUT(frame, self._lut)
        return frame


class ColourBalance(Effect):
    id = "colour"
    name = "Colour"
    group = "Colour"
    blurb = "Saturation, hue and how warm the picture looks."
    params = (
        Param("saturation", "Saturation", "float", 1.0, 0.0, 2.5, 0.05),
        Param("hue", "Hue shift", "int", 0, -90, 90, 1, suffix="°"),
        Param("temperature", "Warmth", "int", 0, -60, 60, 1),
    )

    def apply(self, frame, p, ctx):
        sat = float(p["saturation"])
        hue = int(p["hue"])
        if abs(sat - 1.0) > 0.01 or hue != 0:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.int16)
            if hue:
                hsv[:, :, 0] = (hsv[:, :, 0] + hue // 2) % 180
            if abs(sat - 1.0) > 0.01:
                hsv[:, :, 1] = np.clip(hsv[:, :, 1] * sat, 0, 255)
            frame = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        temp = int(p["temperature"])
        if temp:
            b, g, r = cv2.split(frame.astype(np.int16))
            r = np.clip(r + temp, 0, 255)
            b = np.clip(b - temp, 0, 255)
            frame = cv2.merge([b, g, r]).astype(np.uint8)
        return frame


class Look(Effect):
    id = "look"
    name = "Look"
    group = "Colour"
    blurb = "A single graded look applied over everything else."
    params = (
        Param(
            "style",
            "Style",
            "choice",
            "Black and white",
            choices=(
                "Black and white",
                "Sepia",
                "Inverted",
                "Posterised",
                "Thermal",
                "Night vision",
                "High contrast",
            ),
        ),
        Param("amount", "Amount", "float", 1.0, 0.0, 1.0, 0.05),
        Param("levels", "Posterise steps", "int", 5, 2, 16, 1),
    )

    _SEPIA = np.array(
        [[0.272, 0.534, 0.131], [0.349, 0.686, 0.168], [0.393, 0.769, 0.189]]
    )

    def apply(self, frame, p, ctx):
        style = p["style"]
        if style == "Black and white":
            out = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        elif style == "Sepia":
            out = cv2.transform(frame, self._SEPIA)
            out = np.clip(out, 0, 255).astype(np.uint8)
        elif style == "Inverted":
            out = cv2.bitwise_not(frame)
        elif style == "Posterised":
            steps = max(2, int(p["levels"]))
            size = 256 // steps
            out = (frame // size) * size
        elif style == "Thermal":
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            out = cv2.applyColorMap(grey, cv2.COLORMAP_INFERNO)
        elif style == "Night vision":
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            out = cv2.merge(
                [np.zeros_like(grey), cv2.equalizeHist(grey), np.zeros_like(grey)]
            )
        else:  # High contrast
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            out = cv2.cvtColor(cv2.merge([clahe.apply(l), a, b]), cv2.COLOR_LAB2BGR)

        amount = float(p["amount"])
        if amount >= 0.99:
            return out
        return cv2.addWeighted(out, amount, frame, 1 - amount, 0)


# --------------------------------------------------------------------------
# Detail and stylising
# --------------------------------------------------------------------------


class Detail(Effect):
    id = "detail"
    name = "Softness and detail"
    group = "Texture"
    blurb = "Soften skin or bring back edge definition."
    params = (
        Param("smooth", "Smooth skin", "int", 0, 0, 30, 1),
        Param("blur", "Soften whole picture", "int", 0, 0, 40, 1),
        Param("sharpen", "Sharpen", "float", 0.0, 0.0, 2.0, 0.05),
    )

    def apply(self, frame, p, ctx):
        smooth = int(p["smooth"])
        if smooth > 0:
            h, w = frame.shape[:2]
            small = cv2.resize(frame, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
            small = cv2.bilateralFilter(small, 7, smooth * 5, smooth * 5)
            softened = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
            # Keep some real detail so faces do not turn to plastic.
            frame = cv2.addWeighted(softened, 0.85, frame, 0.15, 0)
        blur = int(p["blur"])
        if blur > 0:
            k = _odd(blur * 2 + 1)
            frame = cv2.GaussianBlur(frame, (k, k), 0)
        amount = float(p["sharpen"])
        if amount > 0.01:
            soft = cv2.GaussianBlur(frame, (0, 0), 3)
            frame = cv2.addWeighted(frame, 1 + amount, soft, -amount, 0)
        return frame


class Stylise(Effect):
    id = "stylise"
    name = "Stylise"
    group = "Texture"
    blurb = "Turn the feed into a drawing, a cartoon or a mosaic."
    params = (
        Param(
            "style",
            "Style",
            "choice",
            "Cartoon",
            choices=("Cartoon", "Pencil sketch", "Edges only", "Emboss", "Mosaic"),
        ),
        Param("strength", "Strength", "int", 7, 2, 40, 1),
    )

    _EMBOSS = np.array([[-2, -1, 0], [-1, 1, 1], [0, 1, 2]], dtype=np.float32)

    def apply(self, frame, p, ctx):
        style = p["style"]
        strength = int(p["strength"])
        if style == "Cartoon":
            # A bilateral filter over a full 720p frame costs well over 100 ms.
            # Doing it at half size and scaling back looks the same and keeps
            # the pipeline inside a 30 fps budget.
            h, w = frame.shape[:2]
            shrink = min(0.5, 480.0 / max(h, w))
            small = cv2.resize(frame, (max(2, int(w * shrink)),
                                       max(2, int(h * shrink))),
                               interpolation=cv2.INTER_AREA)
            small = cv2.bilateralFilter(small, 7, 150, 150)
            colour = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
            grey = cv2.medianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 5)
            edges = cv2.adaptiveThreshold(
                grey, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, strength // 2 + 2
            )
            # The mask= form of bitwise_and falls off OpenCV's fast path and
            # costs around 40 ms at 720p. This is the same result, 100x quicker.
            return cv2.bitwise_and(colour, cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR))
        if style == "Pencil sketch":
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            inv = 255 - grey
            k = _odd(strength * 2 + 1)
            blur = cv2.GaussianBlur(inv, (k, k), 0)
            sketch = cv2.divide(grey, 255 - blur, scale=256)
            return cv2.cvtColor(sketch, cv2.COLOR_GRAY2BGR)
        if style == "Edges only":
            edges = cv2.Canny(frame, strength * 4, strength * 10)
            return cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
        if style == "Emboss":
            return cv2.filter2D(frame, -1, self._EMBOSS, delta=110)
        # Mosaic
        h, w = frame.shape[:2]
        block = max(2, strength)
        small = cv2.resize(
            frame, (max(1, w // block), max(1, h // block)), interpolation=cv2.INTER_LINEAR
        )
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


class Vignette(Effect):
    id = "vignette"
    name = "Vignette"
    group = "Texture"
    blurb = "Darken the corners so attention lands in the middle."
    params = (
        Param("strength", "Strength", "float", 0.6, 0.0, 1.0, 0.05),
        Param("radius", "Size of the bright area", "float", 0.8, 0.2, 1.6, 0.05),
    )

    def __init__(self):
        self._key = None
        self._mask = None

    def _get_mask(self, shape, strength, radius):
        key = (shape, round(strength, 2), round(radius, 2))
        if key != self._key:
            h, w = shape
            ys, xs = np.mgrid[0:h, 0:w]
            cx, cy = w / 2, h / 2
            dist = np.sqrt(((xs - cx) / (w / 2)) ** 2 + ((ys - cy) / (h / 2)) ** 2)
            mask = np.clip(1 - strength * np.clip(dist / radius - 0.4, 0, None), 0, 1)
            grey = (mask * 255).astype(np.uint8)
            self._mask = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
            self._key = key
        return self._mask

    def apply(self, frame, p, ctx):
        mask = self._get_mask(frame.shape[:2], float(p["strength"]), float(p["radius"]))
        return cv2.multiply(frame, mask, scale=1 / 255.0)


class Retro(Effect):
    id = "retro"
    name = "Film and tube"
    group = "Texture"
    blurb = "Grain, scanlines and a slight colour split."
    params = (
        Param("grain", "Film grain", "int", 12, 0, 60, 1),
        Param("scanlines", "Scanlines", "float", 0.0, 0.0, 0.8, 0.05),
        Param("shift", "Colour split", "int", 0, 0, 12, 1),
    )

    def __init__(self):
        self._noise: List[np.ndarray] = []
        self._noise_key = None
        self._scaled: List[Tuple[np.ndarray, np.ndarray]] = []
        self._scaled_key = None
        self._scan_key = None
        self._scan = None

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        shift = int(p["shift"])
        if shift:
            b, g, r = cv2.split(frame)
            r = np.roll(r, shift, axis=1)
            b = np.roll(b, -shift, axis=1)
            frame = cv2.merge([b, g, r])

        grain = int(p["grain"])
        if grain:
            # The bank is built at unit strength, so moving the slider only
            # changes a multiplier instead of regenerating megabytes of noise.
            key = (h, w)
            if key != self._noise_key:
                rng = np.random.default_rng(7)
                self._noise = [
                    rng.normal(0, 1, (h, w)).astype(np.float32) for _ in range(6)
                ]
                self._noise_key = key
            if self._scaled_key != (key, grain):
                self._scaled = []
                for noise in self._noise:
                    values = noise * grain
                    positive = cv2.cvtColor(
                        np.clip(values, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR
                    )
                    negative = cv2.cvtColor(
                        np.clip(-values, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR
                    )
                    self._scaled.append((positive, negative))
                self._scaled_key = (key, grain)
            positive, negative = self._scaled[ctx.get("frame_index", 0) % len(self._scaled)]
            frame = cv2.subtract(cv2.add(frame, positive), negative)

        lines = float(p["scanlines"])
        if lines > 0.01:
            key = (h, w, round(lines, 2))
            if key != self._scan_key:
                mask = np.ones((h, 1, 1), dtype=np.float32)
                mask[::2] = 1 - lines
                self._scan = mask
                self._scan_key = key
            frame = (frame.astype(np.float32) * self._scan).astype(np.uint8)
        return frame


# --------------------------------------------------------------------------
# Overlays
# --------------------------------------------------------------------------


_POSITIONS = ("Top left", "Top right", "Bottom left", "Bottom right", "Centre")


def _anchor(position, frame_w, frame_h, box_w, box_h, margin):
    if position == "Top left":
        return margin, margin
    if position == "Top right":
        return frame_w - box_w - margin, margin
    if position == "Bottom left":
        return margin, frame_h - box_h - margin
    if position == "Centre":
        return (frame_w - box_w) // 2, (frame_h - box_h) // 2
    return frame_w - box_w - margin, frame_h - box_h - margin


class TextOverlay(Effect):
    id = "text"
    name = "Text"
    group = "Overlay"
    blurb = "Your name, a title, or a live clock. Use {time} and {date} for the clock."
    params = (
        Param("text", "Text", "text", "{time}"),
        Param("size", "Size", "float", 1.0, 0.3, 4.0, 0.1),
        Param("colour", "Text colour", "color", "#FFFFFF"),
        Param("position", "Position", "choice", "Bottom left", choices=_POSITIONS),
        Param("margin", "Distance from the edge", "int", 24, 0, 160, 2),
        Param("backdrop", "Dark backdrop", "float", 0.45, 0.0, 1.0, 0.05),
    )

    # OpenCV's built-in font is ASCII only and turns anything else into "?",
    # which would mangle most names in the world. Pillow, when it is
    # installed, draws the text properly with a real system font.
    _FONT_CANDIDATES = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    )

    # DejaVu and friends cover Latin, Greek and Cyrillic but have no CJK
    # glyphs, so Chinese, Japanese and Korean need a font of their own.
    _CJK_CANDIDATES = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSans-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    )

    def __init__(self):
        self._pil = None          # None = not looked yet, False = unavailable
        self._font_path = None
        self._cjk_path = None
        self._font_cache = {}
        self._warned = False

    @staticmethod
    def _first_existing(paths):
        return next((p for p in paths if os.path.isfile(p)), None)

    def _pillow(self):
        if self._pil is None:
            try:
                from PIL import Image, ImageDraw, ImageFont  # noqa: F401

                path = self._first_existing(self._FONT_CANDIDATES)
                if path is None:
                    import glob

                    found = sorted(glob.glob("/usr/share/fonts/**/*.ttf", recursive=True))
                    path = found[0] if found else None
                self._font_path = path
                self._cjk_path = self._first_existing(self._CJK_CANDIDATES)
                self._pil = bool(path or self._cjk_path)
            except Exception:
                self._pil = False
        return self._pil

    @staticmethod
    def _has_cjk(text):
        return any(
            "　" <= c <= "鿿" or "가" <= c <= "힯"
            or "＀" <= c <= "￯"
            for c in text
        )

    def _font(self, pixels, text):
        from PIL import ImageFont

        path = self._font_path
        if self._has_cjk(text) and self._cjk_path:
            path = self._cjk_path
        key = (path, int(pixels))
        if key not in self._font_cache:
            self._font_cache[key] = ImageFont.truetype(path, int(pixels))
            if len(self._font_cache) > 12:
                self._font_cache.pop(next(iter(self._font_cache)))
        return self._font_cache[key]

    @staticmethod
    def _to_ascii(text):
        """Last resort: José becomes Jose rather than Jos?."""
        import unicodedata

        stripped = unicodedata.normalize("NFKD", text)
        stripped = "".join(c for c in stripped if not unicodedata.combining(c))
        return stripped.encode("ascii", "replace").decode("ascii")

    def apply(self, frame, p, ctx):
        now = datetime.now()
        text = str(p["text"]).replace("{time}", now.strftime("%H:%M:%S"))
        text = text.replace("{date}", now.strftime("%d %b %Y"))
        text = text.replace("{fps}", f"{ctx.get('fps', 0):.0f}")
        if not text:
            return frame

        scale = float(p["size"])
        colour = _hex_to_bgr(p["colour"])
        margin = int(p["margin"])
        backdrop = float(p["backdrop"])
        h, w = frame.shape[:2]

        if not text.isascii() and self._pillow():
            return self._draw_pillow(frame, text, scale, colour, p["position"],
                                     margin, backdrop, w, h)

        if not text.isascii():
            text = self._to_ascii(text)
            if not self._warned:
                self._warned = True
                ctx.setdefault("errors", []).append(
                    "Accented characters need Pillow (pip install pillow); "
                    "showing them without accents for now."
                )

        thickness = max(1, int(round(scale * 2)))
        font = cv2.FONT_HERSHEY_DUPLEX
        (tw, th), base = cv2.getTextSize(text, font, scale, thickness)
        x, y = _anchor(p["position"], w, h, tw, th + base, margin)

        if backdrop > 0.01:
            pad = int(8 * scale)
            overlay = frame.copy()
            cv2.rectangle(
                overlay,
                (x - pad, y - pad),
                (x + tw + pad, y + th + base + pad),
                (14, 16, 20),
                -1,
            )
            frame = cv2.addWeighted(overlay, backdrop, frame, 1 - backdrop, 0)
        cv2.putText(
            frame, text, (x, y + th), font, scale, colour, thickness, cv2.LINE_AA,
        )
        return frame

    def _draw_pillow(self, frame, text, scale, colour, position, margin,
                     backdrop, w, h):
        from PIL import Image, ImageDraw

        # Roughly match the size Hershey would have produced at this scale.
        font = self._font(max(8, round(scale * 30)), text)
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(image, "RGBA")
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        tw, th = right - left, bottom - top
        x, y = _anchor(position, w, h, tw, th, margin)

        if backdrop > 0.01:
            pad = int(8 * scale)
            draw.rectangle(
                (x - pad, y - pad, x + tw + pad, y + th + pad),
                fill=(20, 16, 14, int(255 * backdrop)),
            )
        draw.text((x - left, y - top), text, font=font, fill=colour[::-1])
        return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
VIDEO_SUFFIXES = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".mpg", ".mpeg")


class _VideoLoop:
    """Plays a video file frame by frame, at its own speed, on repeat."""

    def __init__(self, path):
        self.path = path
        self.capture = cv2.VideoCapture(path)
        fps = self.capture.get(cv2.CAP_PROP_FPS)
        self.period = 1.0 / fps if 1 <= fps <= 240 else 1 / 30.0
        self.frame = None
        self._next_at = 0.0

    def ok(self):
        return self.capture is not None and self.capture.isOpened()

    def read(self, now, loop=True):
        if not self.ok():
            return self.frame
        if self.frame is not None and now < self._next_at:
            return self.frame
        got, frame = self.capture.read()
        if not got or frame is None:
            if not loop:
                return self.frame
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            got, frame = self.capture.read()
            if not got or frame is None:
                return self.frame
        self.frame = frame
        self._next_at = now + self.period
        return self.frame

    def release(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None


class MediaOverlay(Effect):
    id = "logo"          # kept so presets saved before this existed still load
    name = "Picture or video on top"
    group = "Overlay"
    blurb = (
        "Put a logo, a picture or a playing video over the camera. Transparent "
        "PNGs keep their transparency."
    )
    params = (
        Param("path", "Image or video", "file", "",
              filters="Pictures and video (*.png *.jpg *.jpeg *.webp *.bmp *.gif "
                      "*.mp4 *.mkv *.avi *.mov *.webm);;All files (*)"),
        Param("scale", "Size", "float", 0.25, 0.02, 1.0, 0.01),
        Param("opacity", "Opacity", "float", 0.95, 0.05, 1.0, 0.05),
        Param("position", "Position", "choice", "Top right",
              choices=_POSITIONS + ("Free",)),
        Param("margin", "Distance from the edge", "int", 24, 0, 160, 2),
        Param("free_x", "Across", "float", 0.5, 0.0, 1.0, 0.01,
              hint="Used when Position is set to Free."),
        Param("free_y", "Down", "float", 0.5, 0.0, 1.0, 0.01,
              hint="Used when Position is set to Free."),
        Param("crop_left", "Trim left", "float", 0.0, 0.0, 0.45, 0.01),
        Param("crop_right", "Trim right", "float", 0.0, 0.0, 0.45, 0.01),
        Param("crop_top", "Trim top", "float", 0.0, 0.0, 0.45, 0.01),
        Param("crop_bottom", "Trim bottom", "float", 0.0, 0.0, 0.45, 0.01),
        Param("stretch", "Stretch", "float", 1.0, 0.25, 3.0, 0.05,
              hint="Makes it wider or narrower than the original."),
        Param("angle", "Rotate", "float", 0.0, -180.0, 180.0, 1.0, suffix="°"),
        Param("loop", "Loop the video", "bool", True),
        Param("key_black", "Hide black areas", "bool", False,
              hint="Useful for effects footage shot on black."),
    )

    def __init__(self):
        self._path = None
        self._image = None
        self._video: Optional[_VideoLoop] = None
        self._warned = None

    # -- loading -------------------------------------------------------

    def _source_frame(self, path, now, loop):
        """Return BGR or BGRA for the current moment, image or video."""
        if path != self._path:
            self._release()
            self._path = path
            suffix = os.path.splitext(path)[1].lower()
            if suffix in VIDEO_SUFFIXES:
                self._video = _VideoLoop(path)
                if not self._video.ok():
                    self._video = None
            else:
                self._image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
                if self._image is None:  # an unknown suffix may still be a video
                    self._video = _VideoLoop(path)
                    if not self._video.ok():
                        self._video = None

        if self._video is not None:
            return self._video.read(now, loop)
        return self._image

    def _release(self):
        if self._video is not None:
            self._video.release()
            self._video = None
        self._image = None

    # -- drawing -------------------------------------------------------

    def apply(self, frame, p, ctx):
        path = p["path"]
        if not path or not os.path.isfile(path):
            return frame

        media = self._source_frame(path, ctx.get("t", 0.0), bool(p["loop"]))
        if media is None:
            if self._warned != path:
                self._warned = path
                ctx.setdefault("errors", []).append(
                    f"Could not read {os.path.basename(path)} as a picture or a video."
                )
            return frame

        media = self._crop(media, p)
        if media is None:
            return frame

        h, w = frame.shape[:2]
        target_w = max(2, int(w * float(p["scale"])))
        target_h = max(2, int(target_w * media.shape[0] / media.shape[1]
                             / max(0.05, float(p["stretch"]))))
        media = cv2.resize(media, (target_w, target_h), interpolation=cv2.INTER_AREA)

        colour, alpha = self._split(media, float(p["opacity"]), bool(p["key_black"]))

        angle = float(p["angle"])
        if abs(angle) > 0.5:
            colour, alpha = self._rotate(colour, alpha, angle)
            target_h, target_w = colour.shape[:2]

        x, y = self._place(p, w, h, target_w, target_h)
        return self._blend(frame, colour, alpha, x, y)

    @staticmethod
    def _crop(media, p):
        mh, mw = media.shape[:2]
        x0 = int(mw * float(p["crop_left"]))
        x1 = mw - int(mw * float(p["crop_right"]))
        y0 = int(mh * float(p["crop_top"]))
        y1 = mh - int(mh * float(p["crop_bottom"]))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        return media[y0:y1, x0:x1]

    @staticmethod
    def _split(media, opacity, key_black):
        """Separate colour from a float alpha mask in 0..1."""
        if media.ndim == 2:
            media = cv2.cvtColor(media, cv2.COLOR_GRAY2BGR)
        if media.shape[2] == 4:
            alpha = media[:, :, 3:4].astype(np.float32) / 255.0
            colour = np.ascontiguousarray(media[:, :, :3])
        else:
            alpha = np.ones(media.shape[:2] + (1,), dtype=np.float32)
            colour = media
        if key_black:
            luma = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY).astype(np.float32)
            alpha = alpha * np.clip(luma / 40.0, 0, 1)[:, :, None]
        return colour, alpha * opacity

    @staticmethod
    def _rotate(colour, alpha, angle):
        h, w = colour.shape[:2]
        radians = np.deg2rad(abs(angle) % 180)
        cos, sin = abs(np.cos(radians)), abs(np.sin(radians))
        new_w = int(w * cos + h * sin)
        new_h = int(w * sin + h * cos)
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        matrix[0, 2] += (new_w - w) / 2
        matrix[1, 2] += (new_h - h) / 2
        colour = cv2.warpAffine(colour, matrix, (new_w, new_h), flags=cv2.INTER_LINEAR)
        alpha = cv2.warpAffine(alpha, matrix, (new_w, new_h), flags=cv2.INTER_LINEAR)
        return colour, alpha.reshape(new_h, new_w, 1)

    @staticmethod
    def _place(p, w, h, target_w, target_h):
        if p["position"] == "Free":
            x = int((w - target_w) * float(p["free_x"]))
            y = int((h - target_h) * float(p["free_y"]))
        else:
            x, y = _anchor(p["position"], w, h, target_w, target_h, int(p["margin"]))
        return x, y

    @staticmethod
    def _blend(frame, colour, alpha, x, y):
        """Alpha-composite, clipping whatever hangs off the edge of the frame."""
        h, w = frame.shape[:2]
        th, tw = colour.shape[:2]

        sx0, sy0 = max(0, -x), max(0, -y)
        dx0, dy0 = max(0, x), max(0, y)
        dx1, dy1 = min(w, x + tw), min(h, y + th)
        if dx1 <= dx0 or dy1 <= dy0:
            return frame

        sx1, sy1 = sx0 + (dx1 - dx0), sy0 + (dy1 - dy0)
        patch = colour[sy0:sy1, sx0:sx1].astype(np.float32)
        mask = alpha[sy0:sy1, sx0:sx1]
        roi = frame[dy0:dy1, dx0:dx1].astype(np.float32)
        frame[dy0:dy1, dx0:dx1] = (roi * (1 - mask) + patch * mask).astype(np.uint8)
        return frame


class OutputSize(Effect):
    id = "outsize"
    name = "Output size"
    group = "Output"
    blurb = (
        "Send a different size than the camera gives. Set this last - it decides "
        "the size other apps and recordings receive."
    )
    params = (
        Param(
            "size", "Size", "choice", "1280 x 720",
            choices=(
                "Same as the camera", "1920 x 1080", "1280 x 720", "960 x 540",
                "854 x 480", "640 x 360", "1080 x 1920 (vertical)",
                "1080 x 1080 (square)", "Custom",
            ),
        ),
        Param("width", "Custom width", "int", 1280, 160, 3840, 2),
        Param("height", "Custom height", "int", 720, 120, 2160, 2),
        Param(
            "mode", "When the shape differs", "choice", "Fit, with bars",
            choices=("Fit, with bars", "Fill, cropping the edges", "Stretch"),
        ),
        Param("pad_colour", "Bar colour", "color", "#0E1116"),
    )

    _PRESETS = {
        "1920 x 1080": (1920, 1080), "1280 x 720": (1280, 720),
        "960 x 540": (960, 540), "854 x 480": (854, 480),
        "640 x 360": (640, 360), "1080 x 1920 (vertical)": (1080, 1920),
        "1080 x 1080 (square)": (1080, 1080),
    }

    def apply(self, frame, p, ctx):
        choice = p["size"]
        if choice == "Same as the camera":
            return frame
        if choice == "Custom":
            target = (int(p["width"]), int(p["height"]))
        else:
            target = self._PRESETS.get(choice)
        if not target:
            return frame

        tw, th = target
        h, w = frame.shape[:2]
        if (w, h) == (tw, th):
            return frame

        mode = p["mode"]
        if mode == "Stretch":
            return cv2.resize(frame, (tw, th), interpolation=cv2.INTER_LINEAR)

        if mode == "Fill, cropping the edges":
            scale = max(tw / w, th / h)
            rw, rh = max(tw, int(w * scale)), max(th, int(h * scale))
            resized = cv2.resize(frame, (rw, rh), interpolation=cv2.INTER_LINEAR)
            x, y = (rw - tw) // 2, (rh - th) // 2
            return np.ascontiguousarray(resized[y:y + th, x:x + tw])

        scale = min(tw / w, th / h)
        rw, rh = max(1, int(w * scale)), max(1, int(h * scale))
        resized = cv2.resize(frame, (rw, rh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((th, tw, 3), _hex_to_bgr(p["pad_colour"]), dtype=np.uint8)
        x, y = (tw - rw) // 2, (th - rh) // 2
        canvas[y:y + rh, x:x + rw] = resized
        return canvas


# --------------------------------------------------------------------------
# Colour grading
# --------------------------------------------------------------------------


def _curve_lut(points) -> np.ndarray:
    """Build a 256-entry lookup table from a few (input, output) control points."""
    xs = np.array([p[0] for p in points], dtype=np.float32)
    ys = np.array([p[1] for p in points], dtype=np.float32)
    grid = np.interp(np.arange(256), xs, ys)
    return np.clip(grid, 0, 255).astype(np.uint8)


class ColourGrade(Effect):
    id = "grade"
    name = "Colour grade"
    group = "Colour"
    blurb = "A finished look in one step - warm, cool, filmic, and others."
    params = (
        Param(
            "look", "Look", "choice", "Warm",
            choices=(
                "Warm", "Cool", "Golden hour", "Moonlight", "Teal and orange",
                "Vintage film", "Faded", "Noir", "Vibrant", "Pastel",
                "Cyberpunk", "Bleach bypass",
            ),
        ),
        Param("strength", "Strength", "float", 0.85, 0.0, 1.0, 0.05),
    )

    # Each look is three curves, one per channel, as control points. Only
    # the middle of each curve varies, so the ends are filled in below.
    _LOOKS = {
        # Channels in OpenCV's own order, each as (shadow, midtone, highlight).
        #                  red             green           blue
        "Warm":            ((10, 140, 255), (4, 128, 250), (0, 116, 238)),
        "Cool":            ((0, 116, 238), (2, 126, 248), (12, 142, 255)),
        "Golden hour":     ((22, 152, 255), (8, 130, 246), (0, 104, 214)),
        "Moonlight":       ((0, 102, 214), (6, 120, 236), (26, 152, 255)),
        "Teal and orange": ((6, 150, 255), (8, 126, 240), (24, 120, 226)),
        "Vintage film":    ((32, 146, 238), (26, 132, 228), (34, 124, 206)),
        "Faded":           ((44, 140, 226), (44, 140, 226), (52, 144, 228)),
        "Noir":            ((0, 128, 255), (0, 128, 255), (0, 128, 255)),
        "Vibrant":         ((0, 134, 255), (0, 132, 255), (0, 130, 255)),
        "Pastel":          ((38, 148, 250), (34, 146, 248), (40, 150, 250)),
        "Cyberpunk":       ((14, 134, 244), (0, 104, 214), (30, 162, 255)),
        "Bleach bypass":   ((10, 150, 255), (10, 150, 255), (10, 148, 252)),
    }

    @staticmethod
    def _points(triple):
        """A channel's three numbers, as the curve control points."""
        shadow, midtone, highlight = triple
        return [(0, shadow), (128, midtone), (255, highlight)]

    def __init__(self):
        self._cache = {}

    def _luts(self, look):
        if look not in self._cache:
            # The table reads red, green, blue, because that is how anyone
            # describing a look would say it. OpenCV keeps its channels the
            # other way round, so they are swapped here and nowhere else.
            r, g, b = self._LOOKS.get(look, self._LOOKS["Warm"])
            self._cache[look] = (_curve_lut(self._points(b)),
                                 _curve_lut(self._points(g)),
                                 _curve_lut(self._points(r)))
        return self._cache[look]

    def apply(self, frame, p, ctx):
        look = p["look"]
        lut_b, lut_g, lut_r = self._luts(look)
        table = np.stack([lut_b, lut_g, lut_r], axis=-1).reshape(1, 256, 3)
        out = cv2.LUT(frame, table)

        if look == "Noir":
            grey = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
            out = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
        elif look in ("Vibrant", "Cyberpunk"):
            hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.int16)
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * (1.45 if look == "Vibrant" else 1.3), 0, 255)
            out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        elif look == "Pastel":
            hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.int16)
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 0.6, 0, 255)
            out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        amount = float(p["strength"])
        if amount >= 0.99:
            return out
        return cv2.addWeighted(out, amount, frame, 1 - amount, 0)


# --------------------------------------------------------------------------
# Stylised looks
# --------------------------------------------------------------------------


class Glitch(Effect):
    id = "glitch"
    name = "Glitch"
    group = "Texture"
    blurb = "Digital breakup - blocks slip sideways, colour separates, bands tear."
    params = (
        Param("amount", "Amount", "float", 0.5, 0.05, 1.0, 0.05),
        Param("blocks", "Slipped blocks", "int", 6, 0, 24, 1),
        Param("split", "Colour separation", "int", 6, 0, 30, 1),
        Param("tear", "Tearing", "float", 0.35, 0.0, 1.0, 0.05),
        Param("speed", "Speed", "float", 1.0, 0.1, 3.0, 0.1),
        Param("steady", "Hold still between jumps", "bool", True),
    )

    def __init__(self):
        self._rng = np.random.default_rng(1)
        self._seed_at = -1.0
        self._bands = []

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        amount = float(p["amount"])
        t = ctx.get("t", 0.0) * float(p["speed"])

        # New arrangement a few times a second, so it flickers rather than boils.
        step = 0.12 if not p["steady"] else 0.28
        slot = int(t / step)
        if slot != self._seed_at:
            self._seed_at = slot
            self._rng = np.random.default_rng(slot * 7919)
            count = int(p["blocks"])
            self._bands = [
                (
                    int(self._rng.integers(0, max(1, h - 8))),
                    int(self._rng.integers(6, max(8, h // 10))),
                    int(self._rng.integers(-w // 12, w // 12) * amount),
                )
                for _ in range(count)
            ]

        out = frame.copy()
        for y, height, shift in self._bands:
            y1 = min(h, y + height)
            if shift and y1 > y:
                out[y:y1] = np.roll(out[y:y1], shift, axis=1)

        split = int(int(p["split"]) * amount)
        if split:
            b, g, r = cv2.split(out)
            r = np.roll(r, split, axis=1)
            b = np.roll(b, -split, axis=1)
            out = cv2.merge([b, g, r])

        tear = float(p["tear"]) * amount
        if tear > 0.02:
            rows = int(h * 0.04 * tear) + 1
            start = int(self._rng.integers(0, max(1, h - rows)))
            out[start:start + rows] = np.roll(
                out[start:start + rows], int(w * 0.2 * tear), axis=1
            )
        return out


class Terminal(Effect):
    id = "terminal"
    name = "Terminal"
    group = "Texture"
    blurb = "Old phosphor monitor - one colour, scanlines and a soft glow."
    params = (
        Param(
            "colour", "Phosphor", "choice", "Green",
            choices=("Green", "Amber", "Cyan", "White", "Red alert"),
        ),
        Param("scanlines", "Scanlines", "float", 0.45, 0.0, 1.0, 0.05),
        Param("glow", "Glow", "float", 0.5, 0.0, 1.0, 0.05),
        Param("contrast", "Contrast", "float", 1.35, 0.5, 3.0, 0.05),
        Param("noise", "Static", "int", 6, 0, 40, 1),
    )

    _TINTS = {                    # BGR
        "Green": (60, 255, 90), "Amber": (40, 170, 255), "Cyan": (255, 230, 90),
        "White": (235, 240, 245), "Red alert": (60, 60, 255),
    }

    def __init__(self):
        self._scan = None
        self._scan_key = None
        self._noise = None
        self._noise_key = None
        self._grain = None
        self._grain_key = None

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        grey = cv2.convertScaleAbs(grey, alpha=float(p["contrast"]), beta=-18)

        glow = float(p["glow"])
        if glow > 0.02:
            soft = cv2.GaussianBlur(grey, (0, 0), 3.5)
            grey = cv2.addWeighted(grey, 1.0, soft, glow, 0)

        noise = int(p["noise"])
        if noise:
            key = (h, w)
            if key != self._noise_key:
                rng = np.random.default_rng(3)
                self._noise = [rng.normal(0, 1, (h, w)).astype(np.float32) for _ in range(5)]
                self._noise_key = key
            index = ctx.get("frame_index", 0) % len(self._noise)
            if self._grain_key != (key, noise):
                self._grain = [
                    (np.clip(n * noise, 0, 255).astype(np.uint8),
                     np.clip(-n * noise, 0, 255).astype(np.uint8))
                    for n in self._noise
                ]
                self._grain_key = (key, noise)
            positive, negative = self._grain[index]
            grey = cv2.subtract(cv2.add(grey, positive), negative)

        tint = self._TINTS.get(p["colour"], self._TINTS["Green"])
        out = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
        out = cv2.multiply(out, _flat(out, tint), scale=1 / 255.0)

        lines = float(p["scanlines"])
        if lines > 0.02:
            key = (h, round(lines, 2))
            if key != self._scan_key:
                mask = np.full((h, 1, 1), 255, dtype=np.uint8)
                mask[::3] = int(round((1 - lines) * 255))
                self._scan = np.repeat(np.repeat(mask, 3, axis=2), 1, axis=1)
                self._scan_key = key
            out = cv2.multiply(out, np.broadcast_to(
                self._scan, out.shape).astype(np.uint8), scale=1 / 255.0)
        return out


class Bloom(Effect):
    id = "bloom"
    name = "Glow"
    group = "Texture"
    blurb = "Light blooms out of the bright parts. The soft, dreamy look."
    params = (
        Param("threshold", "Bloom above", "int", 180, 60, 250, 5),
        Param("strength", "Strength", "float", 0.55, 0.0, 1.5, 0.05),
        Param("radius", "Spread", "int", 25, 3, 80, 2),
        Param("soften", "Soften everything", "float", 0.0, 0.0, 1.0, 0.05),
    )

    def apply(self, frame, p, ctx):
        threshold = int(p["threshold"])
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(grey, threshold, 255, cv2.THRESH_BINARY)
        bright = cv2.bitwise_and(frame, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR))

        h, w = frame.shape[:2]
        small = cv2.resize(bright, (max(8, w // 4), max(8, h // 4)), interpolation=cv2.INTER_AREA)
        k = _odd(max(3, int(p["radius"]) // 2))
        small = cv2.GaussianBlur(small, (k, k), 0)
        glow = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)

        out = cv2.addWeighted(frame, 1.0, glow, float(p["strength"]), 0)

        soften = float(p["soften"])
        if soften > 0.02:
            blurred = cv2.GaussianBlur(out, (0, 0), 4)
            out = cv2.addWeighted(out, 1 - soften * 0.6, blurred, soften * 0.6, 0)
        return out


class Halftone(Effect):
    id = "halftone"
    name = "Comic dots"
    group = "Texture"
    blurb = "Printed comic look, built from dots or lines."
    params = (
        Param("style", "Pattern", "choice", "Dots", choices=("Dots", "Lines", "Crosshatch")),
        Param("size", "Pattern size", "int", 6, 2, 20, 1),
        Param("colour", "Keep the colour", "bool", True),
        Param("ink", "Ink", "color", "#101318"),
        Param("paper", "Paper", "color", "#F2EFE6"),
    )

    def __init__(self):
        self._key = None
        self._grid = None

    def _pattern(self, shape, size, style):
        key = (shape, size, style)
        if key == self._key:
            return self._grid
        h, w = shape
        ys, xs = np.mgrid[0:h, 0:w]
        if style == "Dots":
            gx = (xs % size) - size / 2.0
            gy = (ys % size) - size / 2.0
            # The threshold is highest at the middle of each cell, so an ink
            # dot appears there first and grows as the picture gets darker -
            # which is how a printed halftone actually works.
            grid = 1.0 - np.clip(np.sqrt(gx * gx + gy * gy) / (size / 2.0), 0, 1)
        elif style == "Lines":
            grid = (np.abs(((xs + ys) % size) - size / 2.0)) / (size / 2.0)
        else:
            a = np.abs(((xs + ys) % size) - size / 2.0) / (size / 2.0)
            b = np.abs(((xs - ys) % size) - size / 2.0) / (size / 2.0)
            grid = np.minimum(a, b)
        self._key = key
        self._grid = np.clip(grid, 0, 1).astype(np.float32)
        return self._grid

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        size = max(2, int(p["size"]))
        grid = self._pattern((h, w), size, p["style"])
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        grey = cv2.convertScaleAbs(grey, alpha=1.25, beta=-18)
        lit = cv2.compare(grey, (grid * 255).astype(np.uint8), cv2.CMP_GT)
        paper = _flat(frame, _hex_to_bgr(p["paper"]))
        if p["colour"]:
            return _blend(paper, frame, lit)
        return _blend(_flat(frame, _hex_to_bgr(p["ink"])), paper, lit)


class Duotone(Effect):
    id = "duotone"
    name = "Two-tone"
    group = "Colour"
    blurb = "Map the picture between two colours. Strong, poster-like."
    params = (
        Param("dark", "Shadow colour", "color", "#141B33"),
        Param("light", "Highlight colour", "color", "#F2B366"),
        Param("amount", "Amount", "float", 1.0, 0.0, 1.0, 0.05),
        Param("contrast", "Contrast", "float", 1.1, 0.4, 2.5, 0.05),
    )

    def __init__(self):
        self._key = None
        self._table = None

    def apply(self, frame, p, ctx):
        key = (p["dark"], p["light"])
        if key != self._key:
            d = np.array(_hex_to_bgr(p["dark"]), dtype=np.float32)
            l = np.array(_hex_to_bgr(p["light"]), dtype=np.float32)
            ramp = np.linspace(0, 1, 256, dtype=np.float32)[:, None]
            self._table = (d[None, :] * (1 - ramp) + l[None, :] * ramp).astype(np.uint8)
            self._table = self._table.reshape(1, 256, 3)
            self._key = key

        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        grey = cv2.convertScaleAbs(grey, alpha=float(p["contrast"]), beta=0)
        mapped = cv2.LUT(cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR), self._table)

        amount = float(p["amount"])
        if amount >= 0.99:
            return mapped
        return cv2.addWeighted(mapped, amount, frame, 1 - amount, 0)


class TiltShift(Effect):
    id = "tiltshift"
    name = "Depth blur"
    group = "Texture"
    blurb = "Keep a band sharp and blur away from it, like a wide aperture."
    params = (
        Param("centre", "Sharp band centre", "float", 0.5, 0.0, 1.0, 0.02),
        Param("height", "Sharp band height", "float", 0.35, 0.05, 1.0, 0.02),
        Param("strength", "Blur", "int", 25, 3, 80, 2),
        Param("vertical", "Run the band up and down", "bool", False),
    )

    def __init__(self):
        self._key = None
        self._mask = None

    def _get_mask(self, h, w, centre, height, vertical):
        key = (h, w, round(centre, 3), round(height, 3), vertical)
        if key == self._key:
            return self._mask
        axis = np.linspace(0, 1, w if vertical else h, dtype=np.float32)
        distance = np.abs(axis - centre)
        edge = height / 2.0
        mask = np.clip((distance - edge) / max(0.01, edge), 0, 1)
        mask = mask[None, :] if vertical else mask[:, None]
        mask = np.broadcast_to(mask, (h, w)).astype(np.float32)
        self._key = key
        self._mask = cv2.cvtColor((mask * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        return self._mask

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        mask = self._get_mask(h, w, float(p["centre"]), float(p["height"]), bool(p["vertical"]))
        small = cv2.resize(frame, (max(8, w // 3), max(8, h // 3)), interpolation=cv2.INTER_AREA)
        k = _odd(max(3, int(p["strength"]) // 3))
        blurred = cv2.resize(cv2.GaussianBlur(small, (k, k), 0), (w, h),
                             interpolation=cv2.INTER_LINEAR)
        inverse = cv2.bitwise_not(mask)
        return cv2.add(
            cv2.multiply(frame, inverse, scale=1 / 255.0),
            cv2.multiply(blurred, mask, scale=1 / 255.0),
        )


class Kaleidoscope(Effect):
    id = "kaleido"
    name = "Mirror and kaleidoscope"
    group = "Texture"
    blurb = "Fold the picture back on itself."
    params = (
        Param(
            "mode", "Fold", "choice", "Mirror left onto right",
            choices=("Mirror left onto right", "Mirror right onto left",
                     "Mirror top onto bottom", "Four-way", "Kaleidoscope"),
        ),
        Param("segments", "Kaleidoscope segments", "int", 6, 3, 16, 1),
        Param("spin", "Spin", "float", 0.0, -2.0, 2.0, 0.1),
    )

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        mode = p["mode"]

        if mode == "Mirror left onto right":
            half = frame[:, : w // 2]
            return np.hstack([half, cv2.flip(half, 1)])[:, :w]
        if mode == "Mirror right onto left":
            half = frame[:, w - w // 2:]
            return np.hstack([cv2.flip(half, 1), half])[:, :w]
        if mode == "Mirror top onto bottom":
            half = frame[: h // 2]
            return np.vstack([half, cv2.flip(half, 0)])[:h]
        if mode == "Four-way":
            quarter = frame[: h // 2, : w // 2]
            top = np.hstack([quarter, cv2.flip(quarter, 1)])
            return np.vstack([top, cv2.flip(top, 0)])[:h, :w]

        # Kaleidoscope: sample one wedge and rotate it around.
        segments = max(3, int(p["segments"]))
        angle = ctx.get("t", 0.0) * float(p["spin"]) * 40.0
        out = frame.copy()
        step = 360.0 / segments
        wedge = frame
        for i in range(1, segments):
            matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle + i * step, 1.0)
            turned = cv2.warpAffine(wedge, matrix, (w, h), borderMode=cv2.BORDER_REFLECT)
            mask = np.zeros((h, w), dtype=np.uint8)
            start = -90 + (i - 0.5) * step
            cv2.ellipse(mask, (w // 2, h // 2), (w, h), 0, start, start + step, 255, -1)
            out = np.where(mask[:, :, None] > 0, turned, out)
        return out


# --------------------------------------------------------------------------
# Faces
# --------------------------------------------------------------------------


class SkinSmooth(Effect):
    id = "skin"
    name = "Skin smoothing"
    group = "Colour"
    blurb = (
        "Softens skin while keeping eyes, hair and edges sharp. Works on "
        "whatever skin is in shot, so it does not need to find a face."
    )
    params = (
        Param("amount", "Amount", "float", 0.55, 0.0, 1.0, 0.05),
        Param("radius", "Softness", "float", 0.5, 0.1, 1.0, 0.05),
        Param("even", "Even out the tone", "float", 0.30, 0.0, 1.0, 0.05),
        Param("glow", "Glow", "float", 0.0, 0.0, 1.0, 0.05),
        Param("whole", "Apply to the whole picture", "bool", False,
              hint="Off means only skin is softened."),
    )

    def apply(self, frame, p, ctx):
        amount = float(p["amount"])
        if amount <= 0.001:
            return frame
        h, w = frame.shape[:2]
        # The filter is quadratic in the picture size, so it runs on a small
        # copy. Skin has no fine detail to lose, and the result is scaled
        # back up before it is mixed in.
        target = 420.0
        scale = min(1.0, target / max(h, w))
        if scale < 0.99:
            small = cv2.resize(frame, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA)
        else:
            small = frame

        radius = float(p["radius"])
        strength = 24 + int(radius * (60 + float(p["even"]) * 90))
        smooth = cv2.bilateralFilter(small, 5, strength, strength)
        if small is not frame:
            smooth = cv2.resize(smooth, (w, h), interpolation=cv2.INTER_LINEAR)

        if p.get("whole"):
            mask = _flat(frame, (255, 255, 255))
        else:
            ycrcb = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb)
            skin = cv2.inRange(ycrcb, (0, 133, 77), (255, 176, 130))
            skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            skin = cv2.GaussianBlur(skin, (0, 0), max(1.5, min(skin.shape) * 0.02))
            if skin.shape[:2] != (h, w):
                skin = cv2.resize(skin, (w, h), interpolation=cv2.INTER_LINEAR)
            mask = cv2.cvtColor(skin, cv2.COLOR_GRAY2BGR)

        if amount < 0.999:
            mask = cv2.multiply(mask, _flat(mask, (255, 255, 255)), scale=amount / 255.0)
        out = _blend(frame, smooth, mask)

        glow = float(p["glow"])
        if glow > 0.001:
            haze = cv2.GaussianBlur(out, (0, 0), max(3.0, min(h, w) * 0.02))
            haze = cv2.convertScaleAbs(haze, alpha=glow)
            out = _screen(out, haze)
        return out


class FaceWarp(Effect):
    id = "facewarp"
    name = "Face warp"
    group = "Face"
    blurb = (
        "Stretches the face itself - a big head, a tiny one, a squeeze or a "
        "wobble. The rest of the picture is left alone."
    )
    params = (
        Param("style", "Style", "choice", "Big head",
              choices=("Big head", "Tiny head", "Wide", "Narrow", "Tall",
                       "Bulge", "Pinch", "Wobble", "Swirl")),
        Param("amount", "Amount", "float", 0.55, 0.05, 1.0, 0.05),
        Param("reach", "Reach", "float", 1.0, 0.5, 2.0, 0.05, suffix="x",
              hint="How far past the face the warp fades out."),
        Param("speed", "Wobble speed", "float", 1.0, 0.1, 4.0, 0.1,
              suffix="x", hint="Only used by Wobble and Swirl."),
        Param("faces", "Apply to", "choice", "Everyone",
              choices=("Everyone", "Only me", "Everyone except me")),
        Param("every", "Look for faces every N frames", "int", 3, 1, 12, 1),
        Param("steady", "Steadiness", "float", 0.45, 0.1, 1.0, 0.05),
    )

    def availability(self):
        from .faces import face_detection_ready

        return face_detection_ready()

    def __init__(self):
        self._tracker = None
        self._grid = None
        self._grid_shape = None
        self._warned = False

    def _base_grid(self, shape):
        if self._grid_shape != shape:
            h, w = shape
            ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
            self._grid = (xs, ys)
            self._grid_shape = shape
        return self._grid

    def apply(self, frame, p, ctx):
        from .faces import FaceTracker

        if self._tracker is None:
            self._tracker = FaceTracker()
        try:
            faces = self._tracker.detect(
                frame, ctx.get("frame_index", 0),
                every=int(p["every"]), smoothing=float(p["steady"]),
            )
        except Exception as exc:
            if not self._warned:
                self._warned = True
                ctx.setdefault("errors", []).append(f"Face tracking stopped: {exc}")
            return frame
        if not faces:
            return frame

        which = p.get("faces", "Everyone")
        if which != "Everyone":
            nearest = max(faces, key=lambda f: f.area)
            faces = [nearest] if which == "Only me" else [
                f for f in faces if f is not nearest
            ]

        style = p.get("style", "Big head")
        amount = float(p["amount"])
        phase = ctx.get("frame_index", 0) * 0.12 * float(p.get("speed", 1.0))

        out = frame
        for face in faces:
            radius = max(8.0, face.width * float(p.get("reach", 1.0)) * 0.95)
            out = self._warp(out, face.centre, radius, style, amount, phase)
        return out

    def _warp(self, frame, centre, radius, style, amount, phase):
        h, w = frame.shape[:2]
        cx, cy = centre
        x0 = max(0, int(cx - radius * 1.25))
        x1 = min(w, int(cx + radius * 1.25))
        y0 = max(0, int(cy - radius * 1.45))
        y1 = min(h, int(cy + radius * 1.45))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return frame

        roi = frame[y0:y1, x0:x1]
        rh, rw = roi.shape[:2]
        ys, xs = np.mgrid[0:rh, 0:rw].astype(np.float32)
        dx = xs - (cx - x0)
        dy = ys - (cy - y0)
        dist = np.sqrt(dx * dx + dy * dy) / radius
        # Smooth falloff so the warp blends into the untouched picture.
        t = np.clip(1.0 - dist, 0.0, 1.0)
        fade = t * t * (3.0 - 2.0 * t)

        sx, sy = dx.copy(), dy.copy()
        if style in ("Big head", "Tiny head", "Wide", "Narrow", "Tall"):
            grow = {
                "Big head": (1 + amount * 0.9, 1 + amount * 0.9),
                "Tiny head": (1 - amount * 0.45, 1 - amount * 0.45),
                "Wide": (1 + amount * 1.1, 1 - amount * 0.25),
                "Narrow": (1 - amount * 0.45, 1 + amount * 0.35),
                "Tall": (1 - amount * 0.25, 1 + amount * 1.0),
            }[style]
            kx = 1.0 + (1.0 / grow[0] - 1.0) * fade
            ky = 1.0 + (1.0 / grow[1] - 1.0) * fade
            sx = dx * kx
            sy = dy * ky
        elif style in ("Bulge", "Pinch"):
            # These map a point to where it should be read from, so the
            # directions are the reverse of what they look like: a bulge
            # magnifies the middle, which means each point samples from
            # nearer the centre than it sits, and a pinch is the other way.
            power = (1.0 - amount * 0.5) if style == "Bulge" \
                else (1.0 + amount * 1.6)
            safe = np.maximum(dist, 1e-4)
            factor = np.power(safe, 1.0 / power) / safe
            factor = 1.0 + (factor - 1.0) * fade
            sx = dx * factor
            sy = dy * factor
        elif style == "Wobble":
            wave = np.sin(dist * 9.0 - phase) * amount * radius * 0.10 * fade
            safe = np.maximum(dist * radius, 1e-4)
            sx = dx + dx / safe * wave
            sy = dy + dy / safe * wave
        elif style == "Swirl":
            angle = amount * 2.2 * fade * math.sin(phase * 0.35)
            cos_a, sin_a = np.cos(angle), np.sin(angle)
            sx = dx * cos_a - dy * sin_a
            sy = dx * sin_a + dy * cos_a

        map_x = (sx + (cx - x0)).astype(np.float32)
        map_y = (sy + (cy - y0)).astype(np.float32)
        warped = cv2.remap(roi, map_x, map_y, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)
        out = frame.copy()
        out[y0:y1, x0:x1] = warped
        return out


class Weather(Effect):
    id = "weather"
    name = "Falling things"
    group = "Overlay"
    blurb = (
        "Rain, snow, sparkles and the rest, drifting down in front of the "
        "picture."
    )
    params = (
        Param("style", "What falls", "choice", "Snow",
              choices=("Snow", "Rain", "Sparkles", "Confetti", "Bubbles",
                       "Embers", "Leaves", "Stars")),
        Param("count", "How many", "int", 140, 10, 600, 10),
        Param("speed", "Speed", "float", 1.0, 0.1, 4.0, 0.1, suffix="x"),
        Param("size", "Size", "float", 1.0, 0.3, 3.0, 0.1, suffix="x"),
        Param("wind", "Wind", "float", 0.15, -1.0, 1.0, 0.05),
        Param("opacity", "Opacity", "float", 0.85, 0.1, 1.0, 0.05),
    )

    def __init__(self):
        self._state = None
        self._signature = None

    def _particles(self, count, w, h, style):
        signature = (count, w, h, style)
        if self._signature == signature and self._state is not None:
            return self._state
        rng = np.random.RandomState(4)
        self._state = {
            "x": rng.rand(count).astype(np.float32) * w,
            "y": rng.rand(count).astype(np.float32) * h,
            "z": (0.35 + rng.rand(count) * 0.65).astype(np.float32),
            "phase": (rng.rand(count) * 6.283).astype(np.float32),
            "spin": (rng.rand(count) * 6.283).astype(np.float32),
            "hue": rng.randint(0, 180, count).astype(np.uint8),
        }
        self._signature = signature
        return self._state

    PALETTES = {
        "Snow": ((248, 250, 252),),
        "Rain": ((235, 220, 190),),
        "Sparkles": ((210, 240, 255), (255, 245, 210), (230, 220, 255)),
        "Confetti": ((90, 90, 240), (90, 220, 120), (240, 190, 70),
                     (230, 120, 220), (90, 220, 240)),
        "Bubbles": ((245, 230, 200),),
        "Embers": ((60, 130, 255), (40, 90, 240), (90, 190, 255)),
        "Leaves": ((40, 120, 190), (30, 90, 160), (60, 150, 210)),
        "Stars": ((180, 230, 255), (255, 250, 230)),
    }

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        style = p.get("style", "Snow")
        count = int(p["count"])
        state = self._particles(count, w, h, style)
        speed = float(p["speed"])
        size = float(p["size"]) * max(1.0, min(h, w) / 720.0)
        wind = float(p["wind"])

        fall = {"Snow": 1.4, "Rain": 11.0, "Sparkles": 0.9, "Confetti": 2.4,
                "Bubbles": -1.8, "Embers": -2.2, "Leaves": 1.8,
                "Stars": 0.5}[style]
        drift = {"Snow": 1.0, "Rain": 0.25, "Sparkles": 0.8, "Confetti": 1.4,
                 "Bubbles": 0.9, "Embers": 1.2, "Leaves": 2.0,
                 "Stars": 0.2}[style]

        state["phase"] += 0.09 * speed
        state["spin"] += 0.06 * speed
        state["y"] += fall * speed * state["z"] * max(1.0, h / 720.0)
        state["x"] += (wind * 3.0 + np.sin(state["phase"]) * drift) * state["z"]
        # Wrap round, so the fall never runs out.
        if fall >= 0:
            gone = state["y"] > h + 12
            state["y"][gone] = -12
            state["x"][gone] = np.random.rand(int(gone.sum())) * w
        else:
            gone = state["y"] < -12
            state["y"][gone] = h + 12
            state["x"][gone] = np.random.rand(int(gone.sum())) * w
        state["x"] %= w

        layer = np.zeros_like(frame)
        palette = self.PALETTES[style]
        for i in range(count):
            x, y, z = float(state["x"][i]), float(state["y"][i]), float(state["z"][i])
            colour = palette[i % len(palette)]
            radius = max(1, int(size * z * 3))
            if style == "Rain":
                length = int(size * z * 22)
                cv2.line(layer, (int(x), int(y)),
                         (int(x - wind * length * 0.5), int(y - length)),
                         colour, max(1, radius // 2), cv2.LINE_AA)
            elif style == "Sparkles" or style == "Stars":
                twinkle = 0.45 + 0.55 * abs(math.sin(float(state["phase"][i])))
                shade = tuple(int(c * twinkle) for c in colour)
                arm = radius * 2
                cv2.line(layer, (int(x - arm), int(y)), (int(x + arm), int(y)),
                         shade, 1, cv2.LINE_AA)
                cv2.line(layer, (int(x), int(y - arm)), (int(x), int(y + arm)),
                         shade, 1, cv2.LINE_AA)
                cv2.circle(layer, (int(x), int(y)), max(1, radius // 2), shade,
                           -1, cv2.LINE_AA)
            elif style == "Confetti":
                angle = float(state["spin"][i])
                box = cv2.boxPoints((((x), (y)), (radius * 3, radius * 1.4),
                                     math.degrees(angle)))
                cv2.fillConvexPoly(layer, box.astype(np.int32), colour, cv2.LINE_AA)
            elif style == "Bubbles":
                cv2.circle(layer, (int(x), int(y)), radius * 2, colour, 1,
                           cv2.LINE_AA)
                cv2.circle(layer, (int(x - radius * 0.6), int(y - radius * 0.6)),
                           max(1, radius // 2), colour, -1, cv2.LINE_AA)
            elif style == "Leaves":
                angle = float(state["spin"][i])
                cv2.ellipse(layer, (int(x), int(y)),
                            (int(radius * 2.4), int(radius * 1.1)),
                            math.degrees(angle), 0, 360, colour, -1, cv2.LINE_AA)
            else:      # Snow, Embers
                cv2.circle(layer, (int(x), int(y)), radius, colour, -1, cv2.LINE_AA)

        opacity = float(p["opacity"])
        if style in ("Embers", "Sparkles", "Stars"):
            layer = cv2.GaussianBlur(layer, (0, 0), 1.4)
            return cv2.add(frame, cv2.convertScaleAbs(layer, alpha=opacity))
        mask = cv2.cvtColor(layer, cv2.COLOR_BGR2GRAY)
        if opacity < 0.999:
            mask = cv2.convertScaleAbs(mask, alpha=opacity)
        return _blend(frame, layer, mask)


class LightLeak(Effect):
    id = "leak"
    name = "Light and flare"
    group = "Texture"
    blurb = (
        "A wash of coloured light across the picture, the way light leaking "
        "into a camera gives."
    )
    params = (
        Param("style", "Style", "choice", "Warm leak",
              choices=("Warm leak", "Cool leak", "Rainbow", "Sun flare",
                       "Corner glow", "Haze")),
        Param("strength", "Strength", "float", 0.45, 0.05, 1.0, 0.05),
        Param("position", "Position", "float", 0.15, 0.0, 1.0, 0.01),
        Param("drift", "Drift", "float", 0.0, 0.0, 1.0, 0.05,
              hint="Let it move slowly by itself."),
    )

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        style = p.get("style", "Warm leak")
        strength = float(p["strength"])
        place = float(p["position"])
        if float(p["drift"]) > 0.001:
            phase = ctx.get("frame_index", 0) * 0.006 * float(p["drift"])
            place = (place + (math.sin(phase) * 0.5 + 0.5) * 0.5) % 1.0

        # Work small; a wash has no fine detail in it.
        gw, gh = max(32, w // 8), max(32, h // 8)
        ys, xs = np.mgrid[0:gh, 0:gw].astype(np.float32)
        nx, ny = xs / gw, ys / gh
        layer = np.zeros((gh, gw, 3), np.float32)

        if style in ("Warm leak", "Cool leak", "Rainbow"):
            band = np.clip(1.0 - abs(nx - place) * 3.2, 0.0, 1.0) ** 2
            if style == "Warm leak":
                tint = np.array([40, 120, 255], np.float32)
            elif style == "Cool leak":
                tint = np.array([255, 150, 70], np.float32)
            else:
                hue = ((nx * 140 + place * 180) % 180).astype(np.uint8)
                hsv = np.dstack([hue, np.full_like(hue, 210),
                                 np.full_like(hue, 255)])
                tint = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).astype(np.float32)
            layer = band[:, :, None] * (tint if tint.ndim == 1 else tint)
        elif style == "Sun flare":
            sx, sy = place, 0.25
            dist = np.sqrt((nx - sx) ** 2 + ((ny - sy) * (gh / gw)) ** 2)
            core = np.clip(1.0 - dist * 4.0, 0.0, 1.0) ** 3
            halo = np.clip(1.0 - dist * 1.4, 0.0, 1.0) ** 2 * 0.45
            streak = np.clip(1.0 - abs(ny - sy) * 14.0, 0.0, 1.0) * \
                np.clip(1.0 - abs(nx - sx) * 1.6, 0.0, 1.0) * 0.5
            glow = core + halo + streak
            layer = glow[:, :, None] * np.array([190, 225, 255], np.float32)
        elif style == "Corner glow":
            corner = np.clip(1.0 - np.sqrt((nx - place) ** 2 + ny ** 2) * 1.6,
                             0.0, 1.0) ** 2
            layer = corner[:, :, None] * np.array([120, 190, 255], np.float32)
        else:    # Haze
            layer = np.full((gh, gw, 3), 1.0, np.float32) * \
                np.array([210, 215, 225], np.float32) * 0.6

        layer = cv2.GaussianBlur(layer, (0, 0), gw * 0.05)
        small_light = np.clip(layer * strength, 0, 255).astype(np.uint8)
        light = cv2.resize(small_light, (w, h), interpolation=cv2.INTER_LINEAR)
        return _screen(frame, light)


class MotionTrails(Effect):
    id = "trails"
    name = "Motion trails"
    group = "Texture"
    blurb = "Leaves a fading copy of what moved, like a long exposure."
    params = (
        Param("style", "Style", "choice", "Light trails",
              choices=("Light trails", "Echo", "Ghost", "Freeze the background")),
        Param("length", "Length", "float", 0.75, 0.1, 0.98, 0.02),
        Param("strength", "Strength", "float", 0.8, 0.1, 1.0, 0.05),
    )

    def __init__(self):
        self._previous = None

    def apply(self, frame, p, ctx):
        style = p.get("style", "Light trails")
        decay = float(p["length"])
        strength = float(p["strength"])

        if self._previous is None or self._previous.shape != frame.shape:
            self._previous = frame.copy()
            return frame

        previous = self._previous
        if style == "Light trails":
            out = cv2.max(frame, cv2.convertScaleAbs(previous, alpha=decay))
        elif style == "Echo":
            out = cv2.addWeighted(frame, 1 - decay, previous, decay, 0)
        elif style == "Ghost":
            difference = cv2.cvtColor(cv2.absdiff(frame, previous),
                                      cv2.COLOR_BGR2GRAY)
            moving = cv2.convertScaleAbs(difference, alpha=255.0 / 30.0 * decay)
            out = _blend(frame, previous, moving)
        else:    # Freeze the background
            difference = cv2.cvtColor(cv2.absdiff(frame, previous),
                                      cv2.COLOR_BGR2GRAY)
            moving = cv2.convertScaleAbs(difference, alpha=255.0 / 12.0)
            still = cv2.convertScaleAbs(cv2.bitwise_not(moving),
                                        alpha=decay * 0.6)
            out = _blend(frame, previous, still)

        self._previous = out
        if strength >= 0.999:
            return out
        return cv2.addWeighted(frame, 1 - strength, out, strength, 0)


class AsciiArt(Effect):
    id = "ascii"
    name = "Text picture"
    group = "Texture"
    blurb = "Rebuilds the picture out of typed characters."
    RAMPS = {
        "Classic": "@%#*+=-:. ",
        "Blocks": "█▓▒░ ",
        "Dense": "$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/|()1{}[]?-_+~<>i!lI;:,\"^`'. ",
        "Dots": "█•· ",
        "Binary": "10 ",
    }
    params = (
        Param("ramp", "Characters", "choice", "Classic", choices=tuple(RAMPS)),
        Param("cell", "Character size", "int", 10, 4, 28, 1, suffix=" px"),
        Param("colour", "Keep the colours", "bool", True),
        Param("ink", "Ink", "color", "#8CF0A8",
              hint="Used when the colours are turned off."),
        Param("background", "Background", "color", "#06080B"),
        Param("bold", "Bold", "bool", False),
    )

    def __init__(self):
        self._tiles = None
        self._signature = None

    def _glyphs(self, ramp, cell, bold):
        signature = (ramp, cell, bold)
        if self._signature == signature and self._tiles is not None:
            return self._tiles
        characters = self.RAMPS[ramp]
        tiles = []
        font = cv2.FONT_HERSHEY_SIMPLEX
        thickness = 2 if bold else 1
        scale = cell / 22.0
        for character in characters:
            tile = np.zeros((cell, cell), np.uint8)
            if character.strip():
                try:
                    cv2.putText(tile, character, (0, cell - max(1, cell // 5)),
                                font, scale, 255, thickness, cv2.LINE_AA)
                except Exception:
                    tile[:] = 0
            tiles.append(tile)
        self._tiles = np.array(tiles, dtype=np.uint8)
        self._signature = signature
        return self._tiles

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        cell = max(4, int(p["cell"]))
        cols, rows = max(1, w // cell), max(1, h // cell)
        tiles = self._glyphs(p.get("ramp", "Classic"), cell, bool(p.get("bold")))
        steps = len(tiles)

        small = cv2.resize(frame, (cols, rows), interpolation=cv2.INTER_AREA)
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        # Brightest characters come last in every ramp, so map the other way.
        index = ((255 - grey).astype(np.int32) * (steps - 1) // 255)

        # Lay the glyph tiles out as one big single-channel picture.
        stamped = tiles[index]                       # rows, cols, cell, cell
        stamped = stamped.transpose(0, 2, 1, 3).reshape(rows * cell, cols * cell)

        canvas = np.empty((rows * cell, cols * cell, 3), np.uint8)
        canvas[:] = _hex_to_bgr(p.get("background", "#06080B"))

        if p.get("colour", True):
            ink = cv2.resize(small, (cols * cell, rows * cell),
                             interpolation=cv2.INTER_NEAREST)
        else:
            ink = _flat(canvas, _hex_to_bgr(p.get("ink", "#8CF0A8")))

        out = _blend(canvas, ink, stamped)
        if out.shape[:2] != (h, w):
            out = cv2.resize(out, (w, h), interpolation=cv2.INTER_NEAREST)
        return out


class Border(Effect):
    id = "border"
    name = "Frame and corners"
    group = "Output"
    blurb = "Rounds the corners or puts a frame around the picture."
    params = (
        Param("style", "Style", "choice", "Rounded corners",
              choices=("Rounded corners", "Solid frame", "Double frame",
                       "Polaroid", "Circle", "Soft edge")),
        Param("thickness", "Thickness", "float", 0.03, 0.0, 0.25, 0.005),
        Param("radius", "Corner radius", "float", 0.06, 0.0, 0.5, 0.01),
        Param("colour", "Colour", "color", "#12161C"),
        Param("shadow", "Inner shadow", "float", 0.0, 0.0, 1.0, 0.05),
    )

    def apply(self, frame, p, ctx):
        h, w = frame.shape[:2]
        style = p.get("style", "Rounded corners")
        colour = _hex_to_bgr(p.get("colour", "#12161C"))
        edge = int(min(h, w) * float(p["thickness"]))
        radius = int(min(h, w) * float(p["radius"]))
        out = frame.copy()

        if style in ("Solid frame", "Double frame", "Polaroid"):
            if style == "Polaroid":
                top = edge
                bottom = int(edge * 3.2)
                out = cv2.copyMakeBorder(frame, top, bottom, edge, edge,
                                         cv2.BORDER_CONSTANT, value=colour)
                out = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
            else:
                cv2.rectangle(out, (0, 0), (w - 1, h - 1), colour, edge * 2)
                if style == "Double frame":
                    inset = int(edge * 1.8)
                    cv2.rectangle(out, (inset, inset), (w - inset, h - inset),
                                  colour, max(1, edge // 2))

        if style in ("Rounded corners", "Circle", "Soft edge"):
            mask = np.zeros((h, w), np.uint8)
            if style == "Circle":
                cv2.ellipse(mask, (w // 2, h // 2),
                            (int(w * 0.48), int(h * 0.48)), 0, 0, 360, 255, -1,
                            cv2.LINE_AA)
            elif style == "Soft edge":
                cv2.rectangle(mask, (edge, edge), (w - edge, h - edge), 255, -1)
                mask = cv2.GaussianBlur(mask, (0, 0),
                                        max(2.0, min(h, w) * 0.03))
            else:
                r = max(1, radius)
                cv2.rectangle(mask, (r, 0), (w - r, h), 255, -1)
                cv2.rectangle(mask, (0, r), (w, h - r), 255, -1)
                for centre in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
                    cv2.circle(mask, centre, r, 255, -1, cv2.LINE_AA)
            out = _blend(_flat(frame, colour), out, mask)

        shadow = float(p["shadow"])
        if shadow > 0.001:
            inner = np.zeros((h, w), np.uint8)
            pad = max(2, int(min(h, w) * 0.04))
            cv2.rectangle(inner, (pad, pad), (w - pad, h - pad), 255, -1)
            inner = cv2.GaussianBlur(inner, (0, 0), min(h, w) * 0.05)
            darkened = cv2.convertScaleAbs(out, alpha=1.0 - shadow)
            out = _blend(darkened, out, inner)
        return out


# --------------------------------------------------------------------------
# Registry and pipeline
# --------------------------------------------------------------------------

EFFECTS: List[Effect] = [
    Framing(),
    Crop(),
    ZoomPan(),
    ChromaKey(),
    PortraitBackground(),
    FacePrivacy(),
    CoverRegion(),
    FaceWarp(),
    Exposure(),
    ColourBalance(),
    SkinSmooth(),
    ColourGrade(),
    Look(),
    Duotone(),
    Detail(),
    Bloom(),
    LightLeak(),
    MotionTrails(),
    TiltShift(),
    Stylise(),
    Halftone(),
    Glitch(),
    Terminal(),
    Kaleidoscope(),
    AsciiArt(),
    Vignette(),
    Retro(),
    TextOverlay(),
    MediaOverlay(),
    Weather(),
    OutputSize(),
    Border(),
]

EFFECTS_BY_ID = {e.id: e for e in EFFECTS}

GROUP_ORDER = ["Camera", "Background", "Face", "Colour", "Texture", "Overlay", "Output"]


class Pipeline:
    """Holds which effects are on and their settings, and runs them on a frame.

    Settings are read from the interface thread and written from the capture
    thread, so everything goes through one lock.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.state: Dict[str, Dict[str, Any]] = {
            e.id: {"enabled": False, "params": e.defaults()} for e in EFFECTS
        }
        self.state["framing"]["enabled"] = True

    # -- settings ----------------------------------------------------------

    def set_enabled(self, effect_id: str, enabled: bool) -> None:
        with self._lock:
            self.state[effect_id]["enabled"] = bool(enabled)

    def is_enabled(self, effect_id: str) -> bool:
        with self._lock:
            return self.state[effect_id]["enabled"]

    def set_param(self, effect_id: str, key: str, value: Any) -> None:
        with self._lock:
            self.state[effect_id]["params"][key] = value

    def get_params(self, effect_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state[effect_id]["params"])

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                eid: {"enabled": s["enabled"], "params": dict(s["params"])}
                for eid, s in self.state.items()
            }

    def restore(self, data: Dict[str, Any]) -> None:
        with self._lock:
            for eid, saved in (data or {}).items():
                effect = EFFECTS_BY_ID.get(eid)
                if not effect:
                    continue
                params = effect.defaults()
                params.update(saved.get("params", {}))
                self.state[eid] = {"enabled": bool(saved.get("enabled")), "params": params}

    def reset(self) -> None:
        with self._lock:
            for effect in EFFECTS:
                self.state[effect.id] = {"enabled": False, "params": effect.defaults()}
            self.state["framing"]["enabled"] = True

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self.state.values() if s["enabled"])

    # -- processing --------------------------------------------------------

    def process(self, frame: np.ndarray, ctx: Dict[str, Any]) -> np.ndarray:
        with self._lock:
            plan = [
                (e, dict(self.state[e.id]["params"]))
                for e in EFFECTS
                if self.state[e.id]["enabled"]
            ]
        for effect, params in plan:
            usable, _ = effect.availability()
            if not usable:
                continue
            try:
                result = effect.apply(frame, params, ctx)
                if result is not None:
                    frame = result
            except Exception as exc:  # one bad effect must not kill the stream
                ctx.setdefault("errors", []).append(f"{effect.name}: {exc}")
        return frame


def test_pattern(width: int = 1280, height: int = 720, t: float = 0.0) -> np.ndarray:
    """A moving pattern so the app is usable without a camera plugged in."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    bars = [
        (255, 255, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0),
        (255, 0, 255), (0, 0, 255), (255, 0, 0), (32, 32, 32),
    ]
    bar_w = width // len(bars)
    for i, colour in enumerate(bars):
        frame[: int(height * 0.7), i * bar_w : (i + 1) * bar_w] = colour[::-1]
    y = int(height * 0.7)
    frame[y:, :] = (24, 26, 32)
    x = int((np.sin(t) * 0.5 + 0.5) * (width - 120)) + 10
    cv2.circle(frame, (x, y + (height - y) // 2), 28, (61, 163, 232), -1)
    cv2.putText(
        frame, "Camaloop test pattern", (24, y + 36),
        cv2.FONT_HERSHEY_DUPLEX, 0.8, (220, 226, 234), 1, cv2.LINE_AA,
    )
    return frame
