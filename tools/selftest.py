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

"""Run every effect over a synthetic frame without opening a window.

    python3 tools/selftest.py

Useful after changing effects.py, and as a quick check that OpenCV and NumPy
are installed correctly.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from camaloop.core import v4l2  # noqa: E402
from camaloop.core.effects import EFFECTS, Pipeline, test_pattern  # noqa: E402


def main() -> int:
    frame = test_pattern(1280, 720)
    print(f"test pattern: {frame.shape} {frame.dtype}")

    failures = []
    for effect in EFFECTS:
        usable, reason = effect.availability()
        if not usable:
            print(f"  skip  {effect.name:<38} {reason}")
            continue
        params = effect.defaults()
        # Exercise every branch of a choice parameter, not just the default.
        variants = [params]
        for param in effect.params:
            if param.kind == "choice":
                for choice in param.choices:
                    variant = dict(params)
                    variant[param.key] = choice
                    variants.append(variant)

        start = time.perf_counter()
        for variant in variants:
            try:
                out = effect.apply(frame.copy(), variant, {"frame_index": 3, "fps": 30})
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{effect.name} ({variant}): {exc}")
                continue
            if out is None or out.dtype != np.uint8 or out.ndim != 3:
                failures.append(f"{effect.name} returned {type(out)} / bad shape")
        ms = (time.perf_counter() - start) * 1000 / len(variants)
        print(f"  ok    {effect.name:<38} {len(variants)} variant(s), {ms:.1f} ms each")

    pipeline = Pipeline()
    for effect in EFFECTS:
        usable, _ = effect.availability()
        if usable:
            pipeline.set_enabled(effect.id, True)
    start = time.perf_counter()
    out = pipeline.process(frame.copy(), {"frame_index": 0, "fps": 30})
    print(
        f"\neverything at once: {out.shape} in {(time.perf_counter() - start) * 1000:.0f} ms"
    )

    saved = pipeline.snapshot()
    pipeline.reset()
    pipeline.restore(saved)
    assert pipeline.snapshot() == saved, "preset round-trip changed the settings"
    print("preset round-trip: ok")

    print(f"\nVIDIOC_QUERYCAP = 0x{v4l2.VIDIOC_QUERYCAP & 0xFFFFFFFF:08x} (expect 0x80685600)")
    print(f"VIDIOC_S_FMT    = 0x{v4l2.VIDIOC_S_FMT & 0xFFFFFFFF:08x} (expect 0xc0d05605)")
    assert v4l2.VIDIOC_QUERYCAP & 0xFFFFFFFF == 0x80685600
    assert v4l2.VIDIOC_S_FMT & 0xFFFFFFFF == 0xC0D05605

    # Device identification, with the strings real hardware reports. The
    # loopback driver calls itself "v4l2 loopback" through the ioctl and
    # "v4l2loopback" in sysfs; both must be recognised.
    print("\ndevice identification:")
    checks = [
        ("uvcvideo",      v4l2.V4L2_CAP_VIDEO_CAPTURE, False, False),
        ("v4l2 loopback", v4l2.V4L2_CAP_VIDEO_OUTPUT,  True,  True),
        ("v4l2loopback",  v4l2.V4L2_CAP_VIDEO_OUTPUT,  True,  True),
        ("V4L2 Loopback", 0,                           True,  True),
        ("",              0,                           True,  True),   # sysfs only
        ("vivid",         v4l2.V4L2_CAP_VIDEO_CAPTURE, True,  False),
    ]
    for driver, caps, virtual, expected in checks:
        dev = v4l2.VideoDevice(
            path="/dev/video9", index=9, driver=driver,
            capabilities=caps, virtual=virtual,
        )
        state = "ok  " if dev.is_loopback == expected else "FAIL"
        print(f"  {state}  driver={driver or '(none)':<16} virtual={virtual!s:<6}"
              f" -> loopback={dev.is_loopback}")
        if dev.is_loopback != expected:
            failures.append(f"driver {driver!r} identified wrongly")

    devices = v4l2.list_devices()
    print(f"\nvideo devices found: {len(devices)}")
    for dev in devices:
        print(f"  {dev.path}  {dev.card}  [{dev.driver}]  {dev.kind}")

    if failures:
        print("\nFAILURES:")
        for line in failures:
            print("  " + line)
        return 1
    print("\nAll effects ran cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
