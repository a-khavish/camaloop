#!/usr/bin/env python3
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

"""Check this machine end to end.

First a report on what is available here - the session, screen capture,
face tracking, the optional libraries - and then the part that matters:
frames are written into a virtual camera, that same camera is opened the way
any other application would open it, and the frames are checked on the way
back out.

    python3 tools/verify_pipeline.py [/dev/videoN]
    python3 tools/verify_pipeline.py --report      just the report

Exit codes: 0 it works, 1 it does not, 2 nothing to test against.
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIDTH, HEIGHT = 640, 480
GREEN = "\033[32m"
RED = "\033[31m"
AMBER = "\033[33m"
OFF = "\033[0m"


def ok(message):
    print(f"{GREEN}  ok{OFF}  {message}")


def bad(message):
    print(f"{RED}  x{OFF}   {message}")


def skip(message):
    print(f"{AMBER}  -{OFF}   {message}")


def note(message):
    print(f"      {message}")


def report() -> None:
    """What this machine can and cannot do, before anything is tested."""
    import platform
    import shutil

    import camaloop
    from camaloop.core import loopback, screen

    print(f"Camaloop {camaloop.__version__}")
    print(f"  {platform.system()} {platform.release()}, "
          f"Python {platform.python_version()}")

    distribution = ""
    try:
        with open("/etc/os-release") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    distribution = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    if distribution:
        print(f"  {distribution}")

    kind = screen.session_type() or "unknown"
    desktop = screen.desktop_name() or "unknown"
    print(f"  {kind} session on {desktop}")
    print()

    print("Libraries")
    for module, what in (("PyQt5", "the interface"),
                         ("cv2", "video and effects"),
                         ("numpy", "arithmetic"),
                         ("PIL", "accented and non-Latin overlay text"),
                         ("mediapipe", "background removal, precise tracking"),
                         ("gi", "the Wayland screen cast portal")):
        try:
            __import__(module)
        except ImportError:
            (bad if module in ("PyQt5", "cv2", "numpy") else skip)(
                f"{module} is not installed - {what}")
        else:
            ok(f"{module} - {what}")
    print()

    print("Tools")
    for command, what in (("ffmpeg", "screen capture and recording"),
                          ("v4l2loopback-ctl", "adding cameras without a reload"),
                          ("pkexec", "asking for a password in a window"),
                          ("grim", "screen capture on wlroots, as a fallback"),
                          ("gst-launch-1.0", "reading a PipeWire stream")):
        if shutil.which(command):
            ok(f"{command} - {what}")
        else:
            skip(f"{command} is not installed - {what}")
    print()

    print("Screen capture")
    found = screen.backends()
    if found:
        ok("Available: " + ", ".join(found))
    else:
        bad("No way to capture the screen here")
    note(screen.describe_support())
    print()

    print("Face tracking")
    try:
        from camaloop.core import faces
        from camaloop.core.faces import FaceTracker

        found = faces.cascade_file(faces.FACE_CASCADE)
        tracker = FaceTracker()
        cascade, _eyes = tracker._cascades()
        if cascade is not None:
            ok("OpenCV face and eye detection is ready")
            where = ("shipped with the app"
                     if found.startswith(faces.BUNDLED_DATA) else found)
            note(f"detection data: {where}")
        else:
            bad("No face detection data, so nothing that needs a face works")
            for line in faces.face_detection_ready()[1].splitlines()[1:]:
                note(line)
        if tracker._mesh_tracker() is not None:
            ok("The dense face mesh is ready, so the eye line and tilt are precise")
        else:
            skip("No face mesh model, so tracking uses OpenCV only "
                 "(install mediapipe and the app downloads the model)")

    except Exception as exc:
        bad(f"Face tracking could not be set up: {exc}")
    print()

    print("Virtual cameras")
    if loopback.module_loaded():
        version = loopback.module_version() or "unknown version"
        ok(f"v4l2loopback is loaded ({version})")
        if loopback.supports_dynamic_devices():
            ok("Cameras can be added and removed without reloading the driver")
        else:
            note("This version reloads the driver on every change, so all "
                 "virtual cameras must be idle at that moment.")
        cameras = loopback.list_cameras()
        if cameras:
            for camera in cameras:
                state = "in use" if camera.in_use else "idle"
                ok(f"{camera.path}  {camera.label}  ({state})")
        else:
            skip("No virtual cameras exist yet")
    elif loopback.module_installed():
        skip("v4l2loopback is installed but not loaded "
             "(sudo modprobe v4l2loopback)")
    else:
        bad("v4l2loopback is not installed, so there can be no virtual camera")
    print()


def main() -> int:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        bad(f"Cannot import {exc.name}")
        return 1

    from camaloop.core import loopback
    from camaloop.core.v4l2 import V4L2Output, V4L2OutputError

    arguments = [a for a in sys.argv[1:] if a != "--report"]
    report()
    if "--report" in sys.argv[1:]:
        return 0

    print("The round trip")
    if arguments:
        target = arguments[0]
    else:
        cameras = [c for c in loopback.list_cameras() if not c.in_use]
        if not cameras:
            skip("No idle virtual camera, so the round trip was not tested.")
            note("Make one on the Virtual cameras tab, or run:")
            note("  sudo modprobe v4l2loopback devices=1 video_nr=10 \\")
            note("       card_label=Camaloop exclusive_caps=1")
            note("then run this again.")
            return 2
        target = cameras[0].path
    print(f"Testing the full path through {target}")

    # A frame split into three flat colours survives any pixel format the
    # driver might negotiate, so the check stays meaningful either way.
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    third = WIDTH // 3
    frame[:, :third] = (220, 30, 30)  # red    (RGB order going out)
    frame[:, third : 2 * third] = (30, 200, 30)  # green
    frame[:, 2 * third :] = (30, 30, 220)  # blue

    try:
        output = V4L2Output(target, WIDTH, HEIGHT)
    except V4L2OutputError as exc:
        bad(str(exc))
        return 1
    ok(f"Opened {target} for output at {output.width}x{output.height}")

    stop = threading.Event()

    def pump():
        while not stop.is_set():
            try:
                output.send(frame)
            except Exception:
                return
            time.sleep(1 / 30)

    writer = threading.Thread(target=pump, daemon=True)
    writer.start()
    time.sleep(0.4)  # let a few frames land before anyone looks

    result = 1
    capture = None
    try:
        capture = cv2.VideoCapture(target, cv2.CAP_V4L2)
        if not capture.isOpened():
            bad("Another program could not open the camera for reading.")
            return 1
        ok("Opened the same camera for reading, as another app would")

        deadline = time.time() + 6
        got = None
        while time.time() < deadline:
            read_ok, candidate = capture.read()
            if read_ok and candidate is not None:
                got = candidate
                break
            time.sleep(0.1)

        if got is None:
            bad("No frames came back within six seconds.")
            return 1
        ok(f"Read a frame back: {got.shape[1]}x{got.shape[0]}")

        if (got.shape[1], got.shape[0]) != (output.width, output.height):
            bad("The frame came back a different size than it went out.")
            return 1

        # OpenCV hands back BGR, so channel 2 is red and channel 0 is blue.
        left = got[:, : third // 2].reshape(-1, 3).mean(axis=0)
        middle = got[:, third + third // 4 : 2 * third - third // 4].reshape(-1, 3).mean(axis=0)
        right = got[:, 2 * third + third // 2 :].reshape(-1, 3).mean(axis=0)

        checks = (
            ("left band is red", left[2] > 120 and left[2] > left[0] + 40),
            ("middle band is green", middle[1] > 110 and middle[1] > middle[2] + 40),
            ("right band is blue", right[0] > 120 and right[0] > right[2] + 40),
        )
        for label, passed in checks:
            if passed:
                ok(label)
            else:
                bad(f"{label} - colours did not survive the round trip")
        if all(passed for _label, passed in checks):
            result = 0
    finally:
        stop.set()
        writer.join(timeout=2)
        if capture is not None:
            capture.release()
        output.close()

    if result == 0:
        print(f"\n{GREEN}The virtual camera works end to end.{OFF}")
    else:
        print(f"\n{RED}Frames went out but did not come back correctly.{OFF}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
