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

"""The parts of the V4L2 layer that can be checked without a camera."""

import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camaloop.core import v4l2


class IoctlNumbers(unittest.TestCase):
    """These are compared against the numbers the kernel headers produce."""

    def test_querycap(self):
        self.assertEqual(v4l2.VIDIOC_QUERYCAP & 0xFFFFFFFF, 0x80685600)

    def test_set_format(self):
        self.assertEqual(v4l2.VIDIOC_S_FMT & 0xFFFFFFFF, 0xC0D05605)

    def test_get_format(self):
        self.assertEqual(v4l2.VIDIOC_G_FMT & 0xFFFFFFFF, 0xC0D05604)

    def test_fourcc(self):
        self.assertEqual(v4l2.fourcc("RGB3"), v4l2.V4L2_PIX_FMT_RGB24)
        self.assertEqual(v4l2.V4L2_PIX_FMT_YUYV, 0x56595559)


class DriverNames(unittest.TestCase):
    """The loopback driver reports itself under several spellings."""

    def test_squash(self):
        for name in ("v4l2loopback", "v4l2 loopback", "V4L2 Loopback",
                     "V4L2-Loopback"):
            self.assertEqual(v4l2._squash(name), "v4l2loopback", name)

    def _device(self, **kwargs):
        base = dict(path="/dev/video9", index=9, driver="", capabilities=0,
                    virtual=False)
        base.update(kwargs)
        return v4l2.VideoDevice(**base)

    def test_loopback_recognised_however_it_is_spelled(self):
        for name in ("v4l2loopback", "v4l2 loopback", "V4L2 Loopback"):
            self.assertTrue(self._device(driver=name, virtual=True).is_loopback, name)

    def test_loopback_recognised_when_it_cannot_be_opened(self):
        # exclusive_caps makes the node refuse a second open, so the driver
        # name is unknown; sysfs still says it is a virtual device.
        self.assertTrue(self._device(driver="", virtual=True).is_loopback)

    def test_real_camera_is_not_a_loopback(self):
        device = self._device(driver="uvcvideo",
                              capabilities=v4l2.V4L2_CAP_VIDEO_CAPTURE)
        self.assertFalse(device.is_loopback)
        self.assertTrue(device.is_capture)

    def test_other_virtual_drivers_are_not_loopbacks(self):
        device = self._device(driver="vivid", virtual=True,
                              capabilities=v4l2.V4L2_CAP_VIDEO_CAPTURE)
        self.assertFalse(device.is_loopback)

    def test_a_loopback_can_always_be_captured_from(self):
        # Announced as output only, until something streams to it.
        device = self._device(driver="v4l2 loopback",
                              capabilities=v4l2.V4L2_CAP_VIDEO_OUTPUT)
        self.assertTrue(device.is_capture)
        self.assertEqual(device.kind, "Virtual")

    def test_label_mentions_the_node(self):
        device = self._device(card="Camaloop")
        self.assertIn("/dev/video9", device.label)
        self.assertIn("Camaloop", device.label)


class Enumeration(unittest.TestCase):
    def test_listing_never_raises(self):
        for listing in (v4l2.list_devices, v4l2.list_capture_devices,
                        v4l2.list_loopback_devices):
            self.assertIsInstance(listing(), list)

    def test_querying_a_missing_node_still_describes_it(self):
        device = v4l2.query_capability("/dev/video999")
        self.assertIsNotNone(device)
        self.assertEqual(device.index, 999)
        self.assertFalse(device.accessible)
        self.assertTrue(device.error)


if __name__ == "__main__":
    unittest.main()
