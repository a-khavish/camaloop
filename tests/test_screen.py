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

"""Using the screen as a camera: session detection, geometry, decoding."""

import os
import unittest
from unittest import mock

import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camaloop.core import screen
from camaloop.core.screen import ScreenGrabber, ScreenGrabError


class Session(unittest.TestCase):
    def kind_with(self, **env):
        keys = ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY")
        clean = {k: env.get(k, "") for k in keys}
        with mock.patch.dict(os.environ, clean, clear=False):
            for key, value in clean.items():
                if not value:
                    os.environ.pop(key, None)
            return screen.session_type()

    def test_it_believes_the_session_variable(self):
        self.assertEqual(self.kind_with(XDG_SESSION_TYPE="wayland"), "wayland")
        self.assertEqual(self.kind_with(XDG_SESSION_TYPE="x11"), "x11")

    def test_it_falls_back_to_the_display_variables(self):
        self.assertEqual(self.kind_with(WAYLAND_DISPLAY="wayland-0"), "wayland")
        self.assertEqual(self.kind_with(DISPLAY=":0"), "x11")

    def test_it_admits_when_it_cannot_tell(self):
        self.assertEqual(self.kind_with(), "")

    def test_the_desktop_is_named(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME:ubuntu"}):
            self.assertEqual(screen.desktop_name(), "gnome")
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "sway"}):
            self.assertTrue(screen._wlroots_like())
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE"}):
            self.assertFalse(screen._wlroots_like())


class WhatIsAvailable(unittest.TestCase):
    def available(self, session, tools, portal=False):
        which = lambda name: "/usr/bin/" + name if name in tools else None  # noqa: E731
        with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": session,
                                          "DISPLAY": ":0"}), \
             mock.patch.object(screen.shutil, "which", side_effect=which), \
             mock.patch.object(screen, "portal_available", return_value=portal):
            return screen.backends(), screen.describe_support()

    def test_x11_with_ffmpeg(self):
        found, text = self.available("x11", {"ffmpeg"})
        self.assertEqual(found, ["x11"])
        self.assertIn("X11", text)

    def test_x11_without_ffmpeg(self):
        found, text = self.available("x11", set())
        self.assertEqual(found, [])
        self.assertIn("ffmpeg", text)

    def test_wayland_with_a_portal(self):
        found, text = self.available("wayland", {"ffmpeg"}, portal=True)
        self.assertEqual(found[0], "portal")
        self.assertIn("portal", text.lower())

    def test_wayland_with_only_grim(self):
        found, text = self.available("wayland", {"grim"})
        self.assertEqual(found, ["grim"])
        self.assertIn("grim", text)

    def test_wayland_with_nothing(self):
        found, text = self.available("wayland", set())
        self.assertEqual(found, [])
        self.assertIn("xdg-desktop-portal", text)

    def test_wayland_falls_back_to_xwayland(self):
        found, text = self.available("wayland", {"ffmpeg"})
        self.assertEqual(found, ["x11"])
        self.assertIn("XWayland", text)

    def test_starting_with_no_backend_says_why(self):
        with mock.patch.object(screen, "backends", return_value=[]), \
             mock.patch.object(screen, "describe_support",
                               return_value="nothing doing"):
            with self.assertRaises(ScreenGrabError) as caught:
                ScreenGrabber()
            self.assertIn("nothing doing", str(caught.exception))


class Geometry(unittest.TestCase):
    def test_a_rectangle(self):
        self.assertEqual(ScreenGrabber._parse("1280x720+100+50"),
                         (1280, 720, 100, 50))

    def test_a_size_with_no_offset(self):
        self.assertEqual(ScreenGrabber._parse("800x600"), (800, 600, 0, 0))

    def test_the_whole_screen(self):
        for text in ("", "full", "screen", "Whole screen"):
            width, height, x, y = ScreenGrabber._parse(text)
            self.assertGreater(width, 0)
            self.assertGreater(height, 0)
            self.assertEqual((x, y), (0, 0))

    def test_nonsense_is_explained(self):
        with self.assertRaises(ScreenGrabError) as caught:
            ScreenGrabber._parse("as big as possible")
        self.assertIn("1280x720+100+50", str(caught.exception))

    def test_a_screen_size_always_comes_back(self):
        width, height = ScreenGrabber.screen_size()
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)


class PpmDecoding(unittest.TestCase):
    """grim hands back PPM, which has to be read by hand."""

    def frame(self, width=5, height=7):
        rng = np.random.RandomState(1)
        return rng.randint(0, 256, (height, width, 3), dtype=np.uint8)

    def test_a_plain_header(self):
        picture = self.frame()
        data = b"P6\n5 7\n255\n" + picture[:, :, ::-1].tobytes()
        np.testing.assert_array_equal(ScreenGrabber._read_ppm(data), picture)

    def test_a_header_with_comments(self):
        picture = self.frame()
        data = b"P6\n# made by grim\n5 7\n# another\n255\n" \
            + picture[:, :, ::-1].tobytes()
        np.testing.assert_array_equal(ScreenGrabber._read_ppm(data), picture)

    def test_a_header_on_one_line(self):
        picture = self.frame()
        data = b"P6 5 7 255\n" + picture[:, :, ::-1].tobytes()
        np.testing.assert_array_equal(ScreenGrabber._read_ppm(data), picture)

    def test_the_wrong_format_is_refused(self):
        self.assertIsNone(ScreenGrabber._read_ppm(b"P3\n5 7\n255\n"))
        self.assertIsNone(ScreenGrabber._read_ppm(b""))

    def test_a_truncated_picture_is_refused(self):
        self.assertIsNone(ScreenGrabber._read_ppm(b"P6\n5 7\n255\nshort"))


class ShortReads(unittest.TestCase):
    """An unbuffered pipe returns whatever one read gave, not a whole frame."""

    class Dribble:
        def __init__(self, data, chunk):
            self.data, self.chunk, self.at = data, chunk, 0

        def read(self, size):
            piece = self.data[self.at:self.at + min(size, self.chunk)]
            self.at += len(piece)
            return piece

    def test_a_frame_is_assembled_from_pieces(self):
        picture = np.random.RandomState(2).randint(
            0, 256, (12, 20, 3), dtype=np.uint8)
        process = mock.Mock()
        process.stdout = self.Dribble(picture.tobytes(), 37)
        reader = screen._PipeReader(process, 20, 12)
        np.testing.assert_array_equal(reader.read(), picture)

    def test_a_closed_stream_gives_nothing(self):
        process = mock.Mock()
        process.stdout = self.Dribble(b"", 8)
        self.assertIsNone(screen._PipeReader(process, 20, 12).read())

    def test_a_half_sent_frame_is_not_passed_on(self):
        picture = np.zeros((12, 20, 3), np.uint8)
        process = mock.Mock()
        process.stdout = self.Dribble(picture.tobytes()[:100], 37)
        self.assertIsNone(screen._PipeReader(process, 20, 12).read())


if __name__ == "__main__":
    unittest.main()
