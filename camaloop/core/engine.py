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

"""The capture thread: read a frame, run the pipeline, show it, send it out."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from .effects import Pipeline, test_pattern
from .v4l2 import V4L2Output, V4L2OutputError


@dataclass
class SourceSettings:
    kind: str = "camera"  # camera | file | image | pattern | network | screen
    device: str = ""  # /dev/videoN, or a path for file/image
    width: int = 1280
    height: int = 720
    fps: int = 30
    use_mjpeg: bool = True


class Engine(QThread):
    frame_ready = pyqtSignal(object)  # RGB ndarray for the preview
    raw_ready = pyqtSignal(object)  # untouched RGB ndarray, for the before/after view
    stats_ready = pyqtSignal(dict)
    failed = pyqtSignal(str)
    notice = pyqtSignal(str)
    output_changed = pyqtSignal(bool, str)
    recording_changed = pyqtSignal(bool, str)   # on/off, file path
    recording_tick = pyqtSignal(float, int)     # seconds elapsed, frames written
    snapshot_saved = pyqtSignal(str)

    def __init__(self, pipeline: Pipeline, parent=None):
        super().__init__(parent)
        self.pipeline = pipeline
        self._lock = threading.RLock()
        self._source = SourceSettings()
        self._running = False
        self._want_output = False
        self._output_path = ""
        self._output: Optional[V4L2Output] = None
        self._send_raw = False
        self._paused = False
        self._frozen_frame: Optional[np.ndarray] = None

        # recording and stills
        self._recorder: Optional[cv2.VideoWriter] = None
        self._record_path = ""
        self._record_request: Optional[str] = None   # path to start at
        self._stop_record = False
        self._record_started = 0.0
        self._record_frames = 0
        self._record_size = (0, 0)
        self._snapshot_request: Optional[str] = None

    # -- control ----------------------------------------------------------

    def configure(self, source: SourceSettings) -> None:
        with self._lock:
            self._source = source

    def source(self) -> SourceSettings:
        with self._lock:
            return self._source

    def set_output(self, path: str, enabled: bool) -> None:
        with self._lock:
            self._output_path = path
            self._want_output = enabled

    def set_preview_raw(self, enabled: bool) -> None:
        with self._lock:
            self._send_raw = enabled

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused

    def start_recording(self, path: str) -> None:
        """Ask the capture thread to begin writing frames to this file."""
        with self._lock:
            self._record_request = path
            self._stop_record = False

    def stop_recording(self) -> None:
        with self._lock:
            self._stop_record = True
            self._record_request = None

    def is_recording(self) -> bool:
        return self._recorder is not None

    def take_snapshot(self, path: str) -> None:
        """Save the next processed frame as a still."""
        with self._lock:
            self._snapshot_request = path

    def stop(self) -> None:
        self._running = False

    # -- worker -----------------------------------------------------------

    def run(self) -> None:  # noqa: C901 - a capture loop is linear by nature
        self._running = True
        source = self.source()
        capture = None

        if source.kind in ("camera", "file", "network"):
            capture = self._open_capture(source)
            if capture is None:
                self._running = False
                return
        grabber = None
        if source.kind == "screen":
            from .screen import ScreenGrabber, ScreenGrabError

            # "window" and "screen" ask the desktop to pick; anything else is
            # a rectangle in the form WIDTHxHEIGHT+X+Y.
            wanted = (source.device or "").strip().lower()
            picker = wanted if wanted in ("window", "monitor", "both") else "monitor"
            geometry = "" if wanted in ("", "window", "monitor", "both", "screen") \
                else source.device
            try:
                grabber = ScreenGrabber(geometry, source.fps, sources=picker)
            except ScreenGrabError as exc:
                self.failed.emit(str(exc))
                self._running = False
                return
            how = {"x11": "X11", "portal": "the desktop portal",
                   "grim": "grim"}.get(grabber.backend, grabber.backend)
            self.notice.emit(
                f"Grabbing {grabber.width}x{grabber.height} of the screen "
                f"through {how}."
            )

        still: Optional[np.ndarray] = None
        if source.kind == "image":
            still = cv2.imread(source.device, cv2.IMREAD_COLOR)
            if still is None:
                self.failed.emit(f"Could not read the image at {source.device}.")
                self._running = False
                return

        frame_index = 0
        started = time.perf_counter()
        last_stats = started
        last_preview = 0.0
        fps_window = []
        current_fps = 0.0
        # A video file has a frame rate of its own; playing it at the rate
        # chosen for cameras would run it fast or slow.
        playback_fps = source.fps
        if source.kind == "file" and capture is not None:
            reported = capture.get(cv2.CAP_PROP_FPS)
            if reported and 1 <= reported <= 240:
                playback_fps = reported
        target_delta = 1.0 / max(1, playback_fps)

        while self._running:
            loop_start = time.perf_counter()

            with self._lock:
                paused = self._paused

            if paused and self._frozen_frame is not None:
                frame = self._frozen_frame.copy()
            elif source.kind == "pattern":
                frame = test_pattern(source.width, source.height, loop_start - started)
            elif grabber is not None:
                frame = grabber.read()
                if frame is None:
                    detail = grabber.error_text()
                    self.failed.emit(
                        "The screen grab stopped." + (f" {detail}" if detail else "")
                    )
                    break
            elif still is not None:
                frame = still.copy()
            else:
                ok, frame = capture.read()
                if not ok or frame is None:
                    if source.kind == "file":
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    self.failed.emit(
                        "The camera stopped sending frames. It may have been "
                        "unplugged or claimed by another program."
                    )
                    break
                self._frozen_frame = frame

            if self._send_raw:
                self.raw_ready.emit(self._to_rgb(frame))

            ctx = {"frame_index": frame_index, "fps": current_fps, "t": loop_start - started}
            processed = self.pipeline.process(frame, ctx)
            for message in ctx.get("errors", []):
                self.notice.emit(message)

            self._pump_output(processed)
            self._pump_recording(processed, playback_fps)
            self._pump_snapshot(processed)

            now = time.perf_counter()
            if now - last_preview >= 1 / 32.0:
                self.frame_ready.emit(self._to_rgb(processed))
                last_preview = now

            fps_window.append(now)
            fps_window = [t for t in fps_window if now - t < 1.0]
            current_fps = len(fps_window)

            if now - last_stats >= 0.5:
                h, w = processed.shape[:2]
                self.stats_ready.emit(
                    {
                        "fps": current_fps,
                        "width": w,
                        "height": h,
                        "effects": self.pipeline.active_count(),
                        "streaming": self._output is not None,
                        "output": self._output.path if self._output else "",
                    }
                )
                last_stats = now

            frame_index += 1
            # A camera paces itself: read() blocks until the next frame is
            # ready. Everything else would spin as fast as the processor
            # allows, so it is paced here instead.
            if source.kind in ("pattern", "image", "file"):   # not camera/network
                time.sleep(max(0.0, target_delta - (time.perf_counter() - loop_start)))

        if capture is not None:
            capture.release()
        if grabber is not None:
            grabber.close()
        self._close_recorder()
        self._close_output()
        self._running = False
        self.stats_ready.emit({"fps": 0, "width": 0, "height": 0, "streaming": False})

    # -- helpers ----------------------------------------------------------

    def _open_capture(self, source: SourceSettings):
        target = source.device
        if source.kind == "network":
            # A stream can take a moment to answer; do not wait for ever.
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS",
                                  "rtsp_transport;tcp|stimeout;5000000")
        if source.kind == "camera" and not os.path.exists(target):
            self.failed.emit(f"{target} is not there any more. Refresh the camera list.")
            return None

        if source.kind == "camera":
            backend = cv2.CAP_V4L2
        elif source.kind == "network":
            backend = cv2.CAP_FFMPEG      # RTSP, HTTP, MJPEG and the like
        else:
            backend = cv2.CAP_ANY
        capture = cv2.VideoCapture(target, backend)
        if not capture.isOpened():
            if source.kind == "network":
                self.failed.emit(
                    f"Could not open the stream at {target}. Check the address, "
                    "that the camera is reachable on the network, and that any "
                    "user name and password are included in the address."
                )
            else:
                self.failed.emit(
                    f"Could not open {target}. Another program may be using it, or "
                    "your user may not be in the 'video' group."
                )
            return None

        if source.kind == "camera":
            if source.use_mjpeg:
                capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, source.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, source.height)
            capture.set(cv2.CAP_PROP_FPS, source.fps)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            got_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            got_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if (got_w, got_h) != (source.width, source.height) and got_w and got_h:
                self.notice.emit(
                    f"{target} does not do {source.width}x{source.height}; "
                    f"it is sending {got_w}x{got_h} instead."
                )
        return capture

    def _pump_output(self, frame: np.ndarray) -> None:
        with self._lock:
            want = self._want_output
            path = self._output_path

        if not want:
            if self._output is not None:
                self._close_output()
            return

        h, w = frame.shape[:2]
        if self._output is not None and (
            self._output.path != path
            or self._output.width != w
            or self._output.height != h
        ):
            self._close_output()

        if self._output is None:
            if not path:
                return
            try:
                self._output = V4L2Output(path, w, h)
                self.output_changed.emit(True, path)
                self.notice.emit(f"Streaming {w}x{h} to {path}.")
            except V4L2OutputError as exc:
                with self._lock:
                    self._want_output = False
                self.output_changed.emit(False, path)
                self.failed.emit(str(exc))
                return

        try:
            self._output.send(self._to_rgb(frame))
        except (V4L2OutputError, OSError) as exc:
            self._close_output()
            with self._lock:
                self._want_output = False
            self.output_changed.emit(False, path)
            self.failed.emit(f"Stopped streaming: {exc}")

    # -- recording and stills ---------------------------------------------

    def _pump_recording(self, frame: np.ndarray, fps: float) -> None:
        with self._lock:
            wanted = self._record_request
            stopping = self._stop_record

        if stopping and self._recorder is not None:
            self._close_recorder()
            return

        h, w = frame.shape[:2]
        if self._recorder is not None and (w, h) != self._record_size:
            # The output size changed mid-take; a file cannot change size, so
            # the frame is fitted into the size the recording started with.
            frame = self._fit(frame, self._record_size)

        if wanted and self._recorder is None:
            rate = fps if 1 <= fps <= 120 else 30.0
            for fourcc in ("mp4v", "MJPG", "XVID"):
                writer = cv2.VideoWriter(
                    wanted, cv2.VideoWriter_fourcc(*fourcc), rate, (w, h)
                )
                if writer.isOpened():
                    self._recorder = writer
                    break
                writer.release()
            if self._recorder is None:
                with self._lock:
                    self._record_request = None
                self.failed.emit(
                    f"Could not start recording to {wanted}. The folder may not be "
                    "writable, or this build of OpenCV has no video encoder."
                )
                self.recording_changed.emit(False, wanted)
                return
            self._record_path = wanted
            self._record_size = (w, h)
            self._record_started = time.perf_counter()
            self._record_frames = 0
            self.recording_changed.emit(True, wanted)
            self.notice.emit(f"Recording {w}x{h} to {os.path.basename(wanted)}.")

        if self._recorder is not None:
            self._recorder.write(frame)
            self._record_frames += 1
            elapsed = time.perf_counter() - self._record_started
            if self._record_frames % 5 == 0:
                self.recording_tick.emit(elapsed, self._record_frames)

    def _close_recorder(self) -> None:
        if self._recorder is None:
            return
        path, frames = self._record_path, self._record_frames
        self._recorder.release()
        self._recorder = None
        with self._lock:
            self._record_request = None
            self._stop_record = False
        self.recording_changed.emit(False, path)
        self.notice.emit(f"Saved {frames} frames to {path}")

    def _pump_snapshot(self, frame: np.ndarray) -> None:
        with self._lock:
            path = self._snapshot_request
            self._snapshot_request = None
        if not path:
            return
        try:
            if cv2.imwrite(path, frame):
                self.snapshot_saved.emit(path)
            else:
                self.failed.emit(f"Could not write the picture to {path}.")
        except Exception as exc:
            self.failed.emit(f"Could not save the picture: {exc}")

    @staticmethod
    def _fit(frame: np.ndarray, size) -> np.ndarray:
        tw, th = size
        h, w = frame.shape[:2]
        scale = min(tw / w, th / h)
        rw, rh = max(1, int(w * scale)), max(1, int(h * scale))
        resized = cv2.resize(frame, (rw, rh), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        x, y = (tw - rw) // 2, (th - rh) // 2
        canvas[y:y + rh, x:x + rw] = resized
        return canvas

    def _close_output(self) -> None:
        if self._output is not None:
            path = self._output.path
            self._output.close()
            self._output = None
            self.output_changed.emit(False, path)

    @staticmethod
    def _to_rgb(frame: np.ndarray) -> np.ndarray:
        return np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
